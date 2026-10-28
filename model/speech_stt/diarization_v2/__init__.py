"""
model/speech_stt/diarization_v2/

Diarization v2 untuk ElevenLabs Scribe, di balik flag DIARIZATION_V2_MODE
(off | shadow | on, default off). Lihat docs/diarization-impl-plan.md.

  off    -> jalur lama engine.py berjalan apa adanya, modul ini tidak dipanggil.
  shadow -> jalur lama tetap menentukan output; v2 dihitung dari raw response
            yang SAMA (tanpa panggilan API tambahan) dan hanya masuk
            metadata["diarization_v2"].
  on     -> output diarization diambil dari v2.
"""
from model.speech_stt.diarization_v2.pipeline import run_v2, shadow_from_raw
from model.speech_stt.diarization_v2.provider import (
    CachedRawResponseProvider,
    DiarizationProvider,
    ScribeDiarizationProvider,
    words_from_raw,
)
from model.speech_stt.diarization_v2.roles import assign_roles
from model.speech_stt.diarization_v2.types import DiarizationResult, RoleDecision, Utterance, Word

__all__ = [
    "CachedRawResponseProvider",
    "DiarizationProvider",
    "DiarizationResult",
    "RoleDecision",
    "ScribeDiarizationProvider",
    "assign_roles",
    "Utterance",
    "Word",
    "run_v2",
    "shadow_from_raw",
    "words_from_raw",
]
