#!/usr/bin/env python3
"""
model/speech_stt/emotion_detection.py

Deteksi emosi (arousal/dominance/valence) per speaker/segmen memakai
model audeering wav2vec2, plus konklusi telemarketing (POSITIF/NEGATIF/
NETRAL). Dipindah verbatim dari method SpeechSTT._get_telemarketing_conclusion
dan _detect_emotion_for_speaker.
"""

import logging
from typing import Any, Dict, List

from model.speech_stt.audio_slicing import extract_speaker_audio
from model.speech_stt.tempfiles import cleanup_temp_file
from model.speech_stt.model_registry import _SHARED_EMOTION

logger = logging.getLogger(__name__)

_NEUTRAL = {"arousal": 0.5, "dominance": 0.5, "valence": 0.5}


def get_telemarketing_conclusion(emo: dict) -> str:
    s = emo.get("valence",   0.5)
    i = emo.get("arousal",   0.5)
    k = emo.get("dominance", 0.5)
    if s > 0.60:
        return "NETRAL" if (k < 0.40 and i < 0.40) else "POSITIF"
    elif s < 0.40:
        return "NETRAL" if (k < 0.40 and i < 0.40) else "NEGATIF"
    else:
        return "NEGATIF" if (k > 0.60 and i > 0.60) else "NETRAL"


def detect_emotion_for_speaker(
    gpu_id: int, audio_path: str, diar_segments: List[Dict[str, Any]], speaker: str,
) -> Dict[str, float]:
    classifier = _SHARED_EMOTION.get(gpu_id)
    if classifier is None:
        return dict(_NEUTRAL)
    tmp_path = None
    try:
        tmp_path = extract_speaker_audio(audio_path, diar_segments, speaker)
        if tmp_path is None:
            return dict(_NEUTRAL)
        results = classifier(tmp_path)
        emotion_metrics: Dict[str, float] = {}
        for res in results:
            label = res["label"].lower().strip()
            if "arousal"   in label: emotion_metrics["arousal"]   = float(res["score"])
            elif "dominance" in label: emotion_metrics["dominance"] = float(res["score"])
            elif "valence"   in label: emotion_metrics["valence"]   = float(res["score"])
        for key in ("arousal", "dominance", "valence"):
            emotion_metrics.setdefault(key, 0.5)
        return emotion_metrics
    except Exception as e:
        logger.warning("[Emotion] %s: %s", speaker, e)
        return dict(_NEUTRAL)
    finally:
        if tmp_path:
            cleanup_temp_file(tmp_path)
