"""
Sampling terstratifikasi call untuk ground truth (Fase 4).

    python -m tools.diar_eval.sample --labels stems.json --label NQ \\
        --out /luar/repo/gt_sample.json [--per-stratum 10] [--seed 20261028] \\
        [--max-duration 900] [--double 5] [--extra-strata undecided]

Strata (eksklusif, dicek berurutan; nama mengikuti docs/diarization-audit.md §5):
  swap        — jalur lama berlawanan dengan cue App B (role tertukar)
  cue_2label  — frasa skrip agent muncul di >= 2 label mentah Scribe (S7)
  minor_low   — speaker minoritas <= MINOR_WORDS_MAX kata (indikasi merge)
  undecided   — (opsional, --extra-strata) cue tidak ada / seri
  normal      — sisanya, cue App B sepakat dengan jalur lama

`labels`: JSON {stem: label}, mis. hasil ai_status_map App B (NQ/PASS).
Output hanya stem, result_id, dan angka — tanpa teks — tetapi tetap simpan di
luar repo (stem = identitas tiket).
"""
import argparse
import json
import random
from typing import Dict, List, Optional

from model.speech_stt.diarization_v2.roles import load_cues
from tools.diar_eval.cache import cache_dir, iter_entries
from tools.diar_eval.proxy_metrics import evaluate_call

MINOR_WORDS_MAX = 31  # p10 data Cashline (audit §5.2)
BASE_STRATA = ("swap", "cue_2label", "minor_low", "normal")
EXTRA_STRATA = ("undecided",)


def stratum(m: Dict, extra=()) -> str:
    if m["cue_appb"] is not None and m["legacy_agent"] != m["cue_appb"]:
        return "swap"
    if m["cue_raw_labels"] >= 2:
        return "cue_2label"
    if m["minor_words"] <= MINOR_WORDS_MAX:
        return "minor_low"
    if m["cue_appb"] is None:
        return "undecided" if "undecided" in extra else "excluded"
    return "normal"


def draw(rows: List[Dict], per_stratum: int, seed: int, strata, double: int) -> List[Dict]:
    rng = random.Random(seed)
    picked: List[Dict] = []
    for s in strata:
        pool = sorted((r for r in rows if r["stratum"] == s), key=lambda r: r["stem"])
        picked += rng.sample(pool, min(per_stratum, len(pool)))
    for r in rng.sample(picked, min(double, len(picked))):
        r["double_label"] = True
    return picked


def build_rows(entries, labels: Dict[str, str], label: Optional[str], max_duration: Optional[float], extra=()):
    cues = load_cues()
    rows = []
    for e in entries:
        if label and labels.get(e["stem"]) != label:
            continue
        m = evaluate_call(e["raw"] or {}, cues=cues)
        if not m or (max_duration and (m["duration_sec"] or 0) > max_duration):
            continue
        rows.append({"stem": e["stem"], "result_ids": e.get("result_ids", []), "stratum": stratum(m, extra),
                     "double_label": False,
                     **{k: m[k] for k in ("duration_sec", "n_words", "minor_words", "cue_raw_labels",
                                          "legacy_agent", "cue_appb", "dominant", "v2_agent", "v2_basis")}})
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--label", default="NQ")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-stratum", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--max-duration", type=float, default=None, help="detik; batasi beban pelabelan")
    ap.add_argument("--double", type=int, default=5, help="jumlah call yang dilabeli 2 orang")
    ap.add_argument("--extra-strata", nargs="*", default=[], choices=EXTRA_STRATA)
    a = ap.parse_args(argv)
    labels = json.load(open(a.labels))
    rows = build_rows(iter_entries(cache_dir(a.cache_dir)), labels, a.label, a.max_duration, a.extra_strata)
    strata = BASE_STRATA[:-1] + tuple(a.extra_strata) + BASE_STRATA[-1:]
    picked = draw(rows, a.per_stratum, a.seed, strata, a.double)
    pop = {s: sum(r["stratum"] == s for r in rows) for s in strata + ("excluded",)}
    out = {"seed": a.seed, "label": a.label, "population": pop, "n_sample": len(picked),
           "audio_hours": round(sum(r["duration_sec"] or 0 for r in picked) / 3600, 2), "calls": picked}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "calls"}, indent=1))


if __name__ == "__main__":
    main()
