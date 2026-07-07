#!/usr/bin/env python3
"""
model/speech_stt/tempfiles.py

Helper kecil untuk hapus temp file. Sengaja DIPISAH dari
services/audio_upload.cleanup_temp_file (walau logic-nya identik) supaya
layer `model/` tidak punya dependency ke layer `services/` — model
harus bisa dipakai berdiri sendiri tanpa FastAPI app di sekitarnya.
"""

import os


def cleanup_temp_file(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
