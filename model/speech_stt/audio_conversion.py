#!/usr/bin/env python3
"""
model/speech_stt/audio_conversion.py

Semua fungsi terkait "ubah file audio jadi WAV 16kHz mono siap-transcribe":
deteksi & perbaikan phase-cancellation, resample via ffmpeg/pydub, dan
dynamic range compression. Dipindah verbatim dari speech_stt.py.
"""

import os
import subprocess
import tempfile
import logging

import numpy as np
import soundfile as sf

from model.speech_stt.config import FFMPEG_TIMEOUT, DRC_ENABLED, DRC_TARGET_DB, \
    DRC_FRAME_MS, DRC_HOP_MS, DRC_MAX_GAIN_DB, DRC_MIN_GAIN_DB, DRC_ATTACK_MS, \
    DRC_RELEASE_MS
from model.speech_stt.model_registry import _check_ffmpeg

logger = logging.getLogger(__name__)


def _classify_phase_issue(src: str) -> dict:
    result = {"issue_type": "NONE", "correlation": 1.0,
              "delay_ms": 0.0, "description": "normal"}
    try:
        data, sr = sf.read(src, frames=30 * 48000, always_2d=True)
        if data.shape[1] < 2:
            result["description"] = "mono input, skip"
            return result
        left  = data[:, 0].astype(np.float64)
        right = data[:, 1].astype(np.float64)
        std_l, std_r = np.std(left), np.std(right)
        if std_l < 1e-9 or std_r < 1e-9:
            result["description"] = "satu channel silence"
            return result
        corr = float(np.corrcoef(left, right)[0, 1])
        result["correlation"] = corr
        n = min(len(left), 8192)
        L = np.fft.rfft(left[:n],  n=n * 2)
        R = np.fft.rfft(right[:n], n=n * 2)
        xcorr    = np.fft.irfft(L * np.conj(R))
        peak_idx = int(np.argmax(np.abs(xcorr)))
        if peak_idx > n:
            peak_idx = peak_idx - n * 2
        delay_ms = round(float(peak_idx) / sr * 1000.0, 3)
        result["delay_ms"] = delay_ms
        sum_sig = (left + right) / 2.0
        n_fft   = min(len(sum_sig), 4096)
        spectrum = np.abs(np.fft.rfft(sum_sig[:n_fft]))
        spec_cv  = np.std(spectrum) / (np.mean(spectrum) + 1e-9)
        has_comb = spec_cv > 2.5 and 0.3 < abs(delay_ms) < 20.0
        if corr < -0.85:
            result["issue_type"] = "FULL_INVERSION"
            result["description"] = "fase terbalik penuh (corr=%.3f)" % corr
        elif corr < -0.3:
            result["issue_type"] = "PARTIAL"
            result["description"] = "fase terbalik parsial (corr=%.3f)" % corr
        elif has_comb:
            result["issue_type"] = "COMB_FILTER"
            result["description"] = "comb filter delay=%.2fms" % delay_ms
        elif abs(corr) < 0.15 and abs(delay_ms) < 0.1:
            result["issue_type"] = "MS_ENCODED"
            result["description"] = "kemungkinan M/S encoding (corr=%.3f)" % corr
        else:
            result["description"] = "normal (corr=%.3f)" % corr
        logger.info("[PhaseCheck] type=%-14s corr=%+.3f delay=%+.2fms | %s",
                    result["issue_type"], corr, delay_ms, result["description"])
    except Exception as e:
        logger.warning("_classify_phase_issue gagal: %s", e)
    return result


def _normalize_for_diarization(src_path: str) -> str:
    data, sr = sf.read(src_path, always_2d=False)
    peak     = np.max(np.abs(data))
    if peak < 1e-9:
        return src_path
    target = 0.708
    if peak < target * 0.9:
        data = np.clip(data * (target / peak), -0.98, 0.98).astype(np.float32)
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="diar_norm_")
        os.close(fd)
        sf.write(tmp, data, sr)
        logger.info("[DiarNorm] peak %.4f → %.4f", peak, np.max(np.abs(data)))
        return tmp
    return src_path


def _build_af_filter(issue: dict) -> str:
    itype    = issue.get("issue_type", "NONE")
    delay_ms = issue.get("delay_ms", 0.0)
    if itype == "FULL_INVERSION":
        return "pan=mono|c0=0.5*c0-0.5*c1"
    elif itype == "PARTIAL":
        return "pan=mono|c0=0.6*c0-0.4*c1"
    elif itype == "COMB_FILTER":
        return "pan=mono|c0=c0" if delay_ms >= 0 else "pan=mono|c0=c1"
    else:
        return "pan=mono|c0=0.5*c0+0.5*c1"


def _repair_phase_numpy(wav_path: str) -> None:
    try:
        data, sr = sf.read(wav_path, always_2d=False)
        sig      = data.astype(np.float32)
        if len(sig) == 0:
            return
        spectrum = np.fft.rfft(sig)
        mag      = np.abs(spectrum)
        phase    = np.angle(spectrum)
        win      = max(3, int(len(phase) * 0.002))
        if win % 2 == 0:
            win += 1
        phase_s   = np.convolve(phase, np.ones(win) / win, mode="same")
        sig_fixed = np.fft.irfft(mag * np.exp(1j * phase_s), n=len(sig)).astype(np.float32)
        peak      = np.max(np.abs(sig_fixed))
        if peak > 1e-9:
            sig_fixed = sig_fixed / peak * 0.95
        sf.write(wav_path, sig_fixed, sr)
    except Exception as e:
        logger.warning("[PhaseRepair] Gagal: %s", e)


def _convert_to_wav_ffmpeg(src: str, dst: str) -> None:
    issue     = _classify_phase_issue(src)
    af_filter = _build_af_filter(issue)
    logger.info("[PhaseConv] %s → filter: %s", issue["issue_type"], af_filter)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1",
         "-af", af_filter, "-f", "wav", "-vn", dst],
        capture_output=True, timeout=FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        err = result.stderr.decode(errors="ignore")[:300]
        raise RuntimeError("ffmpeg gagal (code %d): %s" % (result.returncode, err))
    if issue["issue_type"] in ("PARTIAL", "COMB_FILTER"):
        _repair_phase_numpy(dst)


def _convert_to_wav_pydub_fallback(src: str, dst: str) -> None:
    from pydub import AudioSegment
    audio = AudioSegment.from_file(src)
    if audio.channels == 2:
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32).reshape(-1, 2)
        left, right = samples[:, 0], samples[:, 1]
        std_l, std_r = np.std(left), np.std(right)
        if std_l > 1e-9 and std_r > 1e-9:
            corr = float(np.corrcoef(left, right)[0, 1])
            if corr < -0.3:
                ch_left  = audio.split_to_mono()[0]
                ch_right = audio.split_to_mono()[1].invert_phase()
                audio    = ch_left.overlay(ch_right)
            else:
                audio = audio.set_channels(1)
        else:
            audio = audio.set_channels(1)
    else:
        audio = audio.set_channels(1)
    audio = audio.set_frame_rate(16000)
    audio.export(dst, format="wav")


def apply_dynamic_range_compression(wav_path: str) -> None:
    if not DRC_ENABLED:
        return
    try:
        data, sr = sf.read(wav_path, always_2d=False)
        y = data.astype(np.float32)
        if len(y) == 0:
            return
        frame_len = max(1, int(DRC_FRAME_MS / 1000.0 * sr))
        hop_len   = max(1, int(DRC_HOP_MS   / 1000.0 * sr))
        n_frames  = max(1, (len(y) - frame_len) // hop_len + 1)
        rms_db    = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            s = i * hop_len
            frame    = y[s: s + frame_len]
            rms      = float(np.sqrt(np.mean(frame ** 2) + 1e-12))
            rms_db[i] = 20.0 * np.log10(max(rms, 1e-6))
        raw_gain_db    = np.clip(DRC_TARGET_DB - rms_db, DRC_MIN_GAIN_DB, DRC_MAX_GAIN_DB)
        attack_frames  = max(1, int(DRC_ATTACK_MS  / 1000.0 * sr / hop_len))
        release_frames = max(1, int(DRC_RELEASE_MS / 1000.0 * sr / hop_len))
        smoothed_gain  = raw_gain_db.copy()
        for i in range(1, n_frames):
            delta = raw_gain_db[i] - smoothed_gain[i - 1]
            alpha = (1.0 - np.exp(-1.0 / max(attack_frames, 1))
                     if delta < 0
                     else 1.0 - np.exp(-1.0 / max(release_frames, 1)))
            smoothed_gain[i] = smoothed_gain[i - 1] + alpha * delta
        frame_centers   = np.arange(n_frames) * hop_len + frame_len // 2
        gain_db_samples = np.interp(np.arange(len(y)), frame_centers, smoothed_gain,
                                    left=smoothed_gain[0], right=smoothed_gain[-1]).astype(np.float32)
        gain_linear = np.power(10.0, gain_db_samples / 20.0)
        y_out       = np.clip(y * gain_linear, -0.98, 0.98).astype(np.float32)
        rms_before  = float(20.0 * np.log10(np.sqrt(np.mean(y     ** 2)) + 1e-12))
        rms_after   = float(20.0 * np.log10(np.sqrt(np.mean(y_out ** 2)) + 1e-12))
        logger.info("[DRC] RMS before=%.1fdBFS after=%.1fdBFS gain_range=[%.1f, %.1f]dB",
                    rms_before, rms_after,
                    float(np.min(smoothed_gain)), float(np.max(smoothed_gain)))
        sf.write(wav_path, y_out, sr)
    except Exception as e:
        logger.warning("[DRC] Gagal: %s", e)


def _is_already_valid_wav(src: str) -> bool:
    try:
        info = sf.info(src)
        return info.format == "WAV" and info.samplerate == 16000 and info.channels == 1
    except Exception:
        return False


def convert_audio_to_wav(src: str, dst: str) -> None:
    import shutil
    if _is_already_valid_wav(src):
        shutil.copy2(src, dst)
    elif _check_ffmpeg():
        _convert_to_wav_ffmpeg(src, dst)
    else:
        _convert_to_wav_pydub_fallback(src, dst)
    apply_dynamic_range_compression(dst)
