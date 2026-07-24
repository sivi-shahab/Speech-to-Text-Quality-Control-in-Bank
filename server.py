#!/usr/bin/env python3
# =================================================================
# server.py  —  v5.0 (refactored, behavior identik v4.0)
#
# File ini sekarang cuma: (1) jaga urutan import supaya CUDA isolation
# tetap benar, (2) buat FastAPI app + lifespan + CORS, (3) daftarkan
# router, (4) entry point uvicorn. Semua logic lain sudah pindah ke:
#   services/server_config.py   — env/CUDA setup (WAJIB diimpor pertama)
#   services/stt_singleton.py   — Singleton get_stt()
#   routes/diagnostics_routes.py — /health, /api/v1/queue/status, /gpu/memory
#   routes/webhook_callback_routes.py — /speech/webhook/callback
#
# ⚠️ JANGAN UBAH URUTAN IMPORT DI BAWAH. `services.server_config` harus
# jadi import PERTAMA sebelum apa pun yang menyentuh torch (termasuk
# `routes.speech_routes`, yang sekarang transitif meng-import
# `model.speech_stt` yang meng-import torch) — kalau tidak, CUDA
# isolation antar proses/GPU tidak akan berfungsi.
# =================================================================

import services.server_config as server_config  # HARUS PALING ATAS

import os
import sys
import time
import logging
import threading
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routes import speech_routes, diagnostics_routes, webhook_callback_routes
from services.stt_singleton import get_stt, clear_instance
from utils.vars import PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

logger.info(
    "Server v5.0 | ASSIGNED_GPU=%d | CUDA_VISIBLE_DEVICES='%s' | PORT=%d",
    server_config.ASSIGNED_GPU, os.environ["CUDA_VISIBLE_DEVICES"], PORT,
)


# =================================================================
# Lifespan — load model saat startup, close saat shutdown
# =================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    pid = os.getpid()
    logger.info("STARTUP | PID:%d | GPU:%d (physical)", pid, server_config.ASSIGNED_GPU)

    try:
        stt = get_stt()
        app.state.stt_instance = stt
        logger.info("STT instance bound to app.state | PID:%d", pid)
    except Exception as e:
        logger.critical(
            "GAGAL load model: %s\n"
            "Pastikan:\n"
            "  1. CUDA tersedia dan GPU tidak OOM\n"
            "  2. Model sudah di-download ke WHISPER_CACHE_DIR\n"
            "  3. HF_TOKEN di-set di .env jika ingin pyannote\n"
            "Aplikasi tidak bisa berjalan tanpa model.",
            e, exc_info=True,
        )
        raise

    yield

    # Shutdown
    if hasattr(app.state, "stt_instance"):
        app.state.stt_instance.close()
    clear_instance()
    logger.info("SHUTDOWN complete | PID:%d", pid)


# [FIXED] Sebelumnya deskripsi hardcoded "Whisper + pyannote" — sekarang
# ambil dari config supaya akurat kalau ASR_BACKEND/DIARIZATION_BACKEND
# di-set ke elevenlabs/nemo. model.speech_stt.config aman di-import di
# sini (cuma baca env var, tidak import torch/cuda).
from model.speech_stt import config as _stt_config
_asr_backend_desc = _stt_config.ASR_BACKEND
_diarization_backend_desc = (
    "elevenlabs (diarize=true, built-in)"
    if _asr_backend_desc == "elevenlabs"
    else _stt_config.DIARIZATION_BACKEND
)

# =================================================================
# FastAPI App
# =================================================================
app = FastAPI(
    title       = "Speech STT API — GPU %d" % server_config.ASSIGNED_GPU,
    version     = "5.0.0",
    description = (
        "STT Server | GPU %d | 1 proses + ThreadPoolExecutor | "
        "ASR: %s + Diarization: %s (shared in-process)"
        % (server_config.ASSIGNED_GPU, _asr_backend_desc, _diarization_backend_desc)
    ),
    lifespan = lifespan,
)

# =================================================================
# CORS
# =================================================================
if server_config.CORS_HAS_WILDCARD and server_config.DEPLOYMENT_MODE == "prod":
    logger.critical(
        "CORS_ORIGINS='*' tidak diizinkan di production. "
        "Set CORS_ORIGINS ke domain spesifik di .env"
    )
    sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = server_config.CORS_ORIGINS,
    allow_credentials = not server_config.CORS_HAS_WILDCARD,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

if server_config.CORS_HAS_WILDCARD:
    logger.warning("CORS wildcard aktif — hanya untuk development.")


# =================================================================
# Dependency — dipakai oleh speech_routes (kalau ada endpoint yang
# butuh STT instance lewat Depends())
# =================================================================
def get_stt_instance(request: Request):
    stt = getattr(request.app.state, "stt_instance", None)
    if stt is not None:
        return stt
    return get_stt()


# =================================================================
# Route Registration
# =================================================================
app.include_router(
    speech_routes.router,
    prefix = "/api/v1",
    tags   = ["speech"],
)
app.include_router(diagnostics_routes.router, tags=["diagnostics"])
app.include_router(webhook_callback_routes.router, tags=["webhook"])
# [PINDAH] campaign_routes & recording_routes sudah dipindah ke
# lightweight_server.py (port 8002) -- lihat catatan di sana. Endpoint
# /campaign/cashline-ntb dan /recording/tms-cashline TIDAK LAGI ada di
# server.py ini (port 8000/8001), supaya tidak antre di belakang proses
# transkripsi STT yang berat (masalah "Read timed out" yang berulang).

# =================================================================
# Entry Point — DEV only
# Production: gunicorn server:app -k uvicorn.workers.UvicornWorker -w 1
# =================================================================
if __name__ == "__main__":
    print("\n%s" % ("=" * 65))
    print("  STT SERVER v5.0 — GPU %d | PORT %d" % (server_config.ASSIGNED_GPU, PORT))
    print("=" * 65)
    print("  Architecture  : 1 process + asyncio + ThreadPoolExecutor")
    print("  Physical GPU  : %d" % server_config.ASSIGNED_GPU)
    print("  Logical GPU   : 0 (via CUDA_VISIBLE_DEVICES)")
    from utils.vars import MODEL_WHISPER
    print("  Model         : %s (loaded once, shared all threads)" % MODEL_WHISPER)
    print("  Mode          : %s" % server_config.DEPLOYMENT_MODE)
    print()
    print("  DEPLOYMENT COMMANDS:")
    print("    Dev  : python server.py")
    print("    Prod : gunicorn server:app \\")
    print("             -k uvicorn.workers.UvicornWorker \\")
    print("             -w 1 \\")
    print("             --bind 0.0.0.0:%d" % PORT)
    print()
    print("  WARNING: Jangan gunakan -w > 1 — setiap worker process")
    print("           akan load model sendiri → OOM!")
    print("=" * 65)

    if server_config.DEPLOYMENT_MODE != "dev":
        print("\nGunakan Gunicorn untuk production.\n")
        sys.exit(1)

    uvicorn.run(
        "server:app",
        host               = "0.0.0.0",
        port               = PORT,
        workers            = 1,     # HARUS 1 — jangan ubah
        log_level          = "info",
        timeout_keep_alive = 300,
        limit_concurrency  = 500,
        backlog            = 2048,
    )