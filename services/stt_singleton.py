#!/usr/bin/env python3
"""
services/stt_singleton.py

[SINGLETON PATTERN]
get_stt() menjamin cuma ADA 1 instance SpeechSTT per proses, di-share
semua thread (double-checked locking). Dipindah verbatim dari server.py
v4.0 — sebelumnya logic ini campur dengan setup FastAPI app, CORS,
endpoint definitions, dsb dalam 1 file.

Thread safety untuk transcribe/diarization sendiri ditangani DI DALAM
model/speech_stt (_GPU_TRANSCRIBE_LOCKS, _PYANNOTE_SEMAPHORES) — modul
ini hanya menjamin proses loading model-nya sendiri singleton.
"""

import os
import logging
import threading

import torch

from services.server_config import ASSIGNED_GPU, LOGICAL_GPU_INDEX
from utils.vars import MAX_FILE_SIZE, MODEL_WHISPER

logger = logging.getLogger("server")

_stt_instance = None
_stt_lock     = threading.Lock()


def get_stt():
    """
    Return singleton SpeechSTT untuk proses ini.
    Thread-safe via double-checked locking.
    Semua thread dalam proses share instance yang sama —
    model Whisper dan pyannote hanya dimuat sekali ke VRAM.
    """
    global _stt_instance
    if _stt_instance is not None:
        return _stt_instance

    with _stt_lock:
        if _stt_instance is not None:
            return _stt_instance

        pid = os.getpid()

        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(LOGICAL_GPU_INDEX)
            vram_gb  = (
                torch.cuda.get_device_properties(LOGICAL_GPU_INDEX).total_memory
                / 1024 ** 3
            )
            logger.info(
                "[PID:%d] GPU %d (logical:0): %s | %.1f GB VRAM",
                pid, ASSIGNED_GPU, gpu_name, vram_gb,
            )
        else:
            logger.warning("[PID:%d] CUDA tidak tersedia — CPU mode.", pid)

        from model.speech_stt import SpeechSTT
        from model.speech_stt import config as stt_config

        # [FIXED] Sebelumnya hardcoded "Loading Whisper + pyannote" apa
        # adanya, tidak peduli ASR_BACKEND/DIARIZATION_BACKEND yang
        # sebenarnya di-set — misleading kalau pakai ElevenLabs/NeMo.
        asr_backend_name = stt_config.ASR_BACKEND
        diarization_backend_name = (
            "elevenlabs (diarize=true, built-in)"
            if asr_backend_name == "elevenlabs"
            else stt_config.DIARIZATION_BACKEND
        )
        logger.info(
            "[PID:%d] Loading %s + %s (sekali untuk semua thread)...",
            pid, asr_backend_name, diarization_backend_name,
        )

        instance = SpeechSTT(
            model_name    = MODEL_WHISPER,
            max_file_size = MAX_FILE_SIZE,
            worker_id     = 0,           # selalu 0 — 1 instance per proses
            gpu_id        = LOGICAL_GPU_INDEX,
            audio_profile = "telephony",
        )
        instance.load_model()

        _stt_instance = instance

        # [FIXED] Sebelumnya hardcoded "Whisper: shared | pyannote: ..."
        # — sekarang laporkan backend ASR & diarization yang BENAR-BENAR
        # aktif (instance.asr_backend_name, bukan asumsi selalu whisper).
        logger.info(
            "[PID:%d] STT ready — ASR: %s | Diarization: %s | GPU: %d (physical: %d)",
            pid,
            instance.asr_backend_name,
            diarization_backend_name if instance.asr_backend_name == "elevenlabs"
                else ("OK" if instance.nemo_available else "disabled (set HF_TOKEN)"),
            LOGICAL_GPU_INDEX,
            ASSIGNED_GPU,
        )

    return _stt_instance


def get_current_instance_or_none():
    """Dipakai endpoint diagnostik yang TIDAK boleh memicu load model."""
    return _stt_instance


def clear_instance():
    """Dipanggil saat shutdown (lifespan) untuk reset state module-level."""
    global _stt_instance
    _stt_instance = None