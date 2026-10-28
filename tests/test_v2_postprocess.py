"""collapse_to_two, smooth_single_word_flips, group_utterances."""
from model.speech_stt.diarization_v2.postprocess import (
    collapse_to_two,
    group_utterances,
    smooth_single_word_flips,
)
from model.speech_stt.diarization_v2.types import Word


def _words(spec):
    """spec: list of (speaker, start, end) -> Word dengan teks w0, w1, ..."""
    return [Word("w%d" % i, s, e, spk) for i, (spk, s, e) in enumerate(spec)]


def _spk(words):
    return [w.speaker for w in words]


# ── collapse_to_two ──

def test_collapse_noop_bila_maks_dua_speaker():
    ws = _words([("a", 0, 1), ("b", 1.1, 2), ("a", 2.1, 3)])
    out, n = collapse_to_two(ws)
    assert _spk(out) == ["a", "b", "a"] and n == 0


def test_collapse_run_minor_ikut_tetangga_terdekat():
    ws = _words([
        ("a", 0.0, 0.5), ("a", 0.6, 1.0), ("a", 1.1, 1.5),
        ("c", 1.6, 2.0), ("c", 2.1, 2.5),          # gap ke a = 0.1, ke b = 2.0
        ("b", 4.5, 5.0), ("b", 5.1, 5.5), ("b", 5.6, 6.0), ("b", 6.1, 6.5),
        ("c", 7.9, 8.0),                            # gap ke b = 1.4, ke a = 0.1
        ("a", 8.1, 8.5),
    ])
    out, n = collapse_to_two(ws)
    assert _spk(out) == ["a", "a", "a", "a", "a", "b", "b", "b", "b", "a", "a"]
    assert n == 3


def test_collapse_run_di_awal_dan_seri_ikut_sebelumnya():
    ws = _words([("c", 0, 0.5), ("a", 0.6, 1), ("a", 1.1, 2), ("a", 2.05, 2.08),
                 ("b", 2.1, 3), ("b", 3.1, 3.5), ("b", 3.6, 4),
                 ("c", 4.5, 5), ("a", 5.5, 6)])
    out, _ = collapse_to_two(ws)
    # c pertama: tanpa tetangga kiri -> a. c terakhir: jeda 0,5 = 0,5 (seri) -> sebelumnya (b).
    assert _spk(out) == ["a", "a", "a", "a", "b", "b", "b", "b", "a"]


def test_collapse_tidak_mengubah_input():
    ws = _words([("a", 0, 1), ("b", 1.1, 2), ("c", 2.1, 3), ("a", 3.1, 4), ("b", 4.1, 5)])
    collapse_to_two(ws)
    assert _spk(ws) == ["a", "b", "c", "a", "b"]


# ── smooth_single_word_flips ──

def test_flip_tanpa_jeda_dihaluskan():
    ws = _words([("a", 0.0, 0.3), ("b", 0.35, 0.6), ("a", 0.65, 0.9)])
    out, n = smooth_single_word_flips(ws)
    assert _spk(out) == ["a", "a", "a"] and n == 1


def test_backchannel_berjeda_dipertahankan():
    ws = _words([("a", 0.0, 0.3), ("b", 0.8, 1.0), ("a", 1.05, 1.3)])  # jeda sebelum 0.5 s
    out, n = smooth_single_word_flips(ws)
    assert _spk(out) == ["a", "b", "a"] and n == 0


def test_dua_kata_beruntun_bukan_flip_satu_kata():
    ws = _words([("a", 0.0, 0.3), ("b", 0.35, 0.6), ("b", 0.65, 0.9), ("a", 0.95, 1.2)])
    out, n = smooth_single_word_flips(ws)
    assert _spk(out) == ["a", "b", "b", "a"] and n == 0


def test_kata_pertama_dan_terakhir_tidak_disentuh():
    ws = _words([("b", 0.0, 0.3), ("a", 0.35, 0.6), ("a", 0.65, 0.9), ("b", 0.95, 1.2)])
    out, n = smooth_single_word_flips(ws)
    assert _spk(out) == ["b", "a", "a", "b"] and n == 0


def test_smoothing_kiri_ke_kanan_pada_pola_berselang():
    ws = _words([("a", 0, 0.3), ("b", 0.35, 0.6), ("a", 0.65, 0.9), ("b", 0.95, 1.2), ("a", 1.25, 1.5)])
    out, n = smooth_single_word_flips(ws)
    assert _spk(out) == ["a", "a", "a", "a", "a"] and n == 2


# ── group_utterances ──

def test_group_pecah_saat_ganti_speaker_atau_jeda_panjang():
    ws = _words([("a", 0, 0.3), ("a", 0.4, 0.7), ("a", 1.5, 1.8), ("b", 1.9, 2.2)])
    utts = group_utterances(ws)
    assert [(u.id, u.speaker, u.text, u.start, u.end) for u in utts] == [
        (0, "a", "w0 w1", 0, 0.7), (1, "a", "w2", 1.5, 1.8), (2, "b", "w3", 1.9, 2.2),
    ]
    assert [len(u.words) for u in utts] == [2, 1, 1]


def test_group_kosong():
    assert group_utterances([]) == []
