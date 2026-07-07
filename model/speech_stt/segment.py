#!/usr/bin/env python3
"""
model/speech_stt/segment.py

SpeakerSegment — unit dasar hasil alignment (dipakai di semua strategi
alignment, lexical correction, formatter). Dipindah verbatim.
"""

import logging
from typing import Any, Dict, List

from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)


class SpeakerSegment:
    def __init__(self, start: float, end: float, text: str, speaker: str,
                 confidence: float = 0.5):
        self.start       = start
        self.end         = end
        self.text        = text
        self.speaker     = speaker
        self._confidence = confidence


def map_speakers_to_binary(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not segments:
        return segments
    dur: Dict[str, float] = {}
    for seg in segments:
        spk = seg["speaker"]
        dur[spk] = dur.get(spk, 0.0) + (seg["end"] - seg["start"])
    agent_spk = max(dur, key=lambda k: dur[k])
    logger.info(
        "[Diarization] Speaker durasi: %s → agent=%s",
        {k: "%.1fs" % v for k, v in sorted(dur.items(), key=lambda x: -x[1])},
        agent_spk,
    )
    mapped = []
    for seg in segments:
        binary = AGENT_SPEAKER_LABEL if seg["speaker"] == agent_spk else CUSTOMER_SPEAKER_LABEL
        mapped.append({**seg, "speaker": binary, "speaker_original": seg["speaker"]})
    return mapped
