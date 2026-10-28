"""
Rangkaian post-processing v2:

  words -> collapse_to_two -> smooth_single_word_flips -> group_utterances
        -> assign_roles (cue skrip + dominansi) -> label Agent/Customer
        -> merge_same_speaker_simple (fungsi jalur lama, tidak diubah)

Meta yang dihasilkan HANYA berisi angka/label/timestamp — tidak ada teks
transkrip (aman untuk log & kolom DB).
"""
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from model.speech_stt.alignment.pure_alignment import merge_same_speaker_simple
from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL
from model.speech_stt.diarization_v2.postprocess import (
    SMOOTH_MAX_GAP_SEC,
    UTTERANCE_MAX_GAP_SEC,
    collapse_to_two,
    group_utterances,
    smooth_single_word_flips,
)
from model.speech_stt.diarization_v2.provider import CachedRawResponseProvider
from model.speech_stt.diarization_v2.roles import MIN_MARGIN, STRONG_MARGIN, assign_roles
from model.speech_stt.diarization_v2.types import DiarizationResult
from model.speech_stt.segment import SpeakerSegment

logger = logging.getLogger(__name__)

META_VERSION = 2
MERGE_MAX_GAP_SEC = 0.6


@dataclass(frozen=True)
class V2Params:
    """Parameter post-processing. Default = nilai produksi; divariasikan oleh tools/diar_eval/grid.py."""
    smooth_max_gap_sec: float = SMOOTH_MAX_GAP_SEC
    utterance_max_gap_sec: float = UTTERANCE_MAX_GAP_SEC
    role_min_margin: int = MIN_MARGIN
    role_strong_margin: int = STRONG_MARGIN


DEFAULT_PARAMS = V2Params()


def run_v2(
    result: DiarizationResult, params: V2Params = DEFAULT_PARAMS,
) -> Tuple[List[SpeakerSegment], List[Dict[str, Any]], int, Dict[str, Any]]:
    """Return (aligned_merged, diar_segs, segments_before_merge, meta)."""
    words, n_collapsed = collapse_to_two(result.words)
    words, n_smoothed = smooth_single_word_flips(words, params.smooth_max_gap_sec)
    utterances = group_utterances(words, params.utterance_max_gap_sec)
    role = assign_roles(utterances, min_margin=params.role_min_margin, strong_margin=params.role_strong_margin)

    def label(spk):
        return AGENT_SPEAKER_LABEL if spk == role.agent else CUSTOMER_SPEAKER_LABEL

    diar_segs = [{"start": u.start, "end": u.end, "speaker": label(u.speaker), "text": u.text,
                  "speaker_original": u.speaker} for u in utterances]
    aligned = [SpeakerSegment(s["start"], s["end"], s["text"].strip(), s["speaker"], confidence=0.9)
               for s in diar_segs if s["text"].strip()]
    segments_before_merge = len(aligned)
    merged = merge_same_speaker_simple(aligned, max_gap_sec=MERGE_MAX_GAP_SEC)
    meta = {
        "version": META_VERSION,
        **result.meta,
        "role_rule": "cue+dominance",
        **({"v2_params": asdict(params)} if params != DEFAULT_PARAMS else {}),
        "role_confident": role.role_confident,
        "role_basis": role.basis,
        "agent_raw": role.agent,
        "role_evidence": role.evidence,
        "n_words": len(result.words),
        "n_spk_raw": len({w.speaker for w in result.words}),
        "n_collapsed": n_collapsed,
        "n_smoothed": n_smoothed,
        "n_segments_raw": segments_before_merge,
        "n_segments": len(merged),
        "segments": [[round(s.start, 3), round(s.end, 3), s.speaker] for s in merged],
    }
    return merged, diar_segs, segments_before_merge, meta


def shadow_from_raw(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Hitung v2 dari raw response jalur lama. TIDAK PERNAH raise — shadow tidak boleh menggagalkan job."""
    if not raw:
        return None
    try:
        _, _, _, meta = run_v2(CachedRawResponseProvider(raw, source="shadow").diarize())
        return meta
    except Exception as e:
        # Hanya nama exception — pesan bisa memuat potongan data.
        logger.warning("[DiarV2/shadow] gagal: %s", type(e).__name__)
        return {"version": META_VERSION, "source": "shadow", "error": type(e).__name__}
