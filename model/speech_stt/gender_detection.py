#!/usr/bin/env python3
"""
model/speech_stt/gender_detection.py

Deteksi gender per speaker berbasis DSP pitch (analyze_gender_and_fraud_dsp).
Dipindah verbatim dari method SpeechSTT._detect_gender_for_speaker,
_run_gender_detection, _classify_gender_by_pitch, _build_speaker_label.
"""

import logging
from typing import Any, Dict, List

from model.speech_stt.audio_slicing import extract_speaker_audio
from model.speech_stt.dsp_analysis import analyze_gender_and_fraud_dsp
from model.speech_stt.tempfiles import cleanup_temp_file
from model.speech_stt.config import DSP_FEMALE_THRESHOLD_HZ

logger = logging.getLogger(__name__)


def detect_gender_for_speaker(
    audio_path: str, diar_segments: List[Dict[str, Any]], speaker: str,
    pitch_history: Dict[str, Any],
) -> str:
    tmp_path = None
    try:
        tmp_path = extract_speaker_audio(audio_path, diar_segments, speaker)
        if tmp_path is None:
            return "unknown"
        result = analyze_gender_and_fraud_dsp(
            speaker, tmp_path, pitch_history, seg_timestamp=0.0)
        if result in ("male", "female"):
            return result
        history_val = pitch_history.get(speaker, (0.0, 0.0))
        pitch = history_val[0] if isinstance(history_val, tuple) else history_val
        return "female" if pitch > DSP_FEMALE_THRESHOLD_HZ else "male"
    except Exception as e:
        logger.warning("[Gender/DSP] %s error: %s", speaker, e)
        return "unknown"
    finally:
        if tmp_path:
            cleanup_temp_file(tmp_path)


def run_gender_detection(
    audio_path: str, diar_segments: List[Dict[str, Any]],
    pitch_history: Dict[str, Any], max_workers: int = None,
) -> Dict[str, str]:
    import concurrent.futures
    speakers   = list(set(s["speaker"] for s in diar_segments))
    gender_map: Dict[str, str] = {}
    if not speakers:
        return gender_map
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(speakers)) as ex:
        futures = {
            ex.submit(detect_gender_for_speaker,
                      audio_path, diar_segments, spk, pitch_history): spk
            for spk in speakers
        }
        for fut in concurrent.futures.as_completed(futures):
            spk = futures[fut]
            try:
                gender_map[spk] = fut.result()
            except Exception as e:
                logger.warning("[Gender] Speaker %s error: %s", spk, e)
                gender_map[spk] = "unknown"
    logger.info("[Gender] Hasil: %s", gender_map)
    return gender_map


def classify_gender_by_pitch(wav_path: str) -> str:
    result = analyze_gender_and_fraud_dsp("_tmp", wav_path, {}, seg_timestamp=0.0)
    return result if result in ("male", "female") else "unknown"


def build_speaker_label(speaker: str, gender_map: Dict[str, str]) -> str:
    gender = gender_map.get(speaker, "unknown")
    return speaker if gender == "unknown" else "%s (%s)" % (speaker, gender)
