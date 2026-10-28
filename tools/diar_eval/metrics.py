"""
Metrik evaluasi terhadap ground truth.

Format segmen (referensi & hipotesis): list of {"start", "end", "speaker"}
dengan speaker = label ROLE ("Agent"/"Customer"). Format GT minimal per call
(file JSON; konversi RTTM menyusul di Fase 4):

    {"stem": "...",
     "segments": [{"start": 0.5, "end": 1.2, "speaker": "Customer"}, ...],
     "items":    [{"item": "SC_CL_2", "start": 1.5, "end": 4.6, "speaker": "Agent"}, ...]}

  - DER/JER : pyannote.metrics (pemetaan label optimal -> TIDAK peduli role
              tertukar; mengukur kualitas segmentasi/clustering saja).
  - role_accuracy : porsi durasi bicara referensi yang labelnya SAMA PERSIS
              dengan hipotesis (role tertukar = 0%).
  - item_role_accuracy : per item checklist, apakah role mayoritas hipotesis
              di jendela bukti item sama dengan role yang diharapkan.
"""
from collections import defaultdict
from typing import Dict, Iterable, List

from pyannote.core import Annotation, Segment
from pyannote.metrics.diarization import DiarizationErrorRate, JaccardErrorRate

DER_COLLAR = 0.25  # semantik pyannote: lebar total di sekitar tiap batas


def to_annotation(segments: Iterable[Dict], uri: str = "call") -> Annotation:
    ann = Annotation(uri=uri)
    for i, s in enumerate(segments):
        if s["end"] > s["start"]:
            ann[Segment(float(s["start"]), float(s["end"])), i] = s["speaker"]
    return ann


def der_metric(collar: float = DER_COLLAR, skip_overlap: bool = True) -> DiarizationErrorRate:
    return DiarizationErrorRate(collar=collar, skip_overlap=skip_overlap)


def der(reference: List[Dict], hypothesis: List[Dict], collar: float = DER_COLLAR,
        skip_overlap: bool = True) -> Dict[str, float]:
    m = der_metric(collar, skip_overlap)
    d = m(to_annotation(reference), to_annotation(hypothesis), detailed=True)
    return {"der": d["diarization error rate"], "confusion": d["confusion"],
            "missed": d["missed detection"], "false_alarm": d["false alarm"], "total": d["total"]}


def jer(reference: List[Dict], hypothesis: List[Dict], collar: float = DER_COLLAR) -> float:
    return JaccardErrorRate(collar=collar)(to_annotation(reference), to_annotation(hypothesis))


def _overlap(a: Dict, b: Dict) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def role_durations(reference: List[Dict], hypothesis: List[Dict]) -> Dict[str, float]:
    total = sum(max(0.0, r["end"] - r["start"]) for r in reference)
    correct = sum(_overlap(r, h) for r in reference for h in hypothesis if r["speaker"] == h["speaker"])
    return {"correct_sec": correct, "total_sec": total}


def role_accuracy(reference: List[Dict], hypothesis: List[Dict]) -> float:
    d = role_durations(reference, hypothesis)
    return d["correct_sec"] / d["total_sec"] if d["total_sec"] else float("nan")


def item_role_accuracy(items: List[Dict], hypothesis: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "correct": 0})
    for it in items:
        dur: Dict[str, float] = defaultdict(float)
        for h in hypothesis:
            dur[h["speaker"]] += _overlap(it, h)
        got = max(dur, key=dur.get) if dur and max(dur.values()) > 0 else None
        out[it["item"]]["n"] += 1
        out[it["item"]]["correct"] += int(got == it["speaker"])
    return {k: {**v, "acc": v["correct"] / v["n"]} for k, v in sorted(out.items())}
