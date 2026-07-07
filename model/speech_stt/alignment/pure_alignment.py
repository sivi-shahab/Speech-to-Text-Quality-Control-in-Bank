#!/usr/bin/env python3
"""
model/speech_stt/alignment/pure_alignment.py

[STRATEGY PATTERN — implementasi AKTIF]
Ini adalah algoritma alignment yang benar-benar dipanggil oleh
transcribe_with_diarization() di versi asli (v7.0 "PURE MODE").
Alignment MURNI berbasis overlap waktu pyannote, ditambah 1 koreksi
sangat konservatif untuk kata tunggal respons customer.

Dipindah verbatim — tidak ada perubahan logic/threshold/urutan.
"""

import bisect
import logging
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

from model.speech_stt.alignment.base import AlignmentStrategy
from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.lexical_rules import SINGLE_WORD_CUSTOMER
from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)


def align_pure_overlap(whisper_segments, diar_segments) -> List[SpeakerSegment]:
    """
    Alignment MURNI berbasis overlap pyannote. Tiap kata di-assign ke
    speaker yang punya overlap waktu terbesar. Kata tanpa overlap sama
    sekali ikut speaker kata SEBELUMNYA (continuity sederhana, tanpa
    batas/heuristik rumit). Tidak ada QA-split, tidak ada lexical
    flip multi-mekanisme, tidak ada mid-word merge.
    """
    if not diar_segments:
        return [SpeakerSegment(s.start, s.end, s.text, AGENT_SPEAKER_LABEL, 0.3)
                for s in whisper_segments]

    # Kumpulkan kata + timestamp
    words = []
    for seg in whisper_segments:
        if hasattr(seg, "words") and seg.words:
            for w in seg.words:
                words.append({"start": w.start, "end": w.end, "word": w.word})
        else:
            words.append({"start": seg.start, "end": seg.end, "word": seg.text})
    if not words:
        return []

    sorted_diar = sorted(diar_segments, key=lambda d: d["start"])
    diar_starts = [d["start"] for d in sorted_diar]
    diar_ends   = [d["end"]   for d in sorted_diar]

    logger.info("[ALIGN/pure] %d kata dari %d segmen | diar=%d segs",
                len(words), len(whisper_segments), len(diar_segments))

    word_speakers: List[Tuple[dict, str]] = []
    prev_speaker  = None
    anchored      = 0

    for w in words:
        best_speaker, max_overlap = None, 0.0
        lo = bisect.bisect_left(diar_ends,   w["start"])
        hi = bisect.bisect_right(diar_starts, w["end"])
        for i in range(lo, hi):
            d = sorted_diar[i]
            overlap = min(w["end"], d["end"]) - max(w["start"], d["start"])
            if overlap > max_overlap:
                max_overlap  = overlap
                best_speaker = d["speaker"]

        if best_speaker is not None and max_overlap > 0:
            anchored += 1
        else:
            # Tidak ada overlap: cari segmen terdekat (nearest), kalau
            # masih jauh ikut prev_speaker
            idx = bisect.bisect_right(diar_starts, w["start"])
            cand = sorted_diar[max(0, idx-1): idx+1]
            nearest_spk, nearest_dist = None, float("inf")
            for c in cand:
                dist = min(abs(w["start"]-c["end"]), abs(w["end"]-c["start"]))
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_spk  = c["speaker"]
            if nearest_spk is not None and nearest_dist <= 2.0:
                best_speaker = nearest_spk
            else:
                best_speaker = prev_speaker if prev_speaker else (
                    sorted_diar[0]["speaker"])

        word_speakers.append((w, best_speaker))
        prev_speaker = best_speaker

    logger.info("[ALIGN/pure] %d kata | overlap-anchor: %d/%d (%.1f%%)",
                len(words), anchored, len(words),
                anchored / max(len(words), 1) * 100)

    # Gabung kata berurutan dengan speaker sama
    aligned: List[SpeakerSegment] = []
    cur_spk, cur_start, cur_words = None, None, []
    for w, spk in word_speakers:
        if spk != cur_spk:
            if cur_words:
                aligned.append(SpeakerSegment(
                    cur_start, cur_words[-1]["end"],
                    " ".join(x["word"] for x in cur_words).strip(),
                    cur_spk, 0.7))
            cur_spk, cur_start, cur_words = spk, w["start"], [w]
        else:
            cur_words.append(w)
    if cur_words:
        aligned.append(SpeakerSegment(
            cur_start, cur_words[-1]["end"],
            " ".join(x["word"] for x in cur_words).strip(),
            cur_spk, 0.7))
    return aligned


def safe_single_word_correction(
    aligned: List[SpeakerSegment]
) -> Tuple[List[SpeakerSegment], int]:
    """
    SATU koreksi aman: jika segmen AGENT_BM (agen) isinya HANYA 1 kata
    respons-customer yang sangat khas (ya/iya/tidak/boleh/oke/betul/dst)
    DAN durasinya sangat pendek (<1.5s) DAN diapit oleh segmen agen
    panjang di kedua sisi, flip ke NASABAH.

    Sangat konservatif — hanya kata tunggal yang TIDAK MUNGKIN bagian
    kalimat agen. Tidak menyentuh kalimat multi-kata, tidak ada efek
    domino (pakai speaker ASLI tetangga, bukan hasil koreksi).
    """
    if len(aligned) < 3:
        return aligned, 0

    original = [s.speaker for s in aligned]
    corrected = []
    count = 0
    for idx, seg in enumerate(aligned):
        new_spk = seg.speaker
        dur = seg.end - seg.start
        words = seg.text.lower().strip().rstrip(".,!?").split()

        if (seg.speaker == AGENT_SPEAKER_LABEL
                and dur < 1.5
                and len(words) == 1
                and words[0] in SINGLE_WORD_CUSTOMER
                and 0 < idx < len(aligned) - 1):
            prev_o = original[idx - 1]
            next_o = original[idx + 1]
            # hanya flip jika DIAPIT segmen agen (pola jawaban singkat
            # customer di tengah penjelasan agen)
            if prev_o == AGENT_SPEAKER_LABEL and next_o == AGENT_SPEAKER_LABEL:
                new_spk = CUSTOMER_SPEAKER_LABEL
                count += 1
                logger.info(
                    "[SafeCorrect] [%.2f-%.2f] %s→%s | '%s'",
                    seg.start, seg.end, AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL,
                    seg.text.strip()[:30])

        corrected.append(SpeakerSegment(
            seg.start, seg.end, seg.text, new_spk,
            getattr(seg, '_confidence', 0.7)))

    if count:
        logger.info("[SafeCorrect] Total koreksi aman: %d segmen.", count)
    return corrected, count


def merge_same_speaker_simple(
    aligned: List[SpeakerSegment], max_gap_sec: float = 0.6
) -> List[SpeakerSegment]:
    """
    Merge sederhana: gabung segmen berurutan dengan speaker SAMA dan
    gap <= max_gap_sec. Tidak ada blip-absorption, tidak ada mid-word
    merge, tidak ada manipulasi lintas-speaker. Aman & predictable.
    """
    if not aligned:
        return aligned
    merged = []
    i = 0
    while i < len(aligned):
        cur = aligned[i]
        j = i + 1
        while j < len(aligned):
            nxt = aligned[j]
            gap = nxt.start - cur.end
            if nxt.speaker == cur.speaker and gap <= max_gap_sec:
                cur = SpeakerSegment(
                    cur.start, nxt.end,
                    cur.text.rstrip() + " " + nxt.text.lstrip(),
                    cur.speaker,
                    max(getattr(cur, '_confidence', 0.7),
                        getattr(nxt, '_confidence', 0.7)))
                j += 1
            else:
                break
        merged.append(cur)
        i = j
    if len(merged) < len(aligned):
        logger.info("[Merge/simple] %d → %d segmen (%.1f%% reduction).",
                    len(aligned), len(merged),
                    (1 - len(merged)/len(aligned)) * 100)
    return merged


class PureOverlapAlignmentStrategy(AlignmentStrategy):
    """
    Strategi AKTIF dipakai transcribe_with_diarization(). Menjalankan
    3 langkah berurutan: pure overlap alignment -> koreksi kata tunggal
    aman -> merge speaker sama. (Pitch verification dijalankan terpisah
    oleh caller karena butuh audio_path & terjadi SETELAH safe-correction
    tapi SEBELUM merge — urutan dipertahankan persis di engine.py.)
    """

    name = "pure_overlap"

    def align(self, whisper_segments, diar_segments, audio_path=None):
        aligned = align_pure_overlap(whisper_segments, diar_segments)
        aligned, reattributed_count = safe_single_word_correction(aligned)
        return aligned, reattributed_count
