"""
Grid search parameter post-processing v2 terhadap ground truth (tanpa API).

    python -m tools.diar_eval.grid --gt-dir /path/gt [--cache-dir ...] [--top 10]

GT: satu file JSON per call (format di tools/diar_eval/metrics.py), dicocokkan
ke cache lewat `stem`. Baseline jalur lama selalu ikut dilaporkan.
Peringkat utama: role accuracy (pooled, berbobot waktu), lalu DER pooled.
"""
import argparse
import itertools
import json
from pathlib import Path
from typing import Dict, List

from model.speech_stt.diarization_v2.pipeline import V2Params, run_v2
from model.speech_stt.diarization_v2.provider import CachedRawResponseProvider
from tools.diar_eval.cache import cache_dir, iter_entries
from tools.diar_eval.metrics import der_metric, role_durations, to_annotation
from tools.diar_eval.proxy_metrics import legacy_segments

GRID = {
    "smooth_max_gap_sec": [0.15, 0.25, 0.35],
    "utterance_max_gap_sec": [0.4, 0.6, 0.8],
    "role_min_margin": [1, 2],
    "role_strong_margin": [2, 3],
}


def load_gt(gt_dir: str) -> Dict[str, Dict]:
    return {g["stem"]: g for g in (json.loads(p.read_text()) for p in sorted(Path(gt_dir).glob("*.json")))}


def v2_segments(raw: Dict, params: V2Params) -> List[Dict]:
    _, _, _, meta = run_v2(CachedRawResponseProvider(raw).diarize(), params)
    return [{"start": s, "end": e, "speaker": spk} for s, e, spk in meta["segments"]]


def score(pairs, hyp_fn) -> Dict[str, float]:
    """pairs: [(gt, raw)]. Return role accuracy & DER pooled atas semua call."""
    metric = der_metric()
    correct = total = 0.0
    for gt, raw in pairs:
        hyp = hyp_fn(raw)
        d = role_durations(gt["segments"], hyp)
        correct += d["correct_sec"]
        total += d["total_sec"]
        metric(to_annotation(gt["segments"]), to_annotation(hyp))
    return {"role_accuracy": correct / total if total else float("nan"), "der": abs(metric), "n_calls": len(pairs)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt-dir", required=True)
    ap.add_argument("--cache-dir")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    gt = load_gt(a.gt_dir)
    pairs = [(gt[e["stem"]], e["raw"]) for e in iter_entries(cache_dir(a.cache_dir)) if e["stem"] in gt and e["raw"]]
    missing = sorted(set(gt) - {g["stem"] for g, _ in pairs})
    if not pairs:
        raise SystemExit("Tidak ada call GT yang ada di cache.")

    def legacy(raw):
        return [{"start": s["start"], "end": s["end"], "speaker": s["speaker"]} for s in legacy_segments(raw)]

    results = []
    for combo in itertools.product(*GRID.values()):
        params = V2Params(**dict(zip(GRID, combo)))
        results.append({"params": dict(zip(GRID, combo)), **score(pairs, lambda raw: v2_segments(raw, params))})
    results.sort(key=lambda r: (-r["role_accuracy"], r["der"]))
    print(json.dumps({"n_gt": len(gt), "missing_in_cache": missing,
                      "legacy": score(pairs, legacy), "v2_top": results[:a.top]}, indent=1))


if __name__ == "__main__":
    main()
