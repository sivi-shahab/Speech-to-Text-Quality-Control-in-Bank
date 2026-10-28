"""
Provider diarization v2.

ScribeDiarizationProvider memanggil ElevenLabs convert() dengan parameter
EKSPLISIT per mode (lihat docs/diarization-audit.md §2.1):

  fixed2    -> num_speakers=2 (perilaku sekarang)
  threshold -> num_speakers TIDAK dikirim + diarization_threshold
               (SDK: threshold hanya boleh saat num_speakers=None)
  roles     -> num_speakers=2 + detect_speaker_roles=True (+10% biaya)

Beda dengan ElevenLabsBackend._call_api (jalur lama, tidak diubah):
  - timeout benar-benar diteruskan (request_options), bukan default SDK 240 s;
  - TIDAK ada fallback TypeError yang diam-diam membuang parameter;
  - enable_logging dikirim hanya bila dikonfigurasi (zero-retention = False).
Semaphore konkurensi (_ELEVEN_SEM) dipakai BERSAMA dengan jalur lama supaya
batas 12 request paralel langganan tetap dihitung satu.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Protocol

from elevenlabs.core.api_error import ApiError

from model.speech_stt.asr_backends import elevenlabs_backend as _eleven
from model.speech_stt.diarization_v2.types import DiarizationResult, Word

logger = logging.getLogger(__name__)

MODES = ("fixed2", "threshold", "roles")
_ISO1_TO_ISO3 = {"id": "ind", "en": "eng"}


class DiarizationProvider(Protocol):
    def diarize(self, audio_path: str, language: str, *, campaign: Optional[str] = None) -> DiarizationResult:
        ...


def words_from_raw(raw: Optional[Dict[str, Any]]) -> List[Word]:
    """Ambil kata (type=word) dari raw response dict Scribe. Spacing/audio_event dibuang."""
    words: List[Word] = []
    for w in (raw or {}).get("words") or []:
        if w.get("type") and w.get("type") != "word":  # sama dengan jalur lama
            continue
        text = (w.get("text") or "").strip()
        start, end = w.get("start"), w.get("end")
        if not text or start is None or end is None:
            continue
        words.append(Word(text, float(start), float(end), w.get("speaker_id")))
    return words


class CachedRawResponseProvider:
    """Provider dari raw response yang SUDAH ada (shadow mode & evaluasi offline) — tanpa API."""

    def __init__(self, raw: Optional[Dict[str, Any]], source: str = "cached"):
        self.raw = raw
        self.source = source

    def diarize(self, audio_path: str = "", language: str = "", *, campaign: Optional[str] = None) -> DiarizationResult:
        return DiarizationResult(words=words_from_raw(self.raw), raw_response=self.raw,
                                 meta={"source": self.source})


class ScribeDiarizationProvider:
    def __init__(
        self,
        backend: "_eleven.ElevenLabsBackend",
        mode: str = "fixed2",
        threshold: Optional[float] = None,
        enable_logging: Optional[bool] = None,
        keyterms: Optional[List[str]] = None,
        timeout_sec: float = 300.0,
        num_speakers: int = 2,
    ):
        if mode not in MODES:
            raise ValueError("SCRIBE_DIAR_MODE tidak dikenal: %r (pilihan: %s)" % (mode, ", ".join(MODES)))
        if mode == "threshold" and threshold is None:
            raise ValueError("SCRIBE_DIAR_MODE=threshold butuh SCRIBE_DIAR_THRESHOLD")
        if threshold is not None and not 0.0 < threshold < 1.0:
            raise ValueError("SCRIBE_DIAR_THRESHOLD harus di antara 0 dan 1, dapat %r" % threshold)
        self.backend = backend
        self.mode = mode
        self.threshold = threshold
        self.enable_logging = enable_logging
        # Default: keyterms backend (ELEVENLABS_KEYTERMS), sama dengan jalur lama.
        self.keyterms = list(backend.keyterms if keyterms is None else keyterms)
        self.timeout_sec = timeout_sec
        self.num_speakers = num_speakers

    @classmethod
    def from_config(cls, backend) -> "ScribeDiarizationProvider":
        from model.speech_stt import config as c

        return cls(
            backend,
            mode=c.SCRIBE_DIAR_MODE,
            threshold=c.SCRIBE_DIAR_THRESHOLD,
            enable_logging=c.SCRIBE_ENABLE_LOGGING,
            timeout_sec=c.ELEVENLABS_REQUEST_TIMEOUT_SEC,
            num_speakers=c.ELEVENLABS_NUM_SPEAKERS,
        )

    def build_kwargs(self, language: str) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model_id": self.backend.model_id,
            "language_code": _ISO1_TO_ISO3.get((language or "").lower(), language),
            "diarize": True,
            "timestamps_granularity": "word",
            "tag_audio_events": self.backend.tag_audio_events,
            "request_options": {"timeout_in_seconds": int(self.timeout_sec)},
        }
        if self.mode in ("fixed2", "roles"):
            kwargs["num_speakers"] = self.num_speakers
        if self.mode == "threshold":
            kwargs["diarization_threshold"] = self.threshold
        if self.mode == "roles":
            kwargs["detect_speaker_roles"] = True
        if self.keyterms:
            kwargs["keyterms"] = self.keyterms[:1000]
        if self.enable_logging is not None:
            kwargs["enable_logging"] = self.enable_logging
        return kwargs

    def _convert(self, audio_path: str, kwargs: Dict[str, Any]):
        with open(audio_path, "rb") as f:
            wait = 0
            for attempt in range(_eleven._ELEVEN_RETRIES):
                f.seek(0)
                _eleven._ELEVEN_SEM.acquire()
                try:
                    return self.backend._client.speech_to_text.convert(file=f, **kwargs)
                except ApiError as e:
                    if e.status_code != 429:
                        raise
                    wait = min(2 ** attempt, 30)
                    logger.warning("[DiarV2] 429 concurrent_limit — retry %d/%d dalam %ss",
                                   attempt + 1, _eleven._ELEVEN_RETRIES, wait)
                finally:
                    _eleven._ELEVEN_SEM.release()
                time.sleep(wait)
        raise RuntimeError("[DiarV2] Gagal setelah retry: 429 concurrent_limit.")

    def diarize(self, audio_path: str, language: str, *, campaign: Optional[str] = None) -> DiarizationResult:
        kwargs = self.build_kwargs(language)
        response = self._convert(audio_path, kwargs)
        raw = self.backend._response_to_dict(response)
        # Disimpan di backend supaya engine.py & save_stt_pipeline_results()
        # tetap menyimpan raw response seperti jalur lama.
        self.backend.last_raw_response = raw
        sent = {k: v for k, v in kwargs.items() if k not in ("keyterms", "request_options")}
        sent["n_keyterms"] = len(kwargs.get("keyterms", []))
        return DiarizationResult(words=words_from_raw(raw), raw_response=raw,
                                 meta={"source": "scribe_api", "scribe_mode": self.mode, "params": sent})
