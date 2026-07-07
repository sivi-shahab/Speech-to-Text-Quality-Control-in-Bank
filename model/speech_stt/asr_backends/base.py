#!/usr/bin/env python3
"""
model/speech_stt/asr_backends/base.py

[STRATEGY PATTERN]
Interface yang wajib diimplementasikan setiap "mesin ASR" (Whisper vs
MERaLiON-3, atau backend lain di masa depan). Ini SUMBU VARIASI YANG
BERBEDA dari alignment/ (yang mengatur "siapa bicara kapan") — modul ini
mengatur "apa yang diucapkan".

Kenapa dua metode transcribe terpisah (bukan satu):
- `transcribe_full()` dipakai mode simple (tanpa diarization) — butuh
  segmen + word-level timestamps kalau backend menyediakannya (Whisper
  ya, MERaLiON tidak).
- `transcribe_segment()` dipakai mode diarization ketika backend TIDAK
  bisa memberi word-level timestamp (seperti MERaLiON) — pyannote sudah
  menentukan batas waktu & speaker tiap segmen, backend cukup transkrip
  audio segmen itu saja tanpa perlu word-alignment sama sekali.

`supports_word_timestamps` memberi tahu SpeechSTT.transcribe_with_diarization
mana strategi alignment yang boleh dipakai (pure-overlap Whisper vs
per-segment MERaLiON) — lihat engine.py.
"""

from typing import Any, Dict, List, Optional, Tuple


class ASRBackend:
    name: str = "base"

    #: True kalau backend menghasilkan word-level timestamp (Whisper),
    #: False kalau backend cuma menghasilkan teks utuh per klip audio
    #: (MERaLiON — instruction-following Speech-LLM, bukan streaming ASR).
    supports_word_timestamps: bool = False

    #: [NEW] True kalau backend SUDAH menyediakan diarization sendiri dalam
    #: satu API call (mis. ElevenLabs Scribe: ASR + speaker label sekaligus).
    #: Kalau True, engine.py TIDAK akan memuat/memanggil pyannote/NeMo sama
    #: sekali — lihat transcribe_with_speakers() di bawah.
    provides_own_diarization: bool = False

    def load(self, gpu_id: int) -> None:
        raise NotImplementedError

    def transcribe_full(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> Tuple[List[Any], Any]:
        """
        Return (whisper_style_segments, info). `info` minimal punya
        atribut `.language` dan `.language_probability` supaya kompatibel
        dengan caller yang sudah ada di engine.py.
        """
        raise NotImplementedError

    def transcribe_segment(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> str:
        """Transkrip SATU klip audio pendek (hasil potongan per-speaker
        dari diarization) jadi teks. Dipakai backend yang TIDAK mendukung
        word-level timestamp."""
        raise NotImplementedError

    def transcribe_with_speakers(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
        num_speakers: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        [NEW] HANYA perlu diimplementasikan kalau provides_own_diarization=True.
        Return list of dict {"start": float, "end": float, "speaker": str,
        "text": str} — sudah termasuk speaker label MENTAH dari backend
        (belum di-binary-mapped AGENT_BM/NASABAH, itu tetap tugas caller
        lewat segment.map_speakers_to_binary() supaya konsisten dgn backend lain).
        """
        raise NotImplementedError
