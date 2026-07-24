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

    # [FIX] Agent ditentukan dari SIAPA YANG BICARA DULUAN (start time
    # paling kecil di seluruh segmen), BUKAN durasi bicara terlama seperti
    # sebelumnya (agent_spk = max(dur, ...)). Alasan: heuristik durasi bisa
    # salah label kalau nasabah kebetulan bicara lebih lama daripada agent
    # (banyak bertanya, hold time yang salah ke-hitung sebagai bicara,
    # dst) -- padahal siapa yang MEMULAI panggilan hampir selalu agent
    # (dia yang melakukan outbound call, greeting duluan).
    #
    # Pakai min() atas SELURUH segments (bukan asumsi list sudah urut
    # waktu) supaya tetap benar walau urutan segments dari upstream
    # (mis. hasil gabungan beberapa sumber) tidak terjamin kronologis.
    earliest_seg = min(segments, key=lambda s: s["start"])
    agent_spk    = earliest_seg["speaker"]

    logger.info(
        "[Diarization] Speaker durasi: %s → agent=%s (bicara duluan, start=%.2fs)",
        {k: "%.1fs" % v for k, v in sorted(dur.items(), key=lambda x: -x[1])},
        agent_spk, earliest_seg["start"],
    )
    mapped = []
    for seg in segments:
        binary = AGENT_SPEAKER_LABEL if seg["speaker"] == agent_spk else CUSTOMER_SPEAKER_LABEL
        mapped.append({**seg, "speaker": binary, "speaker_original": seg["speaker"]})
    return mapped