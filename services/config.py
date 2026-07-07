#!/usr/bin/env python3
"""
services/config.py

Konfigurasi & konstanta yang sebelumnya tersebar di bagian atas
speech_routes.py (env vars, allowed types, magic bytes, dsb).
Dipisah supaya module lain tinggal `from services.config import X`
tanpa ikut menanggung side-effect import (logger setup, executor
init, dsb) yang ada di speech_routes.py versi lama.

Tidak ada perubahan NILAI atau LOGIC — murni pemindahan.
"""

import os
import logging

logger = logging.getLogger(__name__)

# =================================================================
# Upload constraints
# =================================================================
ALLOWED_AUDIO_TYPES = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp3",
    "audio/ogg", "audio/flac",
    "audio/mp4", "audio/m4a",
    "application/octet-stream",
}
ALLOWED_AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".mp3", ".ogg",
    ".flac", ".m4a", ".mp4",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v"}

AUDIO_MAGIC_BYTES = [
    b"RIFF",
    b"ID3",
    b"\xff\xfb",
    b"\xff\xf3",
    b"\xff\xf2",
    b"OggS",
    b"fLaC",
    b"\x00\x00\x00\x20ftyp",
    b"\x00\x00\x00\x18ftyp",
]

MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_MB", "1000")) * 1024 * 1024
MAX_PENDING_JOBS      = int(os.getenv("MAX_PENDING_JOBS",   "200"))
COPY_CHUNK_SIZE       = 1 * 1024 * 1024  # 1 MB

MAX_TRANSCRIBE_WORKERS = int(os.getenv("MAX_TRANSCRIBE_WORKERS", "50"))

# =================================================================
# Dashboard BARU (/upload_transcript, backend utama port 4000)
# — terpisah total dari dashboard LAMA (API_SAVE_DB / MAIN_BACKEND_RESULT_URL)
# =================================================================
MAIN_BACKEND_BASE_URL              = os.getenv("MAIN_BACKEND_BASE_URL", "http://api:4000")
MAIN_BACKEND_UPLOAD_TRANSCRIPT_URL = f"{MAIN_BACKEND_BASE_URL}/upload_transcript"

# Kredensial service-to-service — backend utama (get_current_user di
# api/dependencies.py) menerima header X-API-Key yang dicocokkan persis
# dengan settings.api_key (.env API_KEY). Kalau cocok, request
# diperlakukan sebagai SystemUser(username="system", role="admin").
MAIN_BACKEND_API_KEY = os.getenv("MAIN_BACKEND_API_KEY", os.getenv("API_KEY", ""))
if not MAIN_BACKEND_API_KEY:
    logger.warning(
        "MAIN_BACKEND_API_KEY / API_KEY tidak diset — "
        "/speech/stt/save_dashboard akan gagal 401/403 saat forward ke backend."
    )

# TODO: GANTI sesuai nama campaign AKTIF di tabel campaigns backend
# utama (dicek via crud.get_active_campaign) — kalau tidak cocok
# persis (case-sensitive), backend akan menolak dengan HTTP 422.
DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN = os.getenv(
    "DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN", "GANTI_NAMA_CAMPAIGN_DI_SINI"
)


def service_auth_headers() -> dict:
    """Header autentikasi service-to-service ke backend utama (port 4000)."""
    return {"X-API-Key": MAIN_BACKEND_API_KEY}
