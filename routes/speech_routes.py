#!/usr/bin/env python3
# =================================================================
# routes/speech_routes.py  —  v4.0 (refactored, behavior identik v3.3)
#
# Router ini sekarang HANYA menangani HTTP concern:
#   - terima UploadFile, validasi, simpan ke temp
#   - deteksi & convert video->audio kalau perlu
#   - schedule background job ke executor
#   - expose status endpoints
#
# Semua orkestrasi job (transcribe -> PDF -> forward hasil) dipindah ke
# services/pipeline.py (Template Method: LegacyDashboardPipeline vs
# NewDashboardPipeline). Semua state global (job storage, webhook queue,
# executor) dipindah ke module services/* masing-masing supaya bisa
# dipakai ulang tanpa import file 900-baris ini.
#
# TIDAK ADA PERUBAHAN PERILAKU dari v3.3 — endpoint, response model,
# urutan validasi, dan pesan error dipertahankan persis sama.
# =================================================================

import os
import asyncio
import tempfile
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Annotated

from fastapi import APIRouter, HTTPException, UploadFile, Request, Form
from pydantic import BaseModel

from services.config import MAX_PENDING_JOBS
from services.executor import transcribe_executor
from services.job_storage import job_storage
from services.webhook_service import send_webhook_notification, _webhook_queue
from services.audio_upload import (
    validate_audio_file,
    copy_upload_to_temp,
    cleanup_temp_file,
    is_video_file,
    convert_video_to_audio,
)
from services.config import DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN
from services.pipeline import LegacyDashboardPipeline, NewDashboardPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class JobResponse(BaseModel):
    job_id:     str
    status:     str
    message:    str
    worker_pid: Optional[int] = None


def _parse_bool_form(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "t", "y", "on")


async def _receive_upload_and_prepare_audio(
    audio_file: UploadFile, worker_pid: int, prefix: str,
) -> str:
    """
    Shared logic dipakai kedua endpoint: simpan upload ke temp file,
    deteksi video, dan convert ke audio kalau perlu. Return path audio
    siap-pakai (WAJIB di-cleanup oleh caller kalau terjadi error setelahnya).
    """
    is_video = is_video_file(audio_file.filename)
    if not is_video:
        validate_audio_file(audio_file)

    fd, temp_audio_path = tempfile.mkstemp(
        suffix = Path(audio_file.filename or "audio").suffix or (".mp4" if is_video else ".wav"),
        prefix = "%s_%d_" % (prefix, worker_pid),
    )
    try:
        written = await asyncio.to_thread(copy_upload_to_temp, audio_file, fd)
    except HTTPException:
        cleanup_temp_file(temp_audio_path)
        raise
    except Exception as e:
        cleanup_temp_file(temp_audio_path)
        raise HTTPException(500, "Gagal menyimpan upload: %s" % e)

    if written == 0:
        cleanup_temp_file(temp_audio_path)
        raise HTTPException(400, "File kosong.")

    if is_video:
        logger.info(
            "[CONVERT] file=%s terdeteksi video, konversi ke audio...",
            audio_file.filename,
        )
        try:
            converted_path = await asyncio.to_thread(
                convert_video_to_audio, temp_audio_path, worker_pid
            )
        except HTTPException:
            cleanup_temp_file(temp_audio_path)
            raise
        cleanup_temp_file(temp_audio_path)  # hapus video asli, sudah tidak dibutuhkan
        temp_audio_path = converted_path
        logger.info("[CONVERT] sukses, audio path=%s", temp_audio_path)

    return temp_audio_path


@router.post("/speech/stt/save", response_model=JobResponse)
async def speech_to_text_save(
    request:            Request,
    audio_file:         UploadFile,
    enable_diarization: Annotated[str, Form()] = "false",
    language:           Annotated[str,  Form()] = "id",
    webhook_url:        Annotated[Optional[str], Form()] = None,
    original_path:      Annotated[Optional[str], Form()] = None,
    marker_path:        Annotated[Optional[str], Form()] = None,
):
    """
    Endpoint LAMA — dashboard lama. Tidak lagi mendaftarkan hasil ke
    backend utama (lihat services/pipeline.py::LegacyDashboardPipeline).
    Murni upload PDF ke API 8010.
    """
    _diarization = _parse_bool_form(enable_diarization)
    logger.info(
        "[REQUEST] file=%s | enable_diarization raw=%s parsed=%s",
        audio_file.filename, enable_diarization, _diarization,
    )

    worker_pid = os.getpid()
    temp_audio_path = None
    pending_count = job_storage.count_by_status("pending", "processing")
    if pending_count >= MAX_PENDING_JOBS:
        raise HTTPException(
            status_code=503,
            detail="Server overload (%d jobs aktif). Coba lagi nanti." % pending_count,
        )

    job_id = None
    try:
        job_id = str(uuid.uuid4())
        job_storage.set(job_id, {"status": "pending", "worker_pid": worker_pid})

        temp_audio_path = await _receive_upload_and_prepare_audio(
            audio_file, worker_pid, prefix="upload"
        )

        pipeline = LegacyDashboardPipeline(
            app_state           = request.app.state,
            job_id               = job_id,
            temp_audio_path      = temp_audio_path,
            original_filename    = audio_file.filename,
            enable_diarization   = _diarization,
            language             = language,
            webhook_url          = webhook_url,
            original_path        = original_path,
            marker_path          = marker_path,
        )
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(
            loop.run_in_executor(transcribe_executor, pipeline.run)
        )

        return JobResponse(
            job_id     = job_id,
            status     = "accepted",
            message    = "Processing started (%d jobs aktif)" % (pending_count + 1),
            worker_pid = worker_pid,
        )
    except HTTPException:
        if webhook_url:
            send_webhook_notification(
                job_id or "unknown", "failed", webhook_url,
                original_path=original_path,
            )
        raise
    except Exception as e:
        if temp_audio_path:
            cleanup_temp_file(temp_audio_path)
        if webhook_url:
            send_webhook_notification(
                job_id or "unknown", "failed", webhook_url,
                error=str(e), original_path=original_path,
            )
        raise HTTPException(500, "Gagal menjadwalkan job: %s" % e)


@router.post("/speech/stt/save_dashboard", response_model=JobResponse)
async def speech_to_text_save_dashboard(
    request:            Request,
    audio_file:         UploadFile,
    enable_diarization: Annotated[str, Form()] = "false",
    language:           Annotated[str,  Form()] = "id",
    campaign:           Annotated[Optional[str], Form()] = None,
    webhook_url:        Annotated[Optional[str], Form()] = None,
):
    """
    Endpoint BARU khusus dashboard baru (Results / bank_qa_system port 4000).
    Alur: audio/video -> transcribe -> PDF -> forward ke backend utama lewat
    DUA cabang (lihat services/pipeline.py::NewDashboardPipeline +
    services/backend_forwarder.py), berjalan bersamaan.

    TERPISAH dari /speech/stt/save (dashboard lama).
    """
    _diarization = _parse_bool_form(enable_diarization)
    _campaign    = (campaign or "").strip() or DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN

    logger.info(
        "[REQUEST][DASHBOARD-NEW] file=%s | enable_diarization=%s | campaign=%s",
        audio_file.filename, _diarization, _campaign,
    )

    worker_pid = os.getpid()
    temp_audio_path = None
    pending_count = job_storage.count_by_status("pending", "processing")
    if pending_count >= MAX_PENDING_JOBS:
        raise HTTPException(
            status_code=503,
            detail="Server overload (%d jobs aktif). Coba lagi nanti." % pending_count,
        )

    job_id = None
    try:
        job_id = str(uuid.uuid4())
        job_storage.set(job_id, {"status": "pending", "worker_pid": worker_pid})

        temp_audio_path = await _receive_upload_and_prepare_audio(
            audio_file, worker_pid, prefix="upload_dashboard"
        )

        pipeline = NewDashboardPipeline(
            app_state           = request.app.state,
            job_id               = job_id,
            temp_audio_path      = temp_audio_path,
            original_filename    = audio_file.filename,
            enable_diarization   = _diarization,
            language             = language,
            webhook_url          = webhook_url,
            campaign             = _campaign,
        )
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(
            loop.run_in_executor(transcribe_executor, pipeline.run)
        )

        return JobResponse(
            job_id     = job_id,
            status     = "accepted",
            message    = "Processing started for dashboard-new (%d jobs aktif)" % (pending_count + 1),
            worker_pid = worker_pid,
        )
    except HTTPException:
        if webhook_url:
            send_webhook_notification(job_id or "unknown", "failed", webhook_url)
        raise
    except Exception as e:
        if temp_audio_path:
            cleanup_temp_file(temp_audio_path)
        if webhook_url:
            send_webhook_notification(job_id or "unknown", "failed", webhook_url, error=str(e))
        raise HTTPException(500, "Gagal menjadwalkan job: %s" % e)


@router.get("/speech/job/{job_id}", response_model=JobResponse)
async def get_single_job_status(job_id: str):
    job = job_storage.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    return JobResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        message=job.get("error_message", "Status retrieved"),
        worker_pid=job.get("worker_pid"),
    )


@router.get("/speech/jobs/status")
async def list_job_statuses():
    all_jobs = job_storage.values()
    by_status: Dict[str, int] = {}
    for j in all_jobs:
        s = j.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total_jobs_in_storage":    len(all_jobs),
        "by_status":                by_status,
        "webhook_queue_size":       _webhook_queue.qsize(),
        "max_pending_jobs":         MAX_PENDING_JOBS,
        "transcribe_executor_size": transcribe_executor._max_workers,
    }


@router.get("/speech/stt/status/{job_id}")
async def get_single_job_stt_status(job_id: str):
    job = job_storage.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan atau sudah kadaluarsa")

    return {
        "job_id":        job_id,
        "status":        job.get("status", "unknown"),
        "error_message": job.get("error_message"),
        "result_id":     job.get("result_id"),
        "main_backend_result_id": job.get("main_backend_result_id"),
    }


# =================================================================
# Backward-compat re-exports
# server.py (versi belum direfactor) melakukan:
#   from routes.speech_routes import job_storage, _webhook_queue, _transcribe_executor
# Supaya server.py TIDAK perlu diubah dulu di stage ini, nama-nama itu
# tetap tersedia di sini, walau isinya sekarang live di services/*.
# Akan dihapus saat server.py masuk giliran direfactor (stage berikutnya).
# =================================================================
_transcribe_executor = transcribe_executor
