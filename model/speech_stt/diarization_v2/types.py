"""Tipe data diarization v2 — bebas dari SDK, bisa dibangun dari raw response dict."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    speaker: Optional[str]  # label mentah Scribe, mis. "speaker_0"


@dataclass
class Utterance:
    id: int
    start: float
    end: float
    speaker: Optional[str]
    text: str
    words: List[Word] = field(default_factory=list)


@dataclass
class DiarizationResult:
    words: List[Word]
    raw_response: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = field(default_factory=dict)
