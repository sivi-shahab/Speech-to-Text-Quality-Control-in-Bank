#!/usr/bin/env python3
"""
model/speech_stt/alignment/pitch_verification.py

v7.1 — Pitch-based speaker verification untuk MEMPERKUAT pyannote.
Dipanggil terpisah dari PureOverlapAlignmentStrategy karena butuh
audio_path (baca sample audio langsung), bukan cuma segmen kata.
Dipindah verbatim dari speech_stt.py.
"""

import logging
from typing import List, Tuple

import numpy as np
import soundfile as sf

from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.dsp_analysis import _compute_median_pitch

logger = logging.getLogger(__name__)


def pitch_verify_speakers(
    aligned: List[SpeakerSegment], audio_path: str,
) -> Tuple[List[SpeakerSegment], int]:
    """
    Pyannote kadang salah-tukar label di segmen tertentu (mis. blok
    closing agen perempuan ke-label sebagai nasabah laki-laki). Kita
    koreksi pakai sinyal FISIK pitch (F0), bukan tebakan teks.

    Algoritma:
      1. Bangun profil pitch global tiap speaker (median F0 dari
         gabungan segmen panjang masing-masing).
      2. Hanya lanjut jika kedua profil BERBEDA JELAS (selisih >=
         PITCH_SEPARATION_MIN Hz). Kalau mirip (mis. 2 perempuan),
         pitch tidak diskriminatif -> SKIP semua (fail-safe).
      3. Untuk tiap segmen PANJANG (>= MIN_DUR), hitung pitch-nya.
         Jika pitch jauh lebih dekat ke profil speaker LAIN
         (selisih >= PITCH_MARGIN), koreksi label segmen itu.

    Konservatif: segmen pendek dibiarkan; koreksi hanya saat selisih
    pitch sangat meyakinkan. Worst case (pitch mirip) = no-op.
    """
    PITCH_SEPARATION_MIN = 40.0   # Hz — beda minimal antar profil
    PITCH_MARGIN         = 25.0   # Hz — keyakinan koreksi
    MIN_DUR              = 2.0     # detik — hanya segmen panjang

    if len(aligned) < 2:
        return aligned, 0

    try:
        data, sr = sf.read(audio_path, always_2d=False)
    except Exception as e:
        logger.warning("[PitchVerify] gagal baca audio: %s", e)
        return aligned, 0

    # ── Tahap 1: profil pitch global per speaker ──
    spk_samples: dict = {}
    for seg in aligned:
        if (seg.end - seg.start) < MIN_DUR:
            continue
        s_f = int(seg.start * sr)
        e_f = min(int(seg.end * sr), len(data))
        if e_f <= s_f:
            continue
        spk_samples.setdefault(seg.speaker, []).append(data[s_f:e_f])

    profiles: dict = {}
    for spk, chunks in spk_samples.items():
        if not chunks:
            continue
        combined = np.concatenate(chunks).astype(np.float32)
        # batasi durasi analisa agar cepat (maks 30 detik)
        if len(combined) > 30 * sr:
            combined = combined[:30 * sr]
        p = _compute_median_pitch(combined, sr)
        if p is not None:
            profiles[spk] = p

    if len(profiles) < 2:
        logger.info("[PitchVerify] profil <2 speaker, skip.")
        return aligned, 0

    spk_list = list(profiles.keys())
    sep = abs(profiles[spk_list[0]] - profiles[spk_list[1]])
    logger.info("[PitchVerify] profil pitch: %s | separasi=%.1fHz",
                {k: "%.1fHz" % v for k, v in profiles.items()}, sep)

    if sep < PITCH_SEPARATION_MIN:
        logger.info("[PitchVerify] pitch terlalu mirip (%.1fHz < %.1fHz), "
                    "skip verifikasi (fail-safe).", sep, PITCH_SEPARATION_MIN)
        return aligned, 0

    # ── Tahap 2: verifikasi tiap segmen panjang ──
    corrected = []
    count = 0
    for seg in aligned:
        new_spk = seg.speaker
        if (seg.end - seg.start) >= MIN_DUR:
            s_f = int(seg.start * sr)
            e_f = min(int(seg.end * sr), len(data))
            if e_f > s_f:
                seg_pitch = _compute_median_pitch(
                    data[s_f:e_f].astype(np.float32), sr)
                if seg_pitch is not None:
                    # cari speaker dengan profil pitch terdekat
                    dist_cur = abs(seg_pitch - profiles.get(seg.speaker, seg_pitch))
                    other = [s for s in profiles if s != seg.speaker]
                    if other:
                        other_spk  = min(other, key=lambda s: abs(seg_pitch - profiles[s]))
                        dist_other = abs(seg_pitch - profiles[other_spk])
                        # koreksi jika JELAS lebih dekat ke speaker lain
                        if dist_cur - dist_other >= PITCH_MARGIN:
                            new_spk = other_spk
                            count += 1
                            logger.info(
                                "[PitchVerify] KOREKSI [%.2f-%.2f] %s→%s | "
                                "seg_pitch=%.1fHz (cur=%.1f, other=%.1f)",
                                seg.start, seg.end, seg.speaker, new_spk,
                                seg_pitch, dist_cur, dist_other)
        corrected.append(SpeakerSegment(
            seg.start, seg.end, seg.text, new_spk,
            getattr(seg, '_confidence', 0.7)))

    if count:
        logger.info("[PitchVerify] Total koreksi pitch: %d segmen.", count)
    else:
        logger.info("[PitchVerify] Tidak ada koreksi (pyannote konsisten dgn pitch).")
    return corrected, count
