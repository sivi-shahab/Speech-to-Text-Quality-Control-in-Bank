#!/usr/bin/env python3
"""
services/tmp_cleanup.py

Sweeper untuk temp file STT yang yatim di TMPDIR (/data/tmp).

Cleanup normal per-job sudah ada di blok `finally` pipeline, tapi blok itu
TIDAK jalan kalau worker mati paksa (restart gunicorn, kill -9, OOM,
worker timeout). Akibatnya file upload yang sedang diproses / masih antre
di transcribe_executor tertinggal selamanya. Modul ini menyapu file
tersebut saat startup dan secara berkala.

Hanya file dengan prefix milik aplikasi ini yang disentuh, dan hanya yang
umurnya (mtime) lebih dari TMP_CLEANUP_MAX_AGE_HOURS — supaya file milik job
yang masih jalan (termasuk dari server instance lain yang berbagi
/data/tmp) tidak ikut terhapus.
"""

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import tempfile
import time

logger = logging.getLogger(__name__)

TMP_CLEANUP_ENABLED          = os.getenv("TMP_CLEANUP_ENABLED", "true").lower() in ("1", "true", "yes")
TMP_CLEANUP_MAX_AGE_HOURS    = float(os.getenv("TMP_CLEANUP_MAX_AGE_HOURS", "6"))
TMP_CLEANUP_INTERVAL_MINUTES = float(os.getenv("TMP_CLEANUP_INTERVAL_MINUTES", "60"))

# Pola nama file temp yang dibuat aplikasi (lihat tempfile.mkstemp/mkdtemp
# di routes/speech_routes.py, services/*, model/speech_stt/*)
_FILE_PATTERNS = (
    "upload_*",              # routes/speech_routes.py (upload & upload_dashboard)
    "videoaudio_*",          # services/audio_upload.py
    "eleven_raw_*",          # routes/elevenlabs_stt_routes.py (body audio mentah)
    "stt_converted_*.wav",   # services/stt_audio_prep.py
    "stt_raw_*.raw",         # model/speech_stt/engine.py
    "emo_seg_*.wav",         # model/speech_stt/audio_slicing.py
    "gender_*.wav",          # model/speech_stt/audio_slicing.py
    "diar_norm_*.wav",       # model/speech_stt/audio_conversion.py
    "transcription_*.pdf",   # services/pipeline.py (LegacyDashboardPipeline)
    "dashboard_new_*.pdf",   # services/pipeline.py (NewDashboardPipeline)
)
# model/speech_stt/engine.py: prefix "stt_<hex8>_" — dicocokkan ketat
# supaya tidak menyapu file "stt_*" lain yang bukan milik kita
_FILE_REGEXES = (re.compile(r"^stt_[0-9a-f]{8}_.*\.wav$"),)
_DIR_PATTERNS = ("nemo_diar_*",)  # model/speech_stt/diarization_backends/nemo_backend.py


def _matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def cleanup_stale_temp_files(tmp_dir: str = None, max_age_hours: float = None) -> int:
    """
    Hapus temp file/dir milik aplikasi yang lebih tua dari max_age_hours.
    Return jumlah entry yang dihapus. Tidak pernah raise.
    """
    tmp_dir       = tmp_dir or tempfile.gettempdir()
    max_age_hours = TMP_CLEANUP_MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    cutoff        = time.time() - max_age_hours * 3600

    removed, freed = 0, 0
    try:
        entries = list(os.scandir(tmp_dir))
    except OSError as e:
        logger.warning("[TMP-CLEANUP] Gagal baca %s: %s", tmp_dir, e)
        return 0

    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            st = entry.stat()
            if st.st_mtime >= cutoff:
                continue

            if entry.is_file():
                if not (_matches(entry.name, _FILE_PATTERNS)
                        or any(r.match(entry.name) for r in _FILE_REGEXES)):
                    continue
                os.remove(entry.path)
                freed += st.st_size
            elif entry.is_dir():
                if not _matches(entry.name, _DIR_PATTERNS):
                    continue
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                continue
            removed += 1
        except FileNotFoundError:
            continue  # sudah dihapus proses lain
        except Exception as e:
            logger.warning("[TMP-CLEANUP] Gagal hapus %s: %s", entry.path, e)

    if removed:
        logger.info(
            "[TMP-CLEANUP] %d temp file yatim dihapus dari %s (%.1f MB, umur > %.1f jam)",
            removed, tmp_dir, freed / (1024 * 1024), max_age_hours,
        )
    return removed


async def periodic_tmp_cleanup() -> None:
    """Loop background: sweep sekali saat start, lalu tiap interval."""
    interval = max(TMP_CLEANUP_INTERVAL_MINUTES, 1) * 60
    while True:
        await asyncio.to_thread(cleanup_stale_temp_files)
        await asyncio.sleep(interval)
