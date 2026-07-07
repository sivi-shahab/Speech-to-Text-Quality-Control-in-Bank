#!/usr/bin/env python3
"""
services/stt_audio_prep.py

_convert_audio_for_stt() dipindah verbatim dari speech_routes.py.
Terpisah dari services/audio_upload.py karena tanggung jawabnya beda:
audio_upload.py = "terima & validasi file dari HTTP request",
stt_audio_prep.py = "siapkan audio yang SUDAH di-upload untuk STT engine"
(phase-fix, resample, dsb — lewat convert_audio_to_wav di model/speech_stt.py).
"""

import os
import uuid
import tempfile
import logging
from pathlib import Path

from model.speech_stt import convert_audio_to_wav
from services.audio_upload import cleanup_temp_file

logger = logging.getLogger(__name__)


def convert_audio_for_stt(src_path: str, job_id: str) -> str:
    """
    Konversi file audio upload ke WAV 16kHz mono dengan phase fix lengkap.

    Memanggil convert_audio_to_wav() dari speech_stt.py yang berisi:
      - _classify_phase_issue: deteksi FULL_INVERSION/PARTIAL/COMB_FILTER/MS_ENCODED
      - _build_af_filter: pilih ffmpeg filter yang tepat per jenis issue
      - _repair_phase_numpy: FFT phase smoothing untuk PARTIAL/COMB_FILTER
      - GSM-aware threshold: tidak false-reject audio GSM 13kbps

    Return: path file WAV hasil konversi (caller wajib cleanup)
    Raise:  RuntimeError jika konversi gagal
    """
    unique_id = uuid.uuid4().hex[:8]
    fd_wav, wav_path = tempfile.mkstemp(
        suffix=".wav",
        prefix="stt_converted_%s_" % unique_id,
    )
    os.close(fd_wav)

    try:
        logger.info(
            "[JOB:%s] Konversi audio: %s → %s",
            job_id, Path(src_path).name, Path(wav_path).name,
        )
        convert_audio_to_wav(src_path, wav_path)
        logger.info("[JOB:%s] Konversi selesai.", job_id)
        return wav_path

    except Exception as e:
        cleanup_temp_file(wav_path)
        logger.error(
            "[JOB:%s] Konversi audio gagal (%s): %s",
            job_id, type(e).__name__, e,
        )
        raise RuntimeError(
            "Konversi audio gagal: %s — %s" % (type(e).__name__, e)
        )
