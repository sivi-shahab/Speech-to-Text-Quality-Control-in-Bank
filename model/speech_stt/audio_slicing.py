#!/usr/bin/env python3
"""
model/speech_stt/audio_slicing.py

Helper "potong audio jadi klip pendek per speaker / per segmen" yang
dipakai bersama oleh gender detection dan emotion detection. Dipindah
verbatim dari method SpeechSTT._extract_speaker_audio dan
SpeechSTT._extract_single_segment_audio (tidak menyentuh state `self`
sama sekali di versi asli, jadi aman jadi plain function).
"""

import os
import tempfile
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def extract_speaker_audio(
    audio_path: str, diar_segments: List[Dict[str, Any]], speaker: str
) -> Optional[str]:
    segs = [s for s in diar_segments if s["speaker"] == speaker]
    if not segs:
        return None
    try:
        data, sr = sf.read(audio_path, always_2d=False)
    except Exception as e:
        logger.warning("[Gender] Gagal baca audio: %s", e)
        return None
    chunks, total_dur, MAX_DUR_SEC = [], 0.0, 30.0
    for seg in sorted(segs, key=lambda s: s["start"]):
        if total_dur >= MAX_DUR_SEC:
            break
        s_f = int(seg["start"] * sr)
        e_f = min(int(seg["end"] * sr), len(data))
        if e_f <= s_f:
            continue
        chunk = data[s_f:e_f][:int((MAX_DUR_SEC - total_dur) * sr)]
        chunks.append(chunk)
        total_dur += len(chunk) / sr
    if total_dur < 1.0:
        return None
    combined = np.concatenate(chunks).astype(np.float32)
    peak = np.max(np.abs(combined))
    if peak > 1e-9:
        combined = combined / peak
    fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="gender_%s_" % speaker)
    os.close(fd)
    sf.write(tmp_path, combined, sr)
    return tmp_path


def extract_single_segment_audio(
    audio_path: str, start: float, end: float, speaker: str
) -> Optional[str]:
    if (end - start) < 0.5:
        return None
    try:
        data, sr = sf.read(audio_path, always_2d=False)
        s_frame  = int(start * sr)
        e_frame  = min(int(end * sr), len(data))
        chunk    = data[s_frame:e_frame].astype(np.float32)
        if len(chunk) == 0:
            return None
        peak = np.max(np.abs(chunk))
        if peak > 1e-9:
            chunk = chunk / peak
        fd, tmp_path = tempfile.mkstemp(
            suffix=".wav", prefix="emo_seg_%s_" % speaker)
        os.close(fd)
        sf.write(tmp_path, chunk, sr)
        return tmp_path
    except Exception as e:
        logger.warning("[Emotion/seg] Gagal potong segmen audio: %s", e)
        return None
