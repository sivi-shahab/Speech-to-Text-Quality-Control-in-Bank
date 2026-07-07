#!/usr/bin/env python3
"""
model/speech_stt/quality_checks.py

- is_hallucination(): filter output Whisper yang "berhalusinasi"
  (mengucap kalimat template YouTube dsb saat audio silent/noise).
- validate_diarization_output(): cek imbalance speaker, correction
  rate, dan excessive silence setelah alignment selesai.

Dipindah verbatim dari method SpeechSTT._is_hallucination dan
_validate_diarization_output.
"""

import logging
from typing import Any, Dict, List

from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.config import _HALLUCINATION_PATTERNS

logger = logging.getLogger(__name__)


def is_hallucination(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped in _HALLUCINATION_PATTERNS:
        if stripped:
            logger.warning("Hallucination: '%s'", stripped[:60])
        return True
    word_count = len(stripped.split())
    if word_count < 3 and len(stripped) < 15:
        return True
    words = stripped.split()
    if len(words) > 4 and len(set(words)) / len(words) < 0.4:
        return True
    return False


def validate_diarization_output(
    stats:                 Dict[str, Any],
    aligned:               List[SpeakerSegment],
    reattributed_count:    int,
    segments_before_merge: int = 0,
) -> Dict[str, Any]:
    warnings: List[str] = []
    try:
        talk_time    = stats.get("talk_time", {})
        agent_pct    = talk_time.get("agent",   {}).get("pct", 0)
        customer_pct = talk_time.get("nasabah", {}).get("pct", 0)
        silence_pct  = talk_time.get("silence_pct", 0)

        if agent_pct > 85 or customer_pct < 10:
            logger.warning(
                "[VALIDATION] IMBALANCE WARNING: Agent %.1f%% / Customer %.1f%% "
                "— possible diarization failure. Recommend manual review.",
                agent_pct, customer_pct,
            )
            warnings.append("speaker_imbalance_detected")
            stats["validation_warning"] = "speaker_imbalance"

        # FIX #5: gunakan pre-merge count sebagai denominator
        denom = segments_before_merge if segments_before_merge > 0 else len(aligned)
        if denom > 0:
            correction_rate = reattributed_count / denom * 100
            if correction_rate > 40:
                logger.warning(
                    "[VALIDATION] HIGH CORRECTION RATE: %.1f%% (%d/%d segmen pre-merge) "
                    "— pyannote output quality may be poor.",
                    correction_rate, reattributed_count, denom,
                )
                warnings.append("high_correction_rate")
                stats.setdefault("validation_warning", "high_corrections")
            else:
                logger.info(
                    "[VALIDATION] Correction rate OK: %.1f%% (%d/%d segmen pre-merge).",
                    correction_rate, reattributed_count, denom,
                )

        if silence_pct > 20:
            logger.warning(
                "[VALIDATION] EXCESSIVE SILENCE: %.1f%% — audio quality may be poor.",
                silence_pct,
            )
            warnings.append("excessive_silence")
            stats.setdefault("validation_warning", "silence")

    except Exception as e:
        logger.debug("[Validation] Error: %s", e)

    stats["validation_warnings"] = warnings
    return stats
