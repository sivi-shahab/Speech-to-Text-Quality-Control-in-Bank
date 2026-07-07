#!/usr/bin/env python3
"""
model/speech_stt/alignment/legacy_alignment.py

[STRATEGY PATTERN — implementasi LEGACY / TIDAK AKTIF]

PENTING: Berdasarkan pembacaan transcribe_with_diarization() di kode
sumber, fungsi-fungsi di modul ini TIDAK DIPANGGIL oleh pipeline yang
aktif (yang dipakai adalah PureOverlapAlignmentStrategy). Kode ini
kemungkinan sisa sebelum migrasi ke "v7.0 PURE MODE" (lihat komentar
asli di transcribe_with_diarization: "Semua heuristik kompleks... DIHAPUS
karena terbukti saling bertabrakan dan menghasilkan artefak").

Dipindah APA ADANYA (tidak dihapus, tidak diubah) untuk menjaga behavior
100% sama dengan kode asli — jaga-jaga kalau ada pemanggil lain yang
tidak terlihat di file yang direview. Kalau tim Anda konfirmasi ini
benar-benar tidak dipakai, modul ini aman dihapus di masa depan.

Semua method aslinya @staticmethod atau method yang tidak menyentuh
`self` sama sekali, jadi di sini jadi plain function tanpa efek samping
tambahan.
"""

import logging
from typing import List, Tuple

import numpy as np

from model.speech_stt.alignment.base import AlignmentStrategy
from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.lexical_rules import (
    CUSTOMER_ONLY_RESPONSES, _PASSIVE_CUSTOMER_RESPONSES, VALID_SHORT_WORDS,
    AGENT_OPENER_PHRASES, QA_INLINE_PATTERNS, AGENT_DOCUMENT_WORDS,
    NOT_PROPER_NOUNS, AGENT_RESUME_MARKERS, AGENT_QUESTION_STARTERS,
    FORBIDDEN_CUSTOMER_WORDS, CUSTOMER_QUESTION_WORDS,
)
from model.speech_stt.config import MIN_SEGMENT_DUR_SEC, AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)


# =================================================================
# Word-level alignment lengkap (dengan continuity/majority/dsb)
# =================================================================
def align_speakers_with_transcription(whisper_segments, diar_segments) -> List[SpeakerSegment]:
    if not diar_segments:
        return [SpeakerSegment(s.start, s.end, s.text, AGENT_SPEAKER_LABEL, confidence=0.3)
                for s in whisper_segments]

    import bisect

    words = []
    for seg in whisper_segments:
        if hasattr(seg, "words") and seg.words:
            for w in seg.words:
                words.append({"start": w.start, "end": w.end, "word": w.word})
        else:
            words.append({"start": seg.start, "end": seg.end, "word": seg.text})

    if not words:
        return []

    sorted_diar  = sorted(diar_segments, key=lambda d: d["start"])
    diar_starts  = [d["start"] for d in sorted_diar]
    diar_ends    = [d["end"]   for d in sorted_diar]
    all_speakers = sorted(set(d["speaker"] for d in sorted_diar))
    spk_count    = len(all_speakers)
    customer_set = {c.lower().strip().rstrip(".,?! ") for c in CUSTOMER_ONLY_RESPONSES}

    logger.info("[ALIGN/word] %d kata dari %d segmen | diar=%d segs",
                len(words), len(whisper_segments), len(diar_segments))

    word_speakers: List[Tuple[dict, str, float]] = []
    prev_speaker     = AGENT_SPEAKER_LABEL
    last_anchor_end  = 0.0   # v5.6: waktu akhir anchor pyannote terakhir
    unmatched        = 0

    for w in words:
        best_speaker, max_overlap = None, 0.0

        # Layer 1: pyannote overlap
        lo = bisect.bisect_left(diar_ends,   w["start"])
        hi = bisect.bisect_right(diar_starts, w["end"])
        for i in range(lo, hi):
            d_seg   = sorted_diar[i]
            overlap = (
                min(w["end"], d_seg["end"])
                - max(w["start"], d_seg["start"])
            )
            if overlap > max_overlap:
                max_overlap  = overlap
                best_speaker = d_seg["speaker"]

        if best_speaker and max_overlap >= 0.1:
            conf = min(0.8, 0.6 + max_overlap)
            word_speakers.append((w, best_speaker, conf))
            prev_speaker    = best_speaker
            last_anchor_end = w["end"]
            continue
        elif best_speaker and max_overlap >= 0.01:
            word_speakers.append((w, best_speaker, 0.45))
            prev_speaker    = best_speaker
            last_anchor_end = w["end"]
            continue

        # Layer 2 (v5.6): nearest pyannote segment, radius DIPERLUAS.
        idx        = bisect.bisect_right(diar_starts, w["start"])
        candidates = sorted_diar[max(0, idx - 2): idx + 3]
        nearest_spk, nearest_dist = None, float("inf")
        for cand in candidates:
            dist = min(
                abs(w["start"] - cand["end"]),
                abs(w["end"]   - cand["start"]),
            )
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_spk  = cand["speaker"]

        if nearest_spk and nearest_dist <= 1.0:
            word_speakers.append((w, nearest_spk, 0.55))
            prev_speaker    = nearest_spk
            last_anchor_end = w["end"]
            continue
        elif nearest_spk and nearest_dist <= 5.0:
            word_speakers.append((w, nearest_spk, 0.35))
            prev_speaker    = nearest_spk
            last_anchor_end = w["end"]
            continue
        elif nearest_spk and nearest_dist <= 10.0:
            word_speakers.append((w, nearest_spk, 0.25))
            prev_speaker = nearest_spk
            continue

        # Layer 4 (v5.6): continuity TERBATAS, bukan tanpa batas.
        CONTINUITY_MAX_GAP = 2.0
        gap_from_last_anchor = w["start"] - last_anchor_end
        if gap_from_last_anchor <= CONTINUITY_MAX_GAP:
            best_speaker = prev_speaker
            conf = 0.20
        else:
            best_speaker = prev_speaker
            conf = 0.10
        word_speakers.append((w, best_speaker, conf))
        prev_speaker = best_speaker
        unmatched   += 1

    # Gabungkan kata berturutan → SpeakerSegment
    aligned:    List[SpeakerSegment] = []
    curr_spk    = None
    curr_start  = None
    curr_words: List[Tuple[dict, str, float]] = []

    for w, spk, conf in word_speakers:
        if spk != curr_spk:
            if curr_words:
                avg_conf = float(np.mean([c for _, _, c in curr_words]))
                aligned.append(SpeakerSegment(
                    curr_start,
                    curr_words[-1][0]["end"],
                    " ".join(x[0]["word"] for x in curr_words).strip(),
                    curr_spk,
                    confidence=avg_conf,
                ))
            curr_spk   = spk
            curr_start = w["start"]
            curr_words = [(w, spk, conf)]
        else:
            curr_words.append((w, spk, conf))

    if curr_words:
        avg_conf = float(np.mean([c for _, _, c in curr_words]))
        aligned.append(SpeakerSegment(
            curr_start,
            curr_words[-1][0]["end"],
            " ".join(x[0]["word"] for x in curr_words).strip(),
            curr_spk,
            confidence=avg_conf,
        ))

    logger.info(
        "[ALIGN/word] %d kata → %d segmen | pyannote anchor: %d/%d kata | unanchored: %d",
        len(words), len(aligned), len(words) - unmatched, len(words), unmatched,
    )
    return aligned


# =================================================================
# Filter rapid switches
# =================================================================
def filter_rapid_switches(
    aligned:     List[SpeakerSegment],
    min_gap_sec: float = 1.5,
) -> List[SpeakerSegment]:
    if not aligned:
        return aligned
    result   = [aligned[0]]
    prev_spk = aligned[0].speaker
    prev_end = aligned[0].end
    for seg in aligned[1:]:
        duration = seg.end - seg.start
        switched = seg.speaker != prev_spk
        if switched and duration < min_gap_sec:
            words      = seg.text.lower().strip().split()
            text_clean = seg.text.lower().strip().rstrip(".,?!")
            is_passive = (
                len(words) <= 2
                and all(w in _PASSIVE_CUSTOMER_RESPONSES for w in words)
            )
            if not is_passive and len(words) <= 3:
                is_passive = (
                    any(w in _PASSIVE_CUSTOMER_RESPONSES for w in words)
                    and len(text_clean) <= 20
                )
            is_customer_response = False
            if seg.speaker == CUSTOMER_SPEAKER_LABEL and len(words) <= 5:
                customer_lower = [c.lower().rstrip(".,?!") for c in CUSTOMER_ONLY_RESPONSES]
                is_customer_response = text_clean in customer_lower
            is_high_confidence = getattr(seg, '_confidence', 0.5) >= 0.75
            if not is_passive and not is_customer_response and not is_high_confidence:
                seg = SpeakerSegment(seg.start, seg.end, seg.text, prev_spk,
                                    confidence=getattr(seg, '_confidence', 0.5))
        result.append(seg)
        prev_spk = seg.speaker
        prev_end = seg.end
    return result


# =================================================================
# Mid-word split detection & merge (Pass 0/1/2)
# =================================================================
def _is_mid_word_split(prev_text: str, next_text: str, gap: float) -> bool:
    if gap > 0.05:
        return False
    prev_text = prev_text.rstrip()
    next_text = next_text.lstrip()
    if not prev_text or not next_text:
        return False
    last_word  = prev_text.split()[-1] if prev_text.split() else ""
    first_word = next_text.split()[0]  if next_text.split() else ""
    if not last_word or not first_word:
        return False

    last_clean = last_word.rstrip(".,!?;:")
    if not (1 <= len(last_clean) <= 3):
        return False
    if last_word != last_clean:
        return False
    if last_clean.lower() in VALID_SHORT_WORDS:
        return False
    if not first_word[0].islower():
        return False

    return True


def merge_consecutive_segments(
    aligned:     List[SpeakerSegment],
    max_gap_sec: float = 0.5,
) -> List[SpeakerSegment]:
    """
    Pass 0 (v5.7): gabung paksa mid-word splits LINTAS speaker.
    Pass 1: merge same-speaker (confidence-agnostic).
    Pass 2: absorb isolated blips (<0.8s, different from both neighbours).
    """
    if not aligned:
        return aligned

    # Pass 0: mid-word split repair (lintas speaker)
    repaired: List[SpeakerSegment] = [aligned[0]]
    for seg in aligned[1:]:
        prev = repaired[-1]
        gap  = seg.start - prev.end
        if _is_mid_word_split(prev.text, seg.text, gap):
            logger.info(
                "[Merge/mid-word] gabung '%s'+'%s' (gap=%.2fs, lintas speaker %s->%s)",
                prev.text.split()[-1] if prev.text.split() else "",
                seg.text.split()[0] if seg.text.split() else "",
                gap, prev.speaker, seg.speaker,
            )
            repaired[-1] = SpeakerSegment(
                prev.start, seg.end,
                prev.text.rstrip() + seg.text.lstrip(),  # TANPA spasi — sambung kata
                prev.speaker,  # pertahankan speaker segmen pertama
                confidence=max(getattr(prev, '_confidence', 0.5),
                                getattr(seg,  '_confidence', 0.5)),
            )
        else:
            repaired.append(seg)
    aligned = repaired

    # Pass 1
    merged = []
    i = 0
    while i < len(aligned):
        current = aligned[i]
        j = i + 1
        while j < len(aligned):
            nxt = aligned[j]
            gap = nxt.start - current.end
            if nxt.speaker == current.speaker and gap <= max_gap_sec:
                current = SpeakerSegment(
                    current.start, nxt.end,
                    current.text.rstrip() + " " + nxt.text.lstrip(),
                    current.speaker,
                    confidence=max(
                        getattr(current, '_confidence', 0.5),
                        getattr(nxt,     '_confidence', 0.5),
                    ),
                )
                j += 1
            else:
                break
        merged.append(current)
        i = j

    # Pass 2: blip absorption
    ABSORB = 0.8
    if len(merged) >= 3:
        absorbed = [merged[0]]
        for k in range(1, len(merged) - 1):
            seg  = merged[k]
            prev = absorbed[-1]
            nxt  = merged[k + 1]
            dur  = seg.end - seg.start
            conf = getattr(seg, '_confidence', 0.5)
            is_blip = (
                dur < ABSORB
                and seg.speaker != prev.speaker
                and seg.speaker != nxt.speaker
                and conf < 0.75
            )
            if is_blip:
                if seg.start - prev.end <= nxt.start - seg.end:
                    absorbed[-1] = SpeakerSegment(
                        prev.start, seg.end,
                        prev.text.rstrip() + " " + seg.text.lstrip(),
                        prev.speaker,
                        confidence=max(getattr(prev, '_confidence', 0.5), conf),
                    )
                else:
                    merged[k + 1] = SpeakerSegment(
                        seg.start, nxt.end,
                        seg.text.rstrip() + " " + nxt.text.lstrip(),
                        nxt.speaker,
                        confidence=max(conf, getattr(nxt, '_confidence', 0.5)),
                    )
            else:
                absorbed.append(seg)
        absorbed.append(merged[-1])
        merged = absorbed

    if len(merged) < len(aligned):
        reduction_pct = (1 - len(merged) / len(aligned)) * 100
        logger.info("[Merge] %d segmen → %d segmen (%.1f%% reduction).",
                    len(aligned), len(merged), reduction_pct)
    return merged


# =================================================================
# Anchor agent by opener
# =================================================================
def anchor_agent_by_opener(
    aligned: List[SpeakerSegment],
) -> Tuple[List[SpeakerSegment], bool]:
    early_segs = [s for s in aligned if s.start <= 30.0]
    speaker_1_has_opener = any(
        any(phrase in seg.text.lower() for phrase in AGENT_OPENER_PHRASES)
        for seg in early_segs if seg.speaker == CUSTOMER_SPEAKER_LABEL
    )
    if not speaker_1_has_opener:
        return aligned, False
    flipped = [
        SpeakerSegment(
            seg.start, seg.end, seg.text,
            CUSTOMER_SPEAKER_LABEL if seg.speaker == AGENT_SPEAKER_LABEL else AGENT_SPEAKER_LABEL,
            confidence=getattr(seg, '_confidence', 0.5),
        )
        for seg in aligned
    ]
    logger.info("[Anchor] SPEAKER flip — NASABAH bicara sebagai agen di awal.")
    return flipped, True


# =================================================================
# QA pattern splitter
# =================================================================
def _strip_proper_nouns(text: str) -> str:
    words  = text.split()
    result = []
    for w in words:
        core = w.strip(".,?!;:")
        is_capitalized = len(core) >= 3 and core[0].isupper()
        is_not_common  = core.lower() not in NOT_PROPER_NOUNS
        is_reasonable  = 3 <= len(core) <= 20 and core.replace("-", "").isalpha()
        if is_capitalized and is_not_common and is_reasonable:
            result.append("[NAMA]")
        else:
            result.append(w)
    return " ".join(result)


def _detect_generic_qa_splits(text: str, tlow: str, existing_splits: list) -> list:
    results = []
    existing_ranges = [(s[0], s[1]) for s in existing_splits]

    search_pos = 0
    while True:
        q_idx = text.find("?", search_pos)
        if q_idx == -1:
            break
        search_pos = q_idx + 1

        if any(lo <= q_idx < hi for lo, hi in existing_ranges):
            continue

        after_raw    = text[q_idx + 1: q_idx + 1 + 60]
        lead_ws      = len(after_raw) - len(after_raw.lstrip())
        after_base   = q_idx + 1 + lead_ws
        after        = after_raw.strip()
        if not after:
            continue
        words = after.split()
        if not words:
            continue

        candidate_words = []
        for w in words[:6]:
            if any(w.lower().startswith(m.strip()) for m in AGENT_RESUME_MARKERS):
                break
            candidate_words.append(w)
            if w.endswith((".", "?", "!")) and len(candidate_words) >= 1:
                break

        if not candidate_words:
            continue

        candidate_text = " ".join(candidate_words).strip()
        cand_words_clean = [w.lower().rstrip(".,!?;:") for w in candidate_words]

        has_agent_doc = any(w in AGENT_DOCUMENT_WORDS for w in cand_words_clean)
        if has_agent_doc:
            continue

        first_w = cand_words_clean[0] if cand_words_clean else ""
        ends_with_question = candidate_text.rstrip().endswith("?")
        if first_w in AGENT_QUESTION_STARTERS and ends_with_question:
            continue

        if ends_with_question and len(candidate_words) >= 3:
            continue

        if len(candidate_words) > 6:
            continue

        if len(candidate_text) > 35:
            continue

        a_start = after_base + after.index(candidate_words[0])
        last_word = candidate_words[-1]
        last_rel  = after.rindex(last_word)
        a_end     = after_base + last_rel + len(last_word)

        sliced = text[a_start:a_end].strip()
        if sliced != candidate_text:
            logger.debug(
                "[QASplit/generic] slice mismatch, skip: sliced='%s' vs cand='%s'",
                sliced, candidate_text,
            )
            continue

        if any(not (a_end <= lo or a_start >= hi) for lo, hi in existing_ranges):
            continue

        results.append((q_idx + 1, a_end, candidate_text, "generic"))
        existing_ranges.append((q_idx + 1, a_end))

    return results


def split_embedded_customer_responses(
    aligned: List[SpeakerSegment]
) -> List[SpeakerSegment]:
    SPLIT_MIN_DUR = 3.0
    result: List[SpeakerSegment] = []
    for seg in aligned:
        if seg.speaker != AGENT_SPEAKER_LABEL or (seg.end - seg.start) < SPLIT_MIN_DUR:
            result.append(seg)
            continue
        text  = seg.text.strip()
        tlow  = _strip_proper_nouns(text).lower()
        splits = []
        for q_pat, a_pat in QA_INLINE_PATTERNS:
            q_idx = tlow.find(q_pat)
            if q_idx == -1:
                continue
            search_start = q_idx + len(q_pat)
            effective_start = search_start
            mini_window = tlow[search_start: search_start + 10]
            q_mark_idx  = mini_window.find("?")
            if q_mark_idx != -1:
                effective_start = search_start + q_mark_idx + 1
            window       = tlow[effective_start: effective_start + 80]
            a_idx_rel    = window.find(a_pat)
            if a_idx_rel == -1:
                window    = tlow[search_start: search_start + 80]
                a_idx_rel = window.find(a_pat)
                if a_idx_rel == -1:
                    continue
                a_start = search_start + a_idx_rel
            else:
                a_start = effective_start + a_idx_rel
            a_end   = a_start + len(a_pat)
            splits.append((q_idx + len(q_pat), a_end, a_pat, "pattern"))

        generic_splits = _detect_generic_qa_splits(text, tlow, splits)
        splits.extend(generic_splits)

        splits.sort(key=lambda x: x[0])
        deduped = []
        for sp in splits:
            if not deduped or sp[0] >= deduped[-1][1]:
                deduped.append(sp)
        splits = deduped
        if not splits:
            result.append(seg)
            continue
        total_dur   = seg.end - seg.start
        total_chars = max(len(text), 1)
        prev_char   = 0
        for q_end, a_end, a_pat, src_type in splits:
            agent_text = text[prev_char:q_end].strip()
            if agent_text:
                t_s = seg.start + total_dur * (prev_char / total_chars)
                t_e = seg.start + total_dur * (q_end   / total_chars)
                result.append(SpeakerSegment(t_s, t_e, agent_text, AGENT_SPEAKER_LABEL, 0.6))
            cust_text  = text[q_end:a_end].strip().lstrip("?,. ")
            if src_type == "pattern":
                cust_words = cust_text.split()
                for skip in range(min(3, len(cust_words))):
                    cand_low = " ".join(cust_words[skip:]).lower().lstrip("?,. ")
                    for _, ap in QA_INLINE_PATTERNS:
                        if cand_low.startswith(ap):
                            cust_text = " ".join(cust_words[skip:])
                            break
                    else:
                        continue
                    break
            cust_text = cust_text.strip().lstrip("?,. ")
            if cust_text:
                t_s  = seg.start + total_dur * (q_end / total_chars)
                t_e  = seg.start + total_dur * (a_end / total_chars)
                conf = 0.75 if src_type == "pattern" else 0.55
                result.append(SpeakerSegment(t_s, t_e, cust_text, CUSTOMER_SPEAKER_LABEL, conf))
                tag = "QASplit" if src_type == "pattern" else "QASplit/generic"
                logger.info("[%s] %.2f-%.2f AGENT_BM→NASABAH | '%s'",
                            tag, t_s, t_e, cust_text[:40])
            prev_char = a_end
        remaining = text[prev_char:].strip()
        if remaining:
            t_s = seg.start + total_dur * (prev_char / total_chars)
            result.append(SpeakerSegment(t_s, seg.end, remaining, AGENT_SPEAKER_LABEL, 0.6))
    return result


# =================================================================
# Lexical reattribution (anti-domino, block-aware, majority window)
# =================================================================
def detect_reading_blocks(aligned: List[SpeakerSegment]) -> set:
    """
    Deteksi 'reading block': >=4 segmen pendek (<MIN_SEGMENT_DUR) berurutan
    dengan speaker ASLI yang sama. Indeks segmen dalam block dikembalikan
    agar duration_guard skip seluruh block (mencegah efek domino).
    """
    reading_idx: set = set()
    n = len(aligned)
    i = 0
    while i < n:
        spk = aligned[i].speaker
        j   = i
        while (j < n
               and aligned[j].speaker == spk
               and (aligned[j].end - aligned[j].start) < MIN_SEGMENT_DUR_SEC):
            j += 1
        run_len = j - i
        if run_len >= 4:
            for k in range(i, j):
                reading_idx.add(k)
        i = j if j > i else i + 1
    return reading_idx


def lexical_reattribute(
    aligned: List[SpeakerSegment]
) -> Tuple[List[SpeakerSegment], int]:
    corrected:          List[SpeakerSegment] = []
    reattributed_count: int                  = 0
    forbidden_lower = [w.lower() for w in FORBIDDEN_CUSTOMER_WORDS]

    reading_blocks = detect_reading_blocks(aligned)
    if reading_blocks:
        logger.info("[Lexical] %d segmen terdeteksi sebagai reading-block (skip flip).",
                    len(reading_blocks))

    original_speakers = [s.speaker for s in aligned]

    for idx, seg in enumerate(aligned):
        text_lower  = seg.text.lower().strip()
        new_speaker = seg.speaker
        reason      = None

        if seg.speaker == CUSTOMER_SPEAKER_LABEL:
            for forbidden in forbidden_lower:
                if forbidden in text_lower:
                    new_speaker = AGENT_SPEAKER_LABEL
                    reason      = "lexical:%s" % forbidden
                    break

        if (
            new_speaker == AGENT_SPEAKER_LABEL
            and idx > 0
            and (seg.end - seg.start) < 2.0
        ):
            customer_lower = [w.lower() for w in CUSTOMER_ONLY_RESPONSES]
            text_stripped  = text_lower.strip().rstrip(".,?!")
            for cw in customer_lower:
                if text_stripped == cw or text_stripped == cw.rstrip(".,?!"):
                    new_speaker = CUSTOMER_SPEAKER_LABEL
                    reason      = "customer_response:%s" % cw
                    break

        if (
            new_speaker == seg.speaker
            and idx > 0
            and (seg.end - seg.start) < MIN_SEGMENT_DUR_SEC
        ):
            words = text_lower.split()
            clean = text_lower.replace(",", "").replace(".", "").replace("?", "").strip()

            is_passive = (
                len(words) <= 2
                and all(w in _PASSIVE_CUSTOMER_RESPONSES for w in words)
            )
            if not is_passive and len(words) <= 3 and len(clean) <= 20:
                is_passive = any(w in _PASSIVE_CUSTOMER_RESPONSES for w in words)

            is_agent_document = any(
                w.rstrip(".,!?") in AGENT_DOCUMENT_WORDS for w in words
            )

            is_reading_block = idx in reading_blocks

            lo = max(0, idx - 3)
            hi = min(len(original_speakers), idx + 4)
            window = original_speakers[lo:idx] + original_speakers[idx+1:hi]
            if window:
                same_count  = sum(1 for s in window if s == seg.speaker)
                majority_same = same_count > len(window) / 2
            else:
                majority_same = False

            prev_original = original_speakers[idx - 1] if idx > 0 else seg.speaker
            next_original = (original_speakers[idx + 1]
                             if idx + 1 < len(original_speakers) else seg.speaker)
            is_sandwiched_by_same = (
                prev_original == seg.speaker
                or next_original == seg.speaker
            )

            is_customer_question = (
                seg.speaker == CUSTOMER_SPEAKER_LABEL
                and any(qw in text_lower for qw in CUSTOMER_QUESTION_WORDS)
            )

            if (not is_passive
                    and not is_agent_document
                    and not is_reading_block
                    and not majority_same
                    and not is_sandwiched_by_same
                    and not is_customer_question):
                if prev_original != seg.speaker:
                    new_speaker = prev_original
                    reason      = "duration_guard:%.2fs" % (seg.end - seg.start)

        if new_speaker != seg.speaker:
            reattributed_count += 1
            logger.info(
                "[Lexical] Koreksi [%.2f-%.2f] %s→%s | '%s' | %s",
                seg.start, seg.end, seg.speaker, new_speaker,
                seg.text.strip()[:60], reason,
            )
            corrected.append(SpeakerSegment(
                seg.start, seg.end, seg.text, new_speaker, confidence=0.75))
        else:
            corrected.append(SpeakerSegment(
                seg.start, seg.end, seg.text, seg.speaker,
                confidence=getattr(seg, '_confidence', 0.5)))

    if reattributed_count:
        logger.info("[Lexical] Total koreksi: %d/%d segmen.",
                    reattributed_count, len(aligned))
    else:
        logger.info("[Lexical] Tidak ada koreksi diperlukan.")
    return corrected, reattributed_count


class LegacyHeuristicAlignmentStrategy(AlignmentStrategy):
    """
    Strategi LEGACY — TIDAK dipanggil pipeline aktif (lihat docstring
    modul). Orkestrasi berikut adalah urutan yang tersirat dari kode asli
    (word-level align -> merge -> anchor -> QA split -> lexical
    reattribution), disediakan untuk kompatibilitas/referensi.
    """

    name = "legacy_heuristic"

    def align(self, whisper_segments, diar_segments, audio_path=None):
        aligned = align_speakers_with_transcription(whisper_segments, diar_segments)
        aligned = merge_consecutive_segments(aligned)
        aligned, _ = anchor_agent_by_opener(aligned)
        aligned = split_embedded_customer_responses(aligned)
        aligned, reattributed_count = lexical_reattribute(aligned)
        return aligned, reattributed_count
