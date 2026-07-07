#!/usr/bin/env python3
"""
services/backend_forwarder.py

[STRATEGY PATTERN]
Dua cara "forward hasil STT ke backend utama" yang sebelumnya adalah
2 fungsi lepas (register_result_to_main_backend, upload_transcript_to_main_backend)
sekarang jadi 2 class yang implement interface ResultForwarder yang sama.

Kenapa Strategy cocok di sini:
- Kedua cabang dipanggil independen (gagal salah satu tidak menggagalkan
  yang lain) — cocok dengan kontrak "beberapa algoritma yang saling
  dipertukarkan/dijalankan paralel, dengan interface hasil yang seragam".
- process_and_forward_to_new_dashboard (lihat pipeline.py) tidak perlu
  tahu detail request (JSON vs multipart) masing-masing cabang — cukup
  panggil `.forward(ctx)` dan baca `.success` / `.value`.
- Menambah cabang forward baru di masa depan (mis. backend ke-3) tinggal
  tambah 1 class baru tanpa mengubah pipeline atau class lain.

TIDAK ADA PERUBAHAN LOGIC dari versi asli — hanya dibungkus jadi class.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
import time

from services.config import (
    MAIN_BACKEND_UPLOAD_TRANSCRIPT_URL,
    DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN,
    service_auth_headers,
)
from services.http_retry import post_with_retry
from utils.vars import MAIN_BACKEND_RESULT_URL

logger = logging.getLogger(__name__)


@dataclass
class ForwardContext:
    """Semua data yang dibutuhkan salah satu/kedua strategi forward."""
    job_id:            str
    pdf_path:          str                  # path file PDF di disk
    original_filename: str
    speaker_count:     Optional[int] = None
    confidence:        Optional[float] = None
    campaign:          Optional[str] = None


@dataclass
class ForwardResult:
    success:   bool
    result_id: Optional[str] = None         # result_id yg dipakai/dihasilkan backend
    error:     Optional[str] = None


class ResultForwarder:
    """Interface strategi forward — semua strategi wajib implement forward()."""

    name: str = "base"

    def forward(self, ctx: ForwardContext) -> ForwardResult:
        raise NotImplementedError


class RegisterResultStrategy(ResultForwarder):
    """
    Cabang 1 (ringan): daftarkan hasil STT ke backend utama supaya muncul
    di ResultView, memakai result_id = job_id (bukan UUID baru dari backend).

    STT service TIDAK perlu tahu campaign/product — endpoint backend
    ('/webhook/register_stt_result') didesain khusus untuk transkrip
    plain (tanpa evaluasi LLM scorecard berbasis campaign).
    """

    name = "register_result"

    def forward(self, ctx: ForwardContext) -> ForwardResult:
        pdf_filename = Path(ctx.original_filename).stem
        payload = {
            "result_id":       ctx.job_id,        # job_id == result_id (eksplisit, dipaksa)
            "pdf_filename":    pdf_filename,       # nama file di S3 (port 8010)
            "source_filename": ctx.original_filename,
            "speaker_count":   ctx.speaker_count,
            "confidence":      ctx.confidence,
        }
        try:
            resp = post_with_retry(
                url=MAIN_BACKEND_RESULT_URL,
                payload=payload,
                max_retries=3,
                timeout=15,
            )
            if 200 <= resp.status_code < 300:
                logger.info(
                    "[JOB:%s] Registered to main backend as result_id=%s",
                    ctx.job_id, ctx.job_id,
                )
                return ForwardResult(success=True, result_id=ctx.job_id)
            logger.error(
                "[JOB:%s] Gagal register ke main backend: HTTP %d %s",
                ctx.job_id, resp.status_code, resp.text[:300],
            )
            return ForwardResult(success=False, error="HTTP %d" % resp.status_code)
        except Exception as e:
            logger.error("[JOB:%s] Error register ke main backend: %s", ctx.job_id, e)
            return ForwardResult(success=False, error=str(e))


class UploadTranscriptStrategy(ResultForwarder):
    """
    Cabang 2 (berat): upload PDF transkrip ke /upload_transcript backend
    utama (port 4000). BEDA dari RegisterResultStrategy:
      - multipart/form-data (file PDF asli + field 'campaign'), bukan JSON.
      - endpoint ini men-generate result_id BARU sendiri (bukan job_id STT),
        dan men-trigger pipeline evaluasi LLM penuh (create_result +
        enqueue Celery task worker.tasks.process_transcript).
      - butuh header X-API-Key.
      - campaign HARDCODE via DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN kecuali
        di-override lewat ctx.campaign.

    File dibuka ULANG di setiap percobaan retry (stream habis setelah
    sekali dikirim, tidak bisa dipakai ulang di attempt berikutnya).
    """

    name = "upload_transcript"

    def forward(
        self, ctx: ForwardContext, max_retries: int = 3, timeout: int = 30,
    ) -> ForwardResult:
        campaign = ctx.campaign or DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN

        # Nama file WAJIB .pdf (validasi backend: endswith(".pdf")). Prefix
        # sebelum "_" pertama dipertahankan sama dengan nama asli supaya
        # customer_id matching di agent_error_summary (dashboard) tidak rusak.
        filename_for_upload = str(Path(ctx.original_filename).with_suffix(".pdf").name)

        headers  = service_auth_headers()
        last_exc = None
        resp     = None

        for attempt in range(max_retries):
            try:
                with open(ctx.pdf_path, "rb") as f:
                    files = {"files": (filename_for_upload, f, "application/pdf")}
                    data  = {"campaign": campaign}
                    resp = requests.post(
                        MAIN_BACKEND_UPLOAD_TRANSCRIPT_URL,
                        files=files, data=data, headers=headers, timeout=timeout,
                    )
                if resp.status_code < 500:
                    break  # sukses atau error klien (4xx) -- tidak perlu retry
                logger.warning(
                    "[JOB:%s] /upload_transcript HTTP %d (attempt %d/%d)",
                    ctx.job_id, resp.status_code, attempt + 1, max_retries,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = e
                resp = None
                logger.warning(
                    "[JOB:%s] /upload_transcript error attempt %d/%d: %s",
                    ctx.job_id, attempt + 1, max_retries, e,
                )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

        if resp is None:
            logger.error(
                "[JOB:%s] /upload_transcript tidak merespons setelah %d percobaan. Last: %s",
                ctx.job_id, max_retries, last_exc,
            )
            return ForwardResult(success=False, error=str(last_exc))

        if resp.status_code in (401, 403):
            logger.error(
                "[JOB:%s] /upload_transcript auth gagal (HTTP %d) — cek MAIN_BACKEND_API_KEY. Body: %s",
                ctx.job_id, resp.status_code, resp.text[:300],
            )
            return ForwardResult(success=False, error="auth_failed_%d" % resp.status_code)

        if not (200 <= resp.status_code < 300):
            logger.error(
                "[JOB:%s] Gagal upload_transcript ke main backend: HTTP %d %s",
                ctx.job_id, resp.status_code, resp.text[:300],
            )
            return ForwardResult(success=False, error="HTTP %d" % resp.status_code)

        try:
            new_result_id = resp.json().get("result_id")
        except Exception:
            new_result_id = None

        logger.info(
            "[JOB:%s] /upload_transcript sukses -> result_id backend baru = %s",
            ctx.job_id, new_result_id,
        )
        return ForwardResult(success=True, result_id=new_result_id)


def forward_to_all_branches(ctx: ForwardContext) -> dict:
    """
    Helper dipakai oleh pipeline dashboard-baru: jalankan KEDUA strategi
    dan kembalikan hasil masing-masing. Kedua cabang independen — gagal
    salah satu tidak menggagalkan yang lain (sama seperti behavior asli
    di process_and_forward_to_new_dashboard).
    """
    register_result = RegisterResultStrategy().forward(ctx)
    upload_result   = UploadTranscriptStrategy().forward(ctx)
    return {
        "register_result": register_result,
        "upload_result":   upload_result,
    }
