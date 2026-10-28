"""
Role assignment v2: siapa Agent di antara 2 label Scribe.

Menggantikan aturan "bicara pertama = Agent" (segment.py:46-47), yang pada
data Cashline salah di 14-16% call (docs/diarization-audit.md §5). Dua
sinyal digabung:

  1. cue skrip agent (role_cues.yaml): skor = 2*intro (N utterance pertama)
     + 1*script - 1*customer, per label;
  2. dominansi: label dengan kata terbanyak (sejalan dengan cue di ~96% call).

Keputusan:
  cue jelas & sama dengan dominan          -> cue,       confident
  cue jelas, beda dominan, margin >= kuat  -> cue,       confident  (intro tervalidasi App B)
  cue jelas, beda dominan, margin lemah    -> cue,       TIDAK confident
  cue tidak ada / seri                     -> dominan,   TIDAK confident
  hanya 1 label                            -> label itu, TIDAK confident
Call yang tidak confident adalah kandidat fallback LLM (Fase 6).
"""
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from model.speech_stt.diarization_v2.types import RoleDecision, Utterance

CUES_PATH = Path(__file__).with_name("role_cues.yaml")
MIN_MARGIN = 1      # selisih skor minimal agar cue dianggap "jelas"
STRONG_MARGIN = 2   # selisih yang setara >= 1 intro: cukup kuat melawan dominansi


@lru_cache(maxsize=4)
def load_cues(path: str = str(CUES_PATH)) -> Dict:
    data = yaml.safe_load(Path(path).read_text())
    return {
        "version": data["version"],
        "intro_window": int(data["intro_window"]),
        "weights": dict(data["weights"]),
        **{k: [re.compile(p, re.IGNORECASE) for p in data.get(k) or []] for k in ("intro", "script", "customer")},
    }


def _hits(patterns, text: str) -> int:
    return int(any(p.search(text) for p in patterns))


def assign_roles(
    utterances: List[Utterance],
    cues: Optional[Dict] = None,
    *,
    min_margin: int = MIN_MARGIN,
    strong_margin: int = STRONG_MARGIN,
) -> RoleDecision:
    cues = cues or load_cues()
    speakers = list(dict.fromkeys(u.speaker for u in utterances))
    if not speakers:
        return RoleDecision(None, False, "empty")

    n_words = Counter()
    hits = {s: Counter() for s in speakers}
    for u in utterances:
        n_words[u.speaker] += len(u.words)
        if u.id < cues["intro_window"]:
            hits[u.speaker]["intro"] += _hits(cues["intro"], u.text)
        hits[u.speaker]["script"] += _hits(cues["script"], u.text)
        hits[u.speaker]["customer"] += _hits(cues["customer"], u.text)

    w = cues["weights"]
    score = {s: sum(w[k] * hits[s][k] for k in ("intro", "script", "customer")) for s in speakers}
    total = sum(n_words.values()) or 1
    dominant = max(speakers, key=lambda s: (n_words[s], -speakers.index(s)))
    evidence = {
        "cues_version": cues["version"],
        "hits": {s: dict(hits[s]) for s in speakers},
        "score": score,
        "word_share": {s: round(n_words[s] / total, 3) for s in speakers},
        "dominant": dominant,
    }
    if len(speakers) == 1:
        return RoleDecision(speakers[0], False, "single_speaker", evidence)

    ranked = sorted(speakers, key=lambda s: -score[s])
    margin = score[ranked[0]] - score[ranked[1]]
    evidence["margin"] = margin
    if margin < min_margin:
        return RoleDecision(dominant, False, "dominance", evidence)
    cue_agent = ranked[0]
    if cue_agent == dominant:
        return RoleDecision(cue_agent, True, "cue+dominance", evidence)
    if margin >= strong_margin:
        return RoleDecision(cue_agent, True, "cue", evidence)
    return RoleDecision(cue_agent, False, "cue_vs_dominance", evidence)
