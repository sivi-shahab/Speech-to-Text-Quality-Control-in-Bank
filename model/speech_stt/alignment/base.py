#!/usr/bin/env python3
"""
model/speech_stt/alignment/base.py

[STRATEGY PATTERN]
Interface yang wajib diimplementasikan setiap algoritma alignment
(pure-overlap vs legacy-heuristic). SpeechSTT (facade) memanggil lewat
interface ini tanpa perlu tahu detail internal masing-masing algoritma.
"""

from typing import List, Optional, Tuple

from model.speech_stt.segment import SpeakerSegment


class AlignmentStrategy:
    name: str = "base"

    def align(
        self,
        whisper_segments,
        diar_segments,
        audio_path: Optional[str] = None,
    ) -> Tuple[List[SpeakerSegment], int]:
        """
        Return (aligned_segments, reattributed_count).
        reattributed_count = berapa segmen yang label speaker-nya
        dikoreksi dari hasil overlap murni pyannote (dipakai untuk
        validasi kualitas di _validate_diarization_output).
        """
        raise NotImplementedError
