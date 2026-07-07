#!/usr/bin/env python3
"""
services/audio_upload.py

Semua helper terkait "menerima file upload dan menyiapkannya untuk STT":
validasi tipe/magic-bytes, copy ke temp file dengan size guard, deteksi &
konversi video ke audio, dan cleanup. Dipindah verbatim dari
speech_routes.py — tidak ada perubahan logic.
"""

import os
import subprocess
import tempfile
import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile

from services.config import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_AUDIO_TYPES,
    AUDIO_MAGIC_BYTES,
    VIDEO_EXTENSIONS,
    MAX_UPLOAD_SIZE_BYTES,
    COPY_CHUNK_SIZE,
)

logger = logging.getLogger(__name__)


def validate_audio_file(audio_file: UploadFile) -> None:
    ext = Path(audio_file.filename or "").suffix.lower()
    ct  = (audio_file.content_type or "").lower()

    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Ekstensi tidak didukung: '%s'. Gunakan: %s"
                   % (ext, ", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))),
        )
    if ct and ct not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Content-Type tidak didukung: '%s'." % ct,
        )

    try:
        header = audio_file.file.read(16)
        audio_file.file.seek(0)
        if not any(header.startswith(magic) for magic in AUDIO_MAGIC_BYTES):
            raise HTTPException(
                status_code=415,
                detail="File bukan audio valid (magic bytes tidak dikenali).",
            )
    except HTTPException:
        raise
    except Exception:
        pass


def copy_upload_to_temp(audio_file: UploadFile, fd: int) -> int:
    total_written = 0
    with os.fdopen(fd, "wb") as tmp:
        while True:
            chunk = audio_file.file.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Ukuran file melebihi batas %d MB."
                           % (MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)),
                )
            tmp.write(chunk)
    return total_written


def cleanup_temp_file(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def is_video_file(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in VIDEO_EXTENSIONS


def convert_video_to_audio(video_path: str, worker_pid: int) -> str:
    """
    Extract audio track dari file video ke .wav pakai ffmpeg.
    Return path file audio hasil convert. Raise HTTPException kalau gagal.
    """
    fd, audio_path = tempfile.mkstemp(
        suffix="_converted.wav",
        prefix="videoaudio_%d_" % worker_pid,
    )
    os.close(fd)  # kita cuma butuh path-nya, ffmpeg yang tulis filenya

    cmd = [
        "ffmpeg",
        "-y",                # overwrite tanpa prompt
        "-i", video_path,
        "-vn",                # buang video stream
        "-acodec", "pcm_s16le",
        "-ar", "16000",       # sample rate umum buat STT
        "-ac", "1",           # mono
        audio_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # sesuaikan sama durasi video max yang diizinkan
        )
    except subprocess.TimeoutExpired:
        cleanup_temp_file(audio_path)
        raise HTTPException(500, "Konversi video ke audio timeout.")
    except FileNotFoundError:
        cleanup_temp_file(audio_path)
        raise HTTPException(500, "ffmpeg tidak ditemukan di server.")

    if result.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        cleanup_temp_file(audio_path)
        logger.error("[FFMPEG] convert gagal: %s", result.stderr[-1000:])
        raise HTTPException(400, "Gagal mengekstrak audio dari video. Pastikan file video valid.")

    return audio_path
