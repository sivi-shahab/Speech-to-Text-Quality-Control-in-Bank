#!/usr/bin/env python3
"""
model/speech_stt/__init__.py

speech_stt.py (file tunggal ~1700 baris) sekarang jadi package
model/speech_stt/ (banyak modul kecil). Supaya semua pemanggil lama
TIDAK PERLU DIUBAH — baik di project ini (services/pipeline.py,
services/stt_audio_prep.py) maupun server.py yang belum direfactor —
nama-nama publik yang dulu bisa diimpor langsung dari
`model.speech_stt` tetap di-re-export di sini.

Pemanggil lama yang tetap jalan tanpa perubahan:
    from model.speech_stt import SpeechSTT, convert_audio_to_wav
    from model.speech_stt import _SHARED_MODELS, _SHARED_MODELS_LOCK, _SHARED_PYANNOTE
"""

from model.speech_stt.engine import SpeechSTT
from model.speech_stt.audio_conversion import convert_audio_to_wav
from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.model_registry import (
    _SHARED_MODELS,
    _SHARED_MODELS_LOCK,
    _SHARED_PYANNOTE,
    _SHARED_PYANNOTE_LOCK,
    _SHARED_GENDER,
    _SHARED_GENDER_LOCK,
    _SHARED_EMOTION,
    _SHARED_EMOTION_LOCK,
    _GPU_TRANSCRIBE_LOCKS,
    _PYANNOTE_SEMAPHORES,
    get_gpu_for_worker,
    get_worker_group,
    get_shared_models,
    unregister_worker,
)

__all__ = [
    "SpeechSTT",
    "convert_audio_to_wav",
    "SpeakerSegment",
    "_SHARED_MODELS",
    "_SHARED_MODELS_LOCK",
    "_SHARED_PYANNOTE",
    "_SHARED_PYANNOTE_LOCK",
    "_SHARED_GENDER",
    "_SHARED_GENDER_LOCK",
    "_SHARED_EMOTION",
    "_SHARED_EMOTION_LOCK",
    "_GPU_TRANSCRIBE_LOCKS",
    "_PYANNOTE_SEMAPHORES",
    "get_gpu_for_worker",
    "get_worker_group",
    "get_shared_models",
    "unregister_worker",
]
