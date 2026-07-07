#!/usr/bin/env python3
"""
model/speech_stt/diarization_backends/base.py

[STRATEGY PATTERN]
Interface yang wajib diimplementasikan setiap "mesin diarization"
(pyannote vs NeMo/MSDD, atau backend lain di masa depan). Ini SUMBU
VARIASI YANG BERBEDA dari asr_backends/ (yang mengatur "apa yang
diucapkan") — modul ini mengatur "siapa bicara kapan", SEBELUM proses
alignment/transcribe jalan.

Output `diarize()` HARUS berbentuk list of dict dengan key "start",
"end", "speaker" (index opsional, akan diisi ulang oleh caller) — sama
seperti output pyannote yang sudah ada, supaya
`segment.map_speakers_to_binary()` dan seluruh pipeline setelahnya
(alignment, formatter, dst) tidak perlu tahu backend diarization mana
yang dipakai.
"""

from typing import Any, Dict, List


class DiarizationBackend:
    name: str = "base"

    def load(self, gpu_id: int) -> None:
        raise NotImplementedError

    def is_available(self) -> bool:
        raise NotImplementedError

    def diarize(self, audio_path: str, num_speakers: int = 2) -> List[Dict[str, Any]]:
        """Return list of {"start": float, "end": float, "speaker": str}."""
        raise NotImplementedError
