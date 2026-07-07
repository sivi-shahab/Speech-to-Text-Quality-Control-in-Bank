#!/usr/bin/env python3
"""
model/speech_stt/formatter.py

Semua fungsi terkait "ubah hasil alignment jadi teks laporan siap-PDF":
talk-time computation, speaker stats accumulation, dan
format_output_with_speakers. Dipindah verbatim dari speech_stt.py.
"""

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

import soundfile as sf

from model.speech_stt.segment import SpeakerSegment
from model.speech_stt.gender_detection import build_speaker_label
from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)


def format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = seconds % 60
    return "%02d:%05.2f" % (mins, secs)


def compute_talk_time(
    aligned_segments: List[SpeakerSegment], audio_path: str
) -> Dict[str, Any]:
    dur: Dict[str, float] = {}
    for seg in aligned_segments:
        dur[seg.speaker] = dur.get(seg.speaker, 0.0) + (seg.end - seg.start)
    agent_dur   = dur.get(AGENT_SPEAKER_LABEL, 0.0)
    nasabah_dur = dur.get(CUSTOMER_SPEAKER_LABEL, 0.0)
    try:
        total_dur = sf.info(audio_path).duration
    except Exception:
        total_dur = agent_dur + nasabah_dur
    silence_dur = max(0.0, total_dur - agent_dur - nasabah_dur)
    denom       = total_dur if total_dur > 0 else 1.0
    return {
        "agent":   {"speaker": AGENT_SPEAKER_LABEL,
                    "duration_sec": round(agent_dur, 2),
                    "pct": round(agent_dur / denom * 100, 1)},
        "nasabah": {"speaker": CUSTOMER_SPEAKER_LABEL,
                    "duration_sec": round(nasabah_dur, 2),
                    "pct": round(nasabah_dur / denom * 100, 1)},
        "silence_sec": round(silence_dur, 2),
        "silence_pct": round(silence_dur / denom * 100, 1),
        "total_sec":   round(total_dur, 2),
    }


def accum_speaker_stats(
    speaker_stats: Dict[str, dict], speaker: str, conclusion: str
) -> None:
    if speaker not in speaker_stats:
        speaker_stats[speaker] = {
            "POSITIF": 0, "NEGATIF": 0, "NETRAL": 0, "TOTAL": 0}
    speaker_stats[speaker][conclusion] = \
        speaker_stats[speaker].get(conclusion, 0) + 1
    speaker_stats[speaker]["TOTAL"] += 1


def split_long_segment(
    start: float, end: float, text: str, max_chars: int = 80
) -> List[str]:
    import re
    duration  = end - start
    sentences = re.split(r'(?<=[.?!])\s+', text)
    if len(sentences) == 1 and len(text) > max_chars:
        words, sentences = text.split(), []
        current, length  = [], 0
        for word in words:
            if length + len(word) > max_chars and current:
                sentences.append(" ".join(current))
                current, length = [word], len(word)
            else:
                current.append(word)
                length += len(word) + 1
        if current:
            sentences.append(" ".join(current))
    total_chars = sum(len(s) for s in sentences) or 1
    lines, current_t = [], start
    for sentence in sentences:
        ratio   = len(sentence) / total_chars
        seg_end = current_t + duration * ratio
        lines.append("[%s -> %s] %s" % (
            format_time(current_t), format_time(seg_end), sentence))
        current_t = seg_end
    return lines


def format_output_with_speakers(
    aligned_segments:  List[SpeakerSegment],
    gender_map:        Optional[Dict[str, str]]  = None,
    segment_emotions:  Optional[List[str]]        = None,
    speaker_stats:     Optional[Dict[str, dict]] = None,
    audio_meta:        Optional[Dict[str, Any]]  = None,
) -> Tuple[str, dict]:
    gender_map       = gender_map       or {}
    segment_emotions = segment_emotions or []
    speaker_stats    = speaker_stats    or {}
    audio_meta       = audio_meta       or {}

    lines: List[str]       = []
    stats: Dict[str, float] = {}
    for seg in aligned_segments:
        stats.setdefault(seg.speaker, 0.0)
        stats[seg.speaker] += seg.end - seg.start

    now_str      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fraud_alerts = audio_meta.get("fraud_alerts", [])

    if fraud_alerts:
        lines.append("STATUS AUDIT   : ⚠️  DANGER / SUSPECTED FRAUD ⚠️")
        for i, alert in enumerate(fraud_alerts, 1):
            itype = "Manipulasi Suara" if alert["type"] == "pitch_unstable" \
                    else "Estafet Pembicara"
            lines.append("  ├─ INDIKASI %-2d: Anomali pitch pada %s di menit [%s] (%s)"
                          % (i, alert["speaker"], alert["timestamp"], itype))
    else:
        lines.append("STATUS AUDIT   : ✅ AMAN")

    lines.append("Audio File: %s" % audio_meta.get("file_name", "-"))
    lines.append("Generated : %s" % now_str)
    lines.append("Mode      : %s" % audio_meta.get("mode", "diarization+transcription"))
    lines.append("Language  : %s" % audio_meta.get("language", "id"))
    lines.append("Speakers  : %d" % len(stats))
    lines.append("Proc. Time: %s" % audio_meta.get("proc_time", "-"))

    tt = audio_meta.get("talk_time")
    if tt:
        lines.append("DURASI BICARA:")
        a = tt.get("agent",   {})
        n = tt.get("nasabah", {})
        lines.append("• Agent   (%-10s) : %6.1f detik  (%5.1f%%)"
                      % (a.get("speaker", AGENT_SPEAKER_LABEL),
                         a.get("duration_sec", 0.0), a.get("pct", 0.0)))
        lines.append("• Nasabah (%-10s) : %6.1f detik  (%5.1f%%)"
                      % (n.get("speaker", CUSTOMER_SPEAKER_LABEL),
                         n.get("duration_sec", 0.0), n.get("pct", 0.0)))
        lines.append("• Silence              : %6.1f detik  (%5.1f%%)"
                      % (tt.get("silence_sec", 0.0), tt.get("silence_pct", 0.0)))
        lines.append("• Total                : %6.1f detik" % tt.get("total_sec", 0.0))

    if speaker_stats:
        lines.append("PROFIL & DOMINASI EMOSI:")
        for spk in sorted(stats.keys()):
            gender_raw = gender_map.get(spk, "unknown")
            gender_id  = ("Pria" if gender_raw == "male"
                           else "Wanita" if gender_raw == "female" else "?")
            st    = speaker_stats.get(
                spk, {"POSITIF": 0, "NEGATIF": 0, "NETRAL": 0, "TOTAL": 1})
            total = max(st.get("TOTAL", 1), 1)
            lines.append(
                "• %-10s : %-6s | POSITIF %.0f%% / NEGATIF %.0f%% / NETRAL %.0f%%"
                % (spk, gender_id,
                   st.get("POSITIF", 0) / total * 100,
                   st.get("NEGATIF", 0) / total * 100,
                   st.get("NETRAL",  0) / total * 100))

    lines.append("=" * 64)

    curr_spk, block_idx = None, 0
    for idx, seg in enumerate(aligned_segments):
        emo = segment_emotions[idx] if idx < len(segment_emotions) else ""
        if seg.speaker != curr_spk:
            seg_ts        = format_time(seg.start)
            fraud_for_seg = next(
                (a for a in fraud_alerts
                 if a["speaker"] == seg.speaker and a["timestamp"] == seg_ts),
                None,
            )
            base_label = (
                "%s (%s)" % (seg.speaker, fraud_for_seg["detail"])
                if fraud_for_seg
                else build_speaker_label(seg.speaker, gender_map)
            )
            emo_suffix = " [%s]" % emo if emo else ""
            if block_idx == 0:
                lines.append("%s%s:" % (base_label, emo_suffix))
            else:
                lines.append("")
                lines.append("[%s%s]:" % (base_label, emo_suffix))
            curr_spk   = seg.speaker
            block_idx += 1
        s = format_time(seg.start)
        e = format_time(seg.end)
        lines.append("[%s -> %s] %s" % (s, e, seg.text.strip()))

    return "\n".join(lines).strip(), {
        "speaker_count":     len(stats),
        "speaker_durations": stats,
        "talk_time":         audio_meta.get("talk_time", {}),
    }
