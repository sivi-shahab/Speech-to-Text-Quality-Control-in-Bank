"""
Rangkaian post-processing v2: words -> segmen -> label Agent/Customer -> merge.

FASE 1: isi rangkaian SENGAJA setara jalur lama (grouping per perubahan
speaker_id + map_speakers_to_binary "bicara pertama = Agent" + merge 0,6 s),
supaya flag on/shadow bisa diuji identik dulu. Fase 2 mengganti langkahnya
(collapse, smoothing, role assignment berbasis cue).

Meta yang dihasilkan HANYA berisi angka/label/timestamp — tidak ada teks
transkrip (aman untuk log & kolom DB).
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from model.speech_stt.alignment.pure_alignment import merge_same_speaker_simple
from model.speech_stt.diarization_v2.provider import CachedRawResponseProvider
from model.speech_stt.diarization_v2.types import DiarizationResult, Word
from model.speech_stt.segment import SpeakerSegment, map_speakers_to_binary

logger = logging.getLogger(__name__)

META_VERSION = 1
MERGE_MAX_GAP_SEC = 0.6


def _group_by_speaker(words: List[Word]) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for w in words:
        if segments and segments[-1]["speaker_id"] == w.speaker:
            seg = segments[-1]
            seg["end"] = w.end
            seg["text"] += " " + w.text
        else:
            segments.append({"start": w.start, "end": w.end, "speaker": str(w.speaker or "speaker_0"),
                             "text": w.text, "speaker_id": w.speaker, "speaker_name": None})
    return segments


def run_v2(result: DiarizationResult) -> Tuple[List[SpeakerSegment], List[Dict[str, Any]], int, Dict[str, Any]]:
    """Return (aligned_merged, diar_segs, segments_before_merge, meta)."""
    raw_segments = map_speakers_to_binary(_group_by_speaker(result.words))
    aligned = [SpeakerSegment(s["start"], s["end"], s["text"].strip(), s["speaker"], confidence=0.9)
               for s in raw_segments if s["text"].strip()]
    segments_before_merge = len(aligned)
    merged = merge_same_speaker_simple(aligned, max_gap_sec=MERGE_MAX_GAP_SEC)
    meta = {
        "version": META_VERSION,
        **result.meta,
        "role_rule": "first_speaker",
        "n_words": len(result.words),
        "n_spk_raw": len({w.speaker for w in result.words}),
        "n_segments_raw": segments_before_merge,
        "n_segments": len(merged),
        "segments": [[round(s.start, 3), round(s.end, 3), s.speaker] for s in merged],
    }
    return merged, raw_segments, segments_before_merge, meta


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
