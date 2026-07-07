#!/usr/bin/env python3
"""
model/speech_stt/dsp_analysis.py

Analisis pitch (F0) berbasis sinyal fisik untuk deteksi gender & fraud
(pitch jump / pitch unstable), dipindah verbatim dari speech_stt.py.
"""

import logging
from typing import Any, Dict, Optional

import numpy as np

from model.speech_stt.config import (
    DSP_HIGHPASS_HZ, DSP_FMIN_HZ, DSP_FMAX_HZ, DSP_MIN_VOICED_FRAMES,
    DSP_FRAUD_ENABLED, DSP_FRAUD_JUMP_WINDOW_SEC, DSP_FRAUD_JUMP_HZ,
    DSP_FRAUD_STD_HZ, DSP_FEMALE_THRESHOLD_HZ,
)

logger = logging.getLogger(__name__)


def _compute_median_pitch(y: np.ndarray, sr: int) -> Optional[float]:
    """
    Hitung median F0 (pitch) dari array audio. Mengembalikan None jika
    voiced frames terlalu sedikit (tidak reliable). Dipakai untuk
    pitch-based speaker verification (v7.1) — memperkuat pyannote.
    """
    try:
        import librosa
        from scipy.signal import butter, lfilter
        if len(y) == 0:
            return None
        nyq     = 0.5 * sr
        b, a    = butter(5, DSP_HIGHPASS_HZ / nyq, btype="high")
        y_clean = lfilter(b, a, y).astype(np.float32)
        f0       = librosa.yin(y_clean, fmin=DSP_FMIN_HZ, fmax=DSP_FMAX_HZ, sr=sr)
        valid_f0 = f0[(~np.isnan(f0)) & (f0 > 0)]
        if len(valid_f0) < DSP_MIN_VOICED_FRAMES:
            return None
        return float(np.median(valid_f0))
    except Exception:
        return None


def analyze_gender_and_fraud_dsp(
    speaker_id: str, audio_path: str,
    pitch_history: Dict[str, Any], seg_timestamp: float = 0.0,
) -> str:
    try:
        import librosa
        from scipy.signal import butter, lfilter
        y, sr = librosa.load(audio_path, sr=None)
        if len(y) == 0:
            return "unknown"
        nyq     = 0.5 * sr
        b, a    = butter(5, DSP_HIGHPASS_HZ / nyq, btype="high")
        y_clean = lfilter(b, a, y).astype(np.float32)
        f0       = librosa.yin(y_clean, fmin=DSP_FMIN_HZ, fmax=DSP_FMAX_HZ, sr=sr)
        valid_f0 = f0[(~np.isnan(f0)) & (f0 > 0)]
        if len(valid_f0) < DSP_MIN_VOICED_FRAMES:
            logger.info("[DSP/Gender] %s: voiced frames terlalu sedikit (%d) → unknown",
                        speaker_id, len(valid_f0))
            return "unknown"
        median_pitch = float(np.median(valid_f0))
        std_pitch    = float(np.std(valid_f0))
        n_frames     = len(valid_f0)
        adaptive_bonus    = max(0.0, (30 - n_frames) / 30 * 25.0) if n_frames < 30 else 0.0
        effective_std_thr = DSP_FRAUD_STD_HZ + adaptive_bonus
        logger.info("[DSP/Gender] %s: median_F0=%.1fHz std=%.1fHz voiced=%d",
                    speaker_id, median_pitch, std_pitch, n_frames)
        if DSP_FRAUD_ENABLED and speaker_id in pitch_history:
            last_pitch, last_ts = pitch_history[speaker_id]
            time_since_last     = seg_timestamp - last_ts
            jump                = abs(median_pitch - last_pitch)
            if time_since_last <= DSP_FRAUD_JUMP_WINDOW_SEC and jump > DSP_FRAUD_JUMP_HZ:
                pitch_history[speaker_id] = (median_pitch, seg_timestamp)
                result = "suspect_fraud_pitch_jump_%dhz_to_%dhz" % (
                    int(last_pitch), int(median_pitch))
                logger.warning("[DSP/Fraud] %s: PITCH JUMP %.1f→%.1fHz",
                               speaker_id, last_pitch, median_pitch)
                return result
        pitch_history[speaker_id] = (median_pitch, seg_timestamp)
        if DSP_FRAUD_ENABLED and std_pitch > effective_std_thr:
            result = "suspect_fraud_pitch_unstable_%dhz" % int(median_pitch)
            logger.warning("[DSP/Fraud] %s: PITCH UNSTABLE std=%.1fHz", speaker_id, std_pitch)
            return result
        gender = "female" if median_pitch > DSP_FEMALE_THRESHOLD_HZ else "male"
        logger.info("[DSP/Gender] %s → %s (%.1fHz)", speaker_id, gender, median_pitch)
        return gender
    except ImportError:
        logger.warning("[DSP/Gender] librosa atau scipy tidak terinstall.")
        return "unknown"
    except Exception as e:
        logger.warning("[DSP/Gender] Error untuk %s: %s", speaker_id, e)
        return "unknown"
