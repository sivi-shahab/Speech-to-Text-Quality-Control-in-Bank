#!/usr/bin/env python3
"""
model/speech_stt/diarization_backends/pyannote_backend.py

Wrapper dari logic diarization yang SUDAH ADA di versi asli (dulu inline
di dalam SpeechSTT.perform_diarization). Dipindah TANPA PERUBAHAN LOGIC
— semaphore HOTFIX A, normalize sebelum diarize, dan cara membangun
segmen dari `pipeline(...).itertracks()` semuanya persis sama.
"""

import logging
from typing import Any, Dict, List

from model.speech_stt.diarization_backends.base import DiarizationBackend

logger = logging.getLogger(__name__)


class PyannoteDiarizationBackend(DiarizationBackend):
    name = "pyannote"

    def __init__(self, gpu_id: int):
        self.gpu_id   = gpu_id
        self.pipeline = None  # di-set load() atau langsung oleh engine.py kalau sudah shared

    def load(self, gpu_id: int) -> None:
        from model.speech_stt.model_registry import get_shared_pyannote_only
        self.pipeline = get_shared_pyannote_only(gpu_id)

    def is_available(self) -> bool:
        return self.pipeline is not None

    def diarize(self, audio_path: str, num_speakers: int = 2) -> List[Dict[str, Any]]:
        from model.speech_stt.model_registry import get_pyannote_semaphore
        from model.speech_stt.audio_conversion import _normalize_for_diarization
        from model.speech_stt.tempfiles import cleanup_temp_file

        # HOTFIX A: semaphore=1 mencegah cross-contamination antar job
        sem = get_pyannote_semaphore(self.gpu_id)
        with sem:
            norm_path = _normalize_for_diarization(audio_path)
            try:
                diarization = self.pipeline(norm_path, num_speakers=num_speakers)
            finally:
                if norm_path != audio_path:
                    cleanup_temp_file(norm_path)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({"start": turn.start, "end": turn.end,
                              "speaker": speaker, "index": len(segments)})
        return segments
