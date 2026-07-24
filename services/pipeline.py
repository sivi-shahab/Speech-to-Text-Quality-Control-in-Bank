#!/usr/bin/env python3
"""
services/pipeline.py

[TEMPLATE METHOD PATTERN]

Versi asli punya 2 fungsi background job yang ~80% identik:
  process_and_upload_with_webhook()        (dashboard lama)
  process_and_forward_to_new_dashboard()   (dashboard baru)

Keduanya menjalankan urutan LANGKAH YANG SAMA:
  1. Ambil instance STT
  2. Set status "processing" + kirim webhook
  3. Convert audio (phase-fix), fallback ke file asli kalau gagal
  4. Ambil dynamic prompt dari DB (kalau ada)
  5. Transcribe (dengan/tanpa diarization)
  6. Build PDF
  7. "Kirim" hasil ke tujuan akhir  <-- SATU-SATUNYA bagian yang beda
  8. Set status akhir + webhook selesai/gagal
  9. Cleanup semua temp file (selalu jalan, walau error di tengah)

Karena hanya langkah #7 yang beda (upload ke API 8010 vs forward dua-cabang
ke backend utama), ini kandidat tekstbuk untuk Template Method: base class
`STTJobPipeline` mengunci urutan langkah di `run()`, subclass hanya
override `deliver_result()`.

TIDAK ADA PERUBAHAN PERILAKU dari 2 fungsi asli — termasuk pesan log,
urutan cleanup, dan kondisi error masing-masing dipertahankan persis.
"""

import os
import logging
import tempfile
from text_normalization import normalize_transcript_text_safe
import traceback
from pathlib import Path
from typing import Optional

from services.job_storage import job_storage
from services.webhook_service import send_webhook_notification
from services.audio_upload import cleanup_temp_file
from services.stt_audio_prep import convert_audio_for_stt
from services.db import get_dynamic_prompt_from_db, save_stt_pipeline_results
from services.http_retry import post_with_retry
from services.backend_forwarder import ForwardContext, forward_to_all_branches
from utils.pdf_builder import create_transcription_pdf
from utils.vars import URL_SAVE_DB

logger = logging.getLogger(__name__)


class STTJobPipeline:
    """
    Base class — skeleton alur job STT background. Subclass WAJIB
    override `deliver_result()` untuk menentukan apa yang terjadi
    setelah PDF berhasil dibuat.
    """

    #: dipakai di log prefix, mis. "[DASHBOARD-NEW]"
    log_tag: str = ""
    #: prefix nama file temp PDF
    pdf_prefix: str = "transcription"

    def __init__(
        self,
        app_state,
        job_id:             str,
        temp_audio_path:    str,
        original_filename:  str,
        enable_diarization: bool,
        language:           str,
        webhook_url:        Optional[str] = None,
    ):
        self.app_state          = app_state
        self.job_id              = job_id
        self.temp_audio_path     = temp_audio_path
        self.original_filename   = original_filename
        self.enable_diarization  = enable_diarization
        self.language            = language
        self.webhook_url         = webhook_url
        self.worker_pid          = os.getpid()

    # -----------------------------------------------------------------
    # Hook wajib di-override subclass
    # -----------------------------------------------------------------
    def deliver_result(
        self,
        temp_pdf_path: str,
        transcribed_text: str,
        confidence: float,
        metadata: dict,
    ) -> None:
        """
        Dipanggil setelah PDF berhasil dibuat. Subclass bertanggung
        jawab men-set job_storage status akhir + mengirim webhook
        notification sesuai kontrak masing-masing tujuan.
        """
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Hook opsional — bisa dioverride kalau webhook "processing" butuh
    # payload tambahan (tidak dipakai di 2 pipeline saat ini, tapi
    # disediakan supaya ekstensi berikutnya tidak perlu ubah base class)
    # -----------------------------------------------------------------
    def extra_processing_webhook_kwargs(self) -> dict:
        return {}

    # -----------------------------------------------------------------
    # Template method — JANGAN override, urutan langkah dikunci di sini
    # -----------------------------------------------------------------
    def run(self) -> None:
        logger.info(
            "[JOB:%s]%s enable_diarization=%s | language=%s | file=%s",
            self.job_id, self.log_tag, self.enable_diarization,
            self.language, self.original_filename,
        )

        stt = self._get_stt_instance()
        if stt is None:
            self._fail("STT instance tidak ditemukan.")
            cleanup_temp_file(self.temp_audio_path)
            return

        if self.enable_diarization and stt.pyannote_pipeline is None:
            logger.warning(
                "[JOB:%s] pyannote tidak tersedia, lanjut tanpa diarization.",
                self.job_id,
            )
            self.enable_diarization = False

        # [NEW] ElevenLabs mendapat data speaker "gratis" dari API call yang
        # sama (diarize=true tidak menambah panggilan/biaya terpisah dari
        # transcribe biasa) — upgrade otomatis ke mode diarization supaya
        # analisis gender/emotion lebih akurat (per-speaker, bukan
        # whole-file), MESKI client eksplisit minta enable_diarization=False.
        # Konsekuensi: teks output IKUT berubah format (pakai label speaker),
        # ini keputusan yang sudah dikonfirmasi — bukan bug.
        if not self.enable_diarization and getattr(stt, "asr_backend_name", None) == "elevenlabs":
            logger.info(
                "[JOB:%s] Backend ElevenLabs terdeteksi — upgrade otomatis ke "
                "mode diarization (client minta enable_diarization=False, "
                "tapi ElevenLabs dapat data speaker 'gratis' dari API call "
                "yang sama; dipakai untuk analisis gender/emotion yang lebih "
                "akurat). Teks output akan memakai format per-speaker.",
                self.job_id,
            )
            self.enable_diarization = True

        temp_pdf_path  = None
        converted_path = None

        try:
            job_storage.set(self.job_id, {"status": "processing", "worker_pid": self.worker_pid})
            send_webhook_notification(
                self.job_id, "processing", self.webhook_url,
                **self.extra_processing_webhook_kwargs(),
            )

            # ── Konversi audio (phase fix) sebelum transcribe ──
            try:
                converted_path = convert_audio_for_stt(self.temp_audio_path, self.job_id)
            except RuntimeError as conv_err:
                logger.warning(
                    "[JOB:%s] Konversi gagal, fallback ke file asli: %s",
                    self.job_id, conv_err,
                )
                converted_path = None

            audio_to_transcribe = converted_path if converted_path else self.temp_audio_path

            logger.info(
                "[JOB:%s] Transkripsi dimulai (diarization=%s, lang=%s, audio=%s)",
                self.job_id, self.enable_diarization, self.language,
                "converted" if converted_path else "original (fallback)",
            )

            tiket_id       = Path(self.original_filename).stem
            dynamic_prompt = get_dynamic_prompt_from_db(tiket_id)

            if self.enable_diarization:
                transcribed_text, confidence, metadata = stt.transcribe_with_diarization(
                    audio_to_transcribe, self.language,
                    initial_prompt=dynamic_prompt,
                    original_filename=self.original_filename,
                )
            else:
                transcribed_text, confidence, metadata = stt.transcribe_simple_complete(
                    audio_to_transcribe, self.language, initial_prompt=dynamic_prompt
                )

            logger.info(
                "[JOB:%s] Transkripsi selesai | speaker_count=%s",
                self.job_id, metadata.get("speaker_count") if metadata else "N/A",
            )

            if metadata:
                job_data = job_storage.get(self.job_id) or {}
                job_data["speaker_count"] = metadata.get("speaker_count", 1)
                job_storage.set(self.job_id, job_data)

            # [NEW] Simpan hasil pipeline ke DB — dipanggil untuk SEMUA
            # backend. asr_raw_response otomatis None untuk non-elevenlabs
            # (lihat engine.py), gender/emotion selalu diteruskan apa
            # adanya. Kegagalan di sini SENGAJA tidak menggagalkan job
            # (cuma di-log) — PDF dan webhook tetap harus jalan.
            if metadata:
                try:
                    save_stt_pipeline_results(
                        job_id=self.job_id,
                        audio_file=self.original_filename,
                        asr_backend=getattr(stt, "asr_backend_name", "unknown"),
                        asr_raw_response=metadata.get("elevenlabs_raw_response"),
                        gender_detection_result=metadata.get("speaker_genders"),
                        emotion_detection_result=metadata.get("speaker_stats_aggr"),
                    )
                except Exception as e:
                    logger.error(
                        "[JOB:%s] Gagal simpan hasil pipeline ke DB (non-fatal): %s",
                        self.job_id, e,
                    )

            # ── Normalisasi angka untuk teks TAMPILAN (PDF) ──
            # HANYA memengaruhi teks yang di-render ke PDF. `transcribed_text`
            # ASLI (tidak dinormalisasi) tetap diteruskan ke deliver_result()
            # di bawah -- branch lain (RegisterResultStrategy,
            # UploadTranscriptStrategy) TIDAK terpengaruh sama sekali.
            # Gagal (network/API/timeout) -> fallback otomatis ke
            # transcribed_text asli (lihat normalize_transcript_text_safe),
            # TIDAK PERNAH menggagalkan job. Toggle via env TEXT_NORM_ENABLED
            # (default OFF -- nyalakan setelah divalidasi dengan API key +
            # transkrip nyata, lihat catatan di text_normalization.py).
            display_text = transcribed_text
            if os.getenv("TEXT_NORM_ENABLED", "false").lower() in ("1", "true", "yes"):
                logger.info("[JOB:%s] Normalisasi angka transkrip (GPT-5.4-mini)...", self.job_id)
                display_text = normalize_transcript_text_safe(transcribed_text)
                if display_text != transcribed_text:
                    logger.info("[JOB:%s] Normalisasi berhasil, teks PDF diperbarui.", self.job_id)

            # ── Build PDF ──
            logger.info("[JOB:%s] Membuat PDF...", self.job_id)
            try:
                pdf_buffer = create_transcription_pdf(
                    display_text, self.original_filename, metadata=metadata
                )
            except Exception as pdf_err:
                logger.warning(
                    "[JOB:%s] PDF gagal (%s), retry tanpa metadata.",
                    self.job_id, pdf_err,
                )
                pdf_buffer = create_transcription_pdf(display_text, self.original_filename)

            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                prefix="%s_%d_" % (self.pdf_prefix, self.worker_pid),
                delete=False,
            ) as tmp:
                tmp.write(pdf_buffer.getvalue())
                temp_pdf_path = tmp.name

            logger.info("[JOB:%s] PDF tersimpan: %s", self.job_id, temp_pdf_path)

            # ── Hook: langkah yang beda antar pipeline ──
            self.deliver_result(temp_pdf_path, transcribed_text, confidence, metadata)

        except Exception as e:
            error_msg = "Runtime Error: %s" % str(e)
            logger.error(
                "[PID:%d] FATAL Job %s:\n%s",
                self.worker_pid, self.job_id, traceback.format_exc(),
            )
            self._fail(error_msg)

        finally:
            # Cleanup semua temp file — urutan dipertahankan sama seperti asli
            cleanup_temp_file(self.temp_audio_path)
            if converted_path:
                cleanup_temp_file(converted_path)
            if temp_pdf_path:
                cleanup_temp_file(temp_pdf_path)

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    def _get_stt_instance(self):
        stt = getattr(self.app_state, "stt_instance", None)
        if stt is None:
            try:
                from server import get_worker_stt
                stt = get_worker_stt()
            except Exception:
                stt = None
        return stt

    def _fail(self, error_msg: str) -> None:
        job_storage.set(self.job_id, {"status": "failed", "error_message": error_msg})
        send_webhook_notification(
            self.job_id, "failed", self.webhook_url, error=error_msg,
        )


class LegacyDashboardPipeline(STTJobPipeline):
    """
    Pipeline untuk endpoint LAMA (/speech/stt/save).

    [v3.3] TIDAK LAGI memanggil register_result_to_main_backend — fungsi
    itu sudah dipindah sepenuhnya ke NewDashboardPipeline. Pipeline ini
    murni upload PDF ke API 8010, sama seperti sebelum v3.2.
    """

    log_tag    = ""
    pdf_prefix = "transcription"

    def __init__(self, *args, original_path=None, marker_path=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_path = original_path
        self.marker_path    = marker_path

    def extra_processing_webhook_kwargs(self) -> dict:
        return {"original_path": self.original_path}

    def deliver_result(self, temp_pdf_path, transcribed_text, confidence, metadata):
        api_url      = "%s%s" % (URL_SAVE_DB, Path(self.original_filename).stem)
        meta_payload = {
            "confidence":          confidence,
            "language":            self.language,
            "diarization_enabled": self.enable_diarization,
            "speaker_count":       metadata.get("speaker_count", 1) if metadata else 1,
            "worker_pid":          self.worker_pid,
        }

        logger.info(
            "[JOB:%s] Upload ke %s | file_path=%s",
            self.job_id, api_url, temp_pdf_path,
        )
        resp = post_with_retry(
            url         = api_url,
            payload     = {"file_path": temp_pdf_path},
            max_retries = 3,
            timeout     = 30,
        )
        logger.info("[JOB:%s] API response: HTTP %d", self.job_id, resp.status_code)

        if 200 <= resp.status_code < 300:
            job_storage.set(self.job_id, {"status": "completed"})
            logger.info("[JOB:%s] Selesai.", self.job_id)
            send_webhook_notification(
                self.job_id, "completed", self.webhook_url,
                result        = {"message": "PDF uploaded"},
                original_path = self.original_path,
                speaker_count = metadata.get("speaker_count") if metadata else None,
            )
        else:
            error_msg = "API HTTP %d: %s" % (resp.status_code, resp.text[:400])
            logger.error("[JOB:%s] %s", self.job_id, error_msg)
            job_storage.set(self.job_id, {"status": "failed", "error_message": error_msg})
            send_webhook_notification(
                self.job_id, "failed", self.webhook_url,
                error=error_msg, original_path=self.original_path,
            )


class NewDashboardPipeline(STTJobPipeline):
    """
    Pipeline untuk endpoint BARU (/speech/stt/save_dashboard).

    Forward hasil ke backend utama lewat DUA cabang (Strategy pattern,
    lihat services/backend_forwarder.py):
      Cabang 1: RegisterResultStrategy  (result_id = job_id, transkrip plain)
      Cabang 2: UploadTranscriptStrategy (result_id baru, trigger evaluasi LLM)
    Kedua cabang independen — gagal salah satu tidak menggagalkan yang lain.
    Tidak upload ke API 8010 sama sekali (beda dari LegacyDashboardPipeline).
    """

    log_tag    = "[DASHBOARD-NEW]"
    pdf_prefix = "dashboard_new"

    def __init__(self, *args, campaign: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.campaign = campaign

    def run(self) -> None:
        logger.info(
            "[JOB:%s][DASHBOARD-NEW] enable_diarization=%s | language=%s | file=%s | campaign=%s",
            self.job_id, self.enable_diarization, self.language,
            self.original_filename, self.campaign,
        )
        super().run()

    def deliver_result(self, temp_pdf_path, transcribed_text, confidence, metadata):
        ctx = ForwardContext(
            job_id             = self.job_id,
            pdf_path           = temp_pdf_path,
            original_filename  = self.original_filename,
            speaker_count      = metadata.get("speaker_count") if metadata else None,
            confidence         = confidence,
            campaign           = self.campaign,
        )
        branch_results = forward_to_all_branches(ctx)
        registered_result = branch_results["register_result"]
        upload_result      = branch_results["upload_result"]

        registered    = registered_result.success
        new_result_id = upload_result.result_id if upload_result.success else None

        if new_result_id or registered:
            job_storage.set(self.job_id, {
                "status": "completed",
                "result_id": self.job_id if registered else None,
                "main_backend_result_id": new_result_id,
            })
            logger.info(
                "[JOB:%s] Selesai -> registered=%s, main_backend_result_id=%s",
                self.job_id, registered, new_result_id,
            )
            send_webhook_notification(
                self.job_id, "completed", self.webhook_url,
                result={
                    "registered": registered,
                    "main_backend_result_id": new_result_id,
                },
                speaker_count=metadata.get("speaker_count") if metadata else None,
            )
        else:
            error_msg = "Gagal forward ke backend utama (kedua cabang gagal)."
            logger.error("[JOB:%s] %s", self.job_id, error_msg)
            job_storage.set(self.job_id, {"status": "failed", "error_message": error_msg})
            send_webhook_notification(self.job_id, "failed", self.webhook_url, error=error_msg)