"""
Metrik proxy (tanpa ground truth): jalur LAMA vs v2 pada raw response ter-cache.

    python -m tools.diar_eval.proxy_metrics [--labels stems.json] [--per-call out.csv]

Referensi role yang dipakai:
  cue_appb   — algoritme fix_speaker_roles App B (intro di 8 utterance pertama,
               fallback frasa skrip; seri/kosong = tidak diputuskan) atas label
               mentah Scribe. INDEPENDEN dari aturan lama, tetapi SIRKULAR
               terhadap v2 (v2 memakai cue yang sama) — angka v2-vs-cue
               bukan bukti akurasi; ukuran sebenarnya adalah GT (Fase 4).
  dominant   — label dengan kata terbanyak. Independen dari cue.

Output hanya angka; tidak ada teks transkrip yang dicetak/ditulis.
"""
import argparse
import csv
import json
import statistics
from collections import Counter
from types import SimpleNamespace
from typing import Dict, List, Optional

from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt.diarization_v2.pipeline import DEFAULT_PARAMS, V2Params, run_v2
from model.speech_stt.diarization_v2.postprocess import group_utterances
from model.speech_stt.diarization_v2.provider import CachedRawResponseProvider, words_from_raw
from model.speech_stt.diarization_v2.roles import load_cues
from model.speech_stt.segment import map_speakers_to_binary
from tools.diar_eval.cache import cache_dir, iter_entries


def legacy_segments(raw: Dict) -> List[Dict]:
    """Segmen berlabel Agent/Customer menurut jalur lama, memakai fungsi lama apa adanya."""
    resp = SimpleNamespace(words=[SimpleNamespace(**w) for w in raw.get("words") or []])
    return map_speakers_to_binary(ElevenLabsBackend._extract_segments_with_speaker(resp))


def cue_appb(utterances, cues) -> Optional[str]:
    """Agent menurut algoritme App B; None = tidak diputuskan (tanpa cue / seri)."""
    hits = Counter()
    for u in utterances[:cues["intro_window"]]:
        if any(p.search(u.text) for p in cues["intro"]):
            hits[u.speaker] += 1
    if not hits:
        for u in utterances:
            if any(p.search(u.text) for p in cues["script"]):
                hits[u.speaker] += 1
    ranked = hits.most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def _agent_cue_under_customer(segments: List[Dict], cues) -> bool:
    pats = cues["intro"] + cues["script"]
    return any(s["speaker"] == "Customer" and any(p.search(s["text"]) for p in pats) for s in segments)


def evaluate_call(raw: Dict, params: V2Params = DEFAULT_PARAMS, cues=None) -> Optional[Dict]:
    cues = cues or load_cues()
    words = words_from_raw(raw)
    if not words:
        return None
    raw_utts = group_utterances(words)
    n_words = Counter(w.speaker for w in words)
    dominant = max(n_words, key=lambda s: (n_words[s], -list(n_words).index(s)))
    _, v2_segs, _, meta = run_v2(CachedRawResponseProvider(raw).diarize(), params)

    old_segs = legacy_segments(raw)
    old = next((s["speaker_original"] for s in old_segs if s["speaker"] == "Agent"), None)
    return {
        "n_words": len(words),
        "n_spk_raw": len(n_words),
        "legacy_agent": old,
        "v2_agent": meta["agent_raw"],
        "cue_appb": cue_appb(raw_utts, cues),
        "dominant": dominant,
        "v2_basis": meta["role_basis"],
        "v2_confident": meta["role_confident"],
        "n_smoothed": meta["n_smoothed"],
        "n_collapsed": meta["n_collapsed"],
        "legacy_cue_under_customer": _agent_cue_under_customer(old_segs, cues),
        "v2_cue_under_customer": _agent_cue_under_customer(v2_segs, cues),
    }


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def _vs(rows, key, ref):
    decided = [r for r in rows if r[ref] is not None]
    return {"n_decided": len(decided), "agree_pct": _pct(sum(r[key] == r[ref] for r in decided), len(decided)),
            "disagree_pct": _pct(sum(r[key] != r[ref] for r in decided), len(decided))}


def aggregate(rows: List[Dict]) -> Dict:
    n = len(rows)
    undecided = [r for r in rows if r["cue_appb"] is None]
    smooth = [100.0 * r["n_smoothed"] / r["n_words"] for r in rows]
    return {
        "n_calls": n,
        "legacy_vs_cue_appb": _vs(rows, "legacy_agent", "cue_appb"),
        "v2_vs_cue_appb_SIRKULAR": _vs(rows, "v2_agent", "cue_appb"),
        "legacy_vs_dominant": _vs(rows, "legacy_agent", "dominant"),
        "v2_vs_dominant": _vs(rows, "v2_agent", "dominant"),
        "v2_changed_vs_legacy_pct": _pct(sum(r["v2_agent"] != r["legacy_agent"] for r in rows), n),
        "cue_undecided": {"n": len(undecided), "pct": _pct(len(undecided), n),
                          "v2_vs_legacy_disagree_pct": _pct(sum(r["v2_agent"] != r["legacy_agent"] for r in undecided),
                                                            len(undecided))},
        "v2_basis_pct": {k: _pct(v, n) for k, v in Counter(r["v2_basis"] for r in rows).most_common()},
        "v2_role_confident_pct": _pct(sum(r["v2_confident"] for r in rows), n),
        "agent_cue_under_customer_pct": {"legacy": _pct(sum(r["legacy_cue_under_customer"] for r in rows), n),
                                         "v2": _pct(sum(r["v2_cue_under_customer"] for r in rows), n)},
        "smoothed_per_100_words": {"median": round(statistics.median(smooth), 3) if smooth else None,
                                   "calls_with_any_pct": _pct(sum(r["n_smoothed"] > 0 for r in rows), n)},
        "calls_with_collapse_pct": _pct(sum(r["n_collapsed"] > 0 for r in rows), n),
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir")
    ap.add_argument("--labels", help="JSON {stem: label} untuk rincian per kelompok (mis. NQ/PASS)")
    ap.add_argument("--per-call", help="CSV per call (tanpa teks); simpan DI LUAR repo")
    a = ap.parse_args(argv)
    labels = json.load(open(a.labels)) if a.labels else {}
    cues = load_cues()
    rows = []
    for e in iter_entries(cache_dir(a.cache_dir)):
        r = evaluate_call(e["raw"] or {}, cues=cues)
        if r:
            rows.append({"stem": e["stem"], "label": labels.get(e["stem"], "-"), **r})
    report = {"all": aggregate(rows)}
    for lab in sorted({r["label"] for r in rows} - {"-"}):
        report[lab] = aggregate([r for r in rows if r["label"] == lab])
    if a.per_call:
        with open(a.per_call, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
