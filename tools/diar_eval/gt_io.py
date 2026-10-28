"""
I/O ground truth (Fase 4): draft CSV untuk pelabel, impor CSV -> GT JSON, RTTM,
dan kesepakatan antar-pelabel. Panduan pelabelan: docs/diarization-gt-guideline.md.

    python -m tools.diar_eval.gt_io draft     --sample gt_sample.json --out-dir /luar/repo/gt_draft
    python -m tools.diar_eval.gt_io import    --in-dir /luar/repo/gt_draft --gt-dir /luar/repo/gt
    python -m tools.diar_eval.gt_io validate  --gt-dir /luar/repo/gt
    python -m tools.diar_eval.gt_io to-rttm   --gt-dir /luar/repo/gt --out /luar/repo/gt.rttm
    python -m tools.diar_eval.gt_io agreement --a /luar/repo/gt_A --b /luar/repo/gt_B

Draft memakai label NETRAL (SPK_A/SPK_B, urutan kemunculan) — sengaja bukan
Agent/Customer supaya pelabel tidak terbawa tebakan role sistem. Draft berisi
teks transkrip (PII); GT JSON hasil impor TIDAK menyimpan teks.
"""
import argparse
import csv
import io
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from model.speech_stt.diarization_v2.postprocess import group_utterances
from model.speech_stt.diarization_v2.provider import words_from_raw
from tools.diar_eval.cache import cache_dir, iter_entries
from tools.diar_eval.metrics import der, role_durations

ROLES = ("Agent", "Customer", "Other")
SEG_COLS = ("seg_id", "start", "end", "speaker", "text")
ITEM_COLS = ("item", "start", "end", "speaker", "catatan")
_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+(?:[.,]\d+)?)$")


def parse_time(value) -> float:
    """Detik ("12.5", "12,5") atau "mm:ss.xx" (format PDF) -> float detik."""
    m = _TIME_RE.match(str(value).strip())
    if not m:
        raise ValueError("format waktu tidak dikenal: %r" % value)
    return int(m.group(1) or 0) * 60 + float(m.group(2).replace(",", "."))


def _read_csv(path: Path) -> List[Dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text), dialect=dialect)]


def _write_csv(path: Path, cols, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def draft_rows(raw: Dict) -> List[Dict]:
    utts = group_utterances(words_from_raw(raw))
    neutral = {spk: "SPK_%s" % chr(ord("A") + i) for i, spk in enumerate(dict.fromkeys(u.speaker for u in utts))}
    return [{"seg_id": u.id, "start": "%.2f" % u.start, "end": "%.2f" % u.end,
             "speaker": neutral[u.speaker], "text": u.text} for u in utts]


def parse_segments(rows: List[Dict]) -> Tuple[List[Dict], List[str]]:
    segs, errors = [], []
    for i, r in enumerate(rows, start=2):  # baris 1 = header
        if not any(r.values()):
            continue
        try:
            s, e = parse_time(r["start"]), parse_time(r["end"])
        except (KeyError, ValueError) as ex:
            errors.append("baris %d: %s" % (i, ex))
            continue
        spk = r.get("speaker", "")
        if spk not in ROLES:
            errors.append("baris %d: speaker %r harus salah satu dari %s" % (i, spk, "/".join(ROLES)))
        elif e <= s:
            errors.append("baris %d: end (%s) harus > start (%s)" % (i, r["end"], r["start"]))
        else:
            segs.append({"start": s, "end": e, "speaker": spk, **({"item": r["item"]} if r.get("item") else {})})
    return sorted(segs, key=lambda x: (x["start"], x["end"])), errors


def validate_gt(gt: Dict) -> List[str]:
    errors = []
    if not gt.get("stem"):
        errors.append("stem kosong")
    if not gt.get("segments"):
        errors.append("segments kosong")
    for key in ("segments", "items"):
        for i, s in enumerate(gt.get(key) or []):
            if s.get("speaker") not in ROLES:
                errors.append("%s[%d]: speaker %r" % (key, i, s.get("speaker")))
            if not s.get("end", 0) > s.get("start", 0):
                errors.append("%s[%d]: end <= start" % (key, i))
            if key == "items" and not s.get("item"):
                errors.append("items[%d]: item kosong" % i)
    return errors


def import_dir(in_dir: Path, gt_dir: Path) -> Dict[str, List[str]]:
    gt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    report = {}
    for seg_path in sorted(in_dir.glob("*.segments.csv")):
        stem = seg_path.name[: -len(".segments.csv")]
        segs, errors = parse_segments(_read_csv(seg_path))
        items: List[Dict] = []
        item_path = in_dir / ("%s.items.csv" % stem)
        if item_path.exists():
            items, item_errors = parse_segments(_read_csv(item_path))
            errors += ["items " + e for e in item_errors]
        gt = {"stem": stem, "segments": segs, "items": items}
        errors += validate_gt(gt)
        if errors:
            report[stem] = errors
            continue
        (gt_dir / ("%s.json" % stem)).write_text(json.dumps(gt, indent=1))
        report[stem] = []
    return report


def to_rttm(gts: List[Dict]) -> str:
    return "".join("SPEAKER %s 1 %.3f %.3f <NA> <NA> %s <NA> <NA>\n" % (g["stem"], s["start"], s["end"] - s["start"],
                                                                         s["speaker"])
                   for g in gts for s in g["segments"])


def from_rttm(text: str) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            start, dur = float(p[3]), float(p[4])
            out.setdefault(p[1], []).append({"start": start, "end": round(start + dur, 6), "speaker": p[7]})
    return out


def _load_dir(d: Path) -> Dict[str, Dict]:
    return {g["stem"]: g for g in (json.loads(p.read_text()) for p in sorted(d.glob("*.json")))}


def agreement(dir_a: Path, dir_b: Path) -> Dict:
    a, b = _load_dir(dir_a), _load_dir(dir_b)
    common = sorted(set(a) & set(b))
    per_call = {}
    correct = total = 0.0
    for stem in common:
        d = role_durations(a[stem]["segments"], b[stem]["segments"])
        correct, total = correct + d["correct_sec"], total + d["total_sec"]
        per_call[stem] = {"role_agreement": round(d["correct_sec"] / d["total_sec"], 4) if d["total_sec"] else None,
                          "der": round(der(a[stem]["segments"], b[stem]["segments"])["der"], 4)}
    return {"n_common": len(common), "role_agreement_pooled": round(correct / total, 4) if total else None,
            "per_call": per_call}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("draft", "import", "validate", "to-rttm", "agreement"))
    ap.add_argument("--sample")
    ap.add_argument("--cache-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--in-dir")
    ap.add_argument("--gt-dir")
    ap.add_argument("--out")
    ap.add_argument("--a")
    ap.add_argument("--b")
    a = ap.parse_args(argv)
    if a.command == "draft":
        want = {c["stem"]: c for c in json.load(open(a.sample))["calls"]}
        out = Path(a.out_dir)
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        n = 0
        for e in iter_entries(cache_dir(a.cache_dir)):
            if e["stem"] in want and e["raw"]:
                _write_csv(out / ("%s.segments.csv" % e["stem"]), SEG_COLS, draft_rows(e["raw"]))
                _write_csv(out / ("%s.items.csv" % e["stem"]), ITEM_COLS, [])
                n += 1
        done = {p.name.split(".")[0] for p in out.glob("*.segments.csv")}
        print(json.dumps({"drafts": n, "missing": sorted(set(want) - done)}))
    elif a.command == "import":
        rep = import_dir(Path(a.in_dir), Path(a.gt_dir))
        print(json.dumps({"ok": sorted(k for k, v in rep.items() if not v),
                          "errors": {k: v for k, v in rep.items() if v}}, indent=1, ensure_ascii=False))
    elif a.command == "validate":
        print(json.dumps({s: validate_gt(g) for s, g in _load_dir(Path(a.gt_dir)).items() if validate_gt(g)},
                         indent=1) or "{}")
    elif a.command == "to-rttm":
        Path(a.out).write_text(to_rttm(list(_load_dir(Path(a.gt_dir)).values())))
    else:
        print(json.dumps(agreement(Path(a.a), Path(a.b)), indent=1))


if __name__ == "__main__":
    main()
