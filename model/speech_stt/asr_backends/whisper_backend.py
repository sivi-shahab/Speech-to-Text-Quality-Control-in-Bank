#!/usr/bin/env python3
"""
model/speech_stt/asr_backends/whisper_backend.py

Wrapper dari logic transcribe yang SUDAH ADA di versi asli (dipanggil
inline di dalam transcribe_with_diarization/transcribe_simple_complete).
Dipindah ke sini TANPA PERUBAHAN LOGIC — cuma dibungkus interface
ASRBackend supaya bisa dipertukarkan dengan backend lain (mis. ElevenLabsBackend).
"""

import logging
from typing import Any, List, Optional, Tuple

from model.speech_stt.asr_backends.base import ASRBackend
from model.speech_stt.model_registry import get_gpu_transcribe_lock, get_shared_models

logger = logging.getLogger(__name__)


class WhisperBackend(ASRBackend):
    name = "whisper"
    supports_word_timestamps = True

    def __init__(self, model_name: str, gpu_id: int, vad_params: dict):
        self.model_name = model_name
        self.gpu_id     = gpu_id
        self.vad_params = vad_params
        self.model      = None  # BatchedInferencePipeline, di-set load() atau engine.py

    def load(self, gpu_id: int) -> None:
        shared = get_shared_models(gpu_id, self.model_name)
        self.model = shared["whisper"]

    def _build_kwargs(self, language: str, initial_prompt: Optional[str] = None) -> dict:
        kwargs = dict(
            language                   = language,
            beam_size                  = 5,
            condition_on_previous_text = False,
            vad_filter                 = False,
            vad_parameters             = self.vad_params,
            word_timestamps            = True,
        )
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        logger.info("[KWARGS] max_speech_duration_s=%s | threshold=%s",
                    self.vad_params.get("max_speech_duration_s"),
                    self.vad_params.get("threshold"))
        return kwargs

    def transcribe_full(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> Tuple[List[Any], Any]:
        kwargs = self._build_kwargs(language, initial_prompt)
        with get_gpu_transcribe_lock(self.gpu_id):
            segs_gen, info = self.model.model.transcribe(audio_path, **kwargs)
            return list(segs_gen), info

    def transcribe_segment(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> str:
        segs, _info = self.transcribe_full(audio_path, language, initial_prompt)
        return " ".join(s.text.strip() for s in segs)
