#!/usr/bin/env python3
"""
routes/diagnostics_routes.py

Endpoint diagnostik/monitoring: /health, /api/v1/queue/status, /gpu/memory.
Dipindah verbatim dari server.py v4.0 (sebelumnya didefinisikan langsung
di app, campur dengan setup CORS/lifespan/dsb).

Path endpoint DIPERTAHANKAN PERSIS sama seperti asli (bukan lewat prefix
router, karena aslinya juga didaftarkan langsung ke `app`).
"""

import os
import time
import threading
import logging

import torch
from fastapi import APIRouter

from services.server_config import ASSIGNED_GPU, LOGICAL_GPU_INDEX, \
    ESTIMATED_MODEL_MEMORY_GB
from services.stt_singleton import get_current_instance_or_none
from services.job_storage import job_storage
from services.webhook_service import _webhook_queue
from services.executor import transcribe_executor
from utils.vars import PORT

logger = logging.getLogger("server")

router = APIRouter()

# =================================================================
# Shared model import untuk status endpoints
# =================================================================
try:
    from model.speech_stt import (
        _SHARED_MODELS,
        _SHARED_MODELS_LOCK,
        _SHARED_PYANNOTE,
    )
except ImportError:
    _SHARED_MODELS      = {}
    _SHARED_MODELS_LOCK = threading.Lock()
    _SHARED_PYANNOTE    = {}


def _get_model_snapshot() -> dict:
    """Ambil snapshot _SHARED_MODELS dengan lock — aman dari race condition read."""
    with _SHARED_MODELS_LOCK:
        data = _SHARED_MODELS.get(LOGICAL_GPU_INDEX)
        return dict(data) if data else {}


# =================================================================
# Health Check
# =================================================================
@router.get("/health")
async def health():
    gpu_info = {}

    if torch.cuda.is_available():
        gid      = LOGICAL_GPU_INDEX
        gpu_info = {
            "physical_gpu":        ASSIGNED_GPU,
            "logical_gpu":         LOGICAL_GPU_INDEX,
            "gpu_name":            torch.cuda.get_device_name(gid),
            "memory_allocated_gb": round(torch.cuda.memory_allocated(gid) / 1024**3, 3),
            "memory_reserved_gb":  round(torch.cuda.memory_reserved(gid)  / 1024**3, 3),
            "memory_total_gb":     round(
                torch.cuda.get_device_properties(gid).total_memory / 1024**3, 3
            ),
        }

    stt = get_current_instance_or_none()
    diar_available = stt.pyannote_pipeline is not None if stt else False

    # [FIXED] Sebelumnya hardcoded "pyannote" — sekarang dinamis sesuai
    # backend yang benar-benar aktif. Untuk ElevenLabs, diarization
    # backend terpisah (pyannote/nemo) TIDAK PERNAH dimuat sama sekali
    # (provides_own_diarization=True), jadi dilaporkan eksplisit sebagai
    # "elevenlabs (diarize=true, built-in)", bukan ikut-ikutan nama
    # default config yang sebenarnya tidak dipakai.
    asr_backend_name = None
    diarization_engine_name = None
    if stt is not None:
        asr_backend_name = stt.asr_backend_name
        if stt.asr_backend_name == "elevenlabs":
            diarization_engine_name = "elevenlabs (diarize=true, built-in)"
        else:
            diarization_engine_name = stt.diarization_backend_name

    return {
        "status":             "healthy",
        "pid":                os.getpid(),
        "assigned_gpu":       ASSIGNED_GPU,
        "port":               PORT,
        "model_loaded":       stt is not None,
        "asr_backend":        asr_backend_name,
        "diarization_engine": diarization_engine_name,
        # "pyannote_available" dipertahankan (backward-compat, dipakai
        # nama lama), "diarization_available" nama baru yang lebih
        # akurat (mencakup pyannote MAUPUN nemo, bukan cuma pyannote).
        "pyannote_available":     diar_available,
        "diarization_available":  diar_available,
        "concurrency_model":  "1-process + ThreadPoolExecutor",
        "gpu_info":           gpu_info,
    }


# =================================================================
# Queue / Executor Status
# =================================================================
@router.get("/api/v1/queue/status")
async def get_queue_status():
    gpu_info = {}
    if torch.cuda.is_available():
        gid      = LOGICAL_GPU_INDEX
        gpu_info = {
            "physical_gpu_id": ASSIGNED_GPU,
            "logical_gpu_id":  LOGICAL_GPU_INDEX,
            "gpu_name":        torch.cuda.get_device_name(gid),
            "vram_allocated":  "%.2fGB" % (torch.cuda.memory_allocated(gid) / 1024**3),
            "vram_total":      "%.2fGB" % (
                torch.cuda.get_device_properties(gid).total_memory / 1024**3
            ),
        }

    shared = _get_model_snapshot()
    pyannote_ok = _SHARED_PYANNOTE.get(LOGICAL_GPU_INDEX) is not None

    # [NEW] Info backend aktif — sebelumnya tidak ada sama sekali di
    # endpoint ini, cuma whisper_loaded/pyannote_loaded (yang tidak
    # eksplisit sebut NeMo/ElevenLabs kalau itu yang dipakai).
    stt = get_current_instance_or_none()
    asr_backend_name = stt.asr_backend_name if stt else None
    diarization_backend_name = None
    if stt is not None:
        diarization_backend_name = (
            "elevenlabs (diarize=true, built-in)"
            if stt.asr_backend_name == "elevenlabs"
            else stt.diarization_backend_name
        )

    model_status = {
        **gpu_info,
        "whisper_loaded":        bool(shared),
        "pyannote_loaded":       pyannote_ok,
        "pyannote_shared":       True,
        "model_sharing":         "in-process (all threads share 1 instance)",
        "asr_backend":           asr_backend_name,
        "diarization_backend":   diarization_backend_name,
    } if shared else {
        **gpu_info, "status": "NOT_INITIALIZED",
        "asr_backend": asr_backend_name,
        "diarization_backend": diarization_backend_name,
    }

    # Job queue stats
    job_stats = {
        "total":     len(job_storage),
        "by_status": {},
        "webhook_q": _webhook_queue.qsize(),
        "executor_max_workers": transcribe_executor._max_workers,
    }
    for j in job_storage.values():
        s = j.get("status", "unknown")
        job_stats["by_status"][s] = job_stats["by_status"].get(s, 0) + 1

    return {
        "timestamp":       time.time(),
        "server_port":     PORT,
        "assigned_gpu":    ASSIGNED_GPU,
        "model_status":    model_status,
        "job_queue":       job_stats,
        "architecture":    "1 process + asyncio + ThreadPoolExecutor (no multiprocessing)",
    }


# =================================================================
# GPU Memory
# =================================================================
@router.get("/gpu/memory")
async def gpu_memory_status():
    if not torch.cuda.is_available():
        return {"error": "CUDA not available"}

    gid       = LOGICAL_GPU_INDEX
    allocated = torch.cuda.memory_allocated(gid) / 1024**3
    reserved  = torch.cuda.memory_reserved(gid)  / 1024**3
    total     = torch.cuda.get_device_properties(gid).total_memory / 1024**3
    shared    = _get_model_snapshot()

    return {
        "timestamp":       time.time(),
        "physical_gpu_id": ASSIGNED_GPU,
        "logical_gpu_id":  LOGICAL_GPU_INDEX,
        "total_gb":        round(total,     3),
        "allocated_gb":    round(allocated, 3),
        "reserved_gb":     round(reserved,  3),
        "free_gb":         round(max(0.0, total - allocated), 3),
        "utilization_pct": round((allocated / total) * 100 if total else 0, 2),
        "model_breakdown": {
            "whisper_gb":    ESTIMATED_MODEL_MEMORY_GB,
            "pyannote_gb":   2.5,
            "working_gb":    round(max(0.0, allocated - ESTIMATED_MODEL_MEMORY_GB - 2.5), 3),
        },
        "vs_multiprocessing": {
            "this_setup_gb":     round(allocated, 2),
            "3_workers_would":   round(allocated * 3, 2),
            "vram_saved_gb":     round(allocated * 2, 2),
        },
    }