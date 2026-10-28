"""
Post-processing kata Scribe sebelum role assignment (Fase 2).

Urutan di pipeline: collapse_to_two -> smooth_single_word_flips ->
group_utterances. Semua fungsi murni (tidak mengubah input).
"""
from collections import Counter
from typing import List, Tuple

from model.speech_stt.diarization_v2.types import Utterance, Word

SMOOTH_MAX_GAP_SEC = 0.25
UTTERANCE_MAX_GAP_SEC = 0.6


def _relabel(w: Word, speaker) -> Word:
    return Word(w.text, w.start, w.end, speaker)


def collapse_to_two(words: List[Word]) -> Tuple[List[Word], int]:
    """
    Speaker di luar 2 terbesar (jumlah kata) dilebur ke speaker dominan
    terdekat dalam waktu. Diproses per RUN kata berurutan supaya satu
    potongan ucapan tidak terbelah. Seri jarak -> ikut speaker sebelumnya.
    Return (words, jumlah kata yang dilabel ulang).
    """
    counts = Counter(w.speaker for w in words)
    if len(counts) <= 2:
        return list(words), 0
    keep = {s for s, _ in counts.most_common(2)}
    out = list(words)
    n = 0
    i = 0
    while i < len(out):
        if out[i].speaker in keep:
            i += 1
            continue
        j = i
        while j < len(out) and out[j].speaker not in keep:
            j += 1
        prev = out[i - 1] if i > 0 else None
        nxt = out[j] if j < len(out) else None
        gap_prev = out[i].start - prev.end if prev else float("inf")
        gap_next = nxt.start - out[j - 1].end if nxt else float("inf")
        target = prev.speaker if gap_prev <= gap_next else nxt.speaker
        for k in range(i, j):
            out[k] = _relabel(out[k], target)
            n += 1
        i = j
    return out, n


def smooth_single_word_flips(words: List[Word], max_gap_sec: float = SMOOTH_MAX_GAP_SEC) -> Tuple[List[Word], int]:
    """
    Pola A-B-A di mana B satu kata dan KEDUA jedanya < max_gap_sec -> B jadi A
    (flip label tanpa jeda di tengah kalimat). Bila salah satu jeda >= max_gap_sec,
    B dianggap backchannel ("iya", "hmm") dan DIPERTAHANKAN. Diproses kiri ke
    kanan: tetangga kiri memakai label yang SUDAH dihaluskan, tetangga kanan
    label asli (A-B-A-B-A -> A-A-A-A-A).
    """
    out = list(words)
    n = 0
    for k in range(1, len(words) - 1):
        a, b, c = out[k - 1], words[k], words[k + 1]
        if b.speaker != a.speaker and a.speaker == c.speaker \
                and b.start - a.end < max_gap_sec and c.start - b.end < max_gap_sec:
            out[k] = _relabel(b, a.speaker)
            n += 1
    return out, n


def group_utterances(words: List[Word], max_gap_sec: float = UTTERANCE_MAX_GAP_SEC) -> List[Utterance]:
    """Utterance baru saat speaker berganti ATAU jeda > max_gap_sec (setara segmen PDF yang dibaca App B)."""
    utts: List[Utterance] = []
    for w in words:
        last = utts[-1] if utts else None
        if last and last.speaker == w.speaker and w.start - last.end <= max_gap_sec:
            last.end = w.end
            last.text += " " + w.text
            last.words.append(w)
        else:
            utts.append(Utterance(len(utts), w.start, w.end, w.speaker, w.text, [w]))
    return utts
