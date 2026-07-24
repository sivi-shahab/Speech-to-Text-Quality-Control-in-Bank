#!/usr/bin/env python3
"""
model/speech_stt/asr_backends/elevenlabs_backend.py

[NEW] Backend opsional: ElevenLabs Scribe v2 — SATU-SATUNYA backend ASR
di sistem ini yang BUKAN model self-hosted, melainkan cloud API pihak
ketiga. Value-add utamanya: ASR + speaker diarization dalam SATU
panggilan API — output sudah berisi label speaker PER SEGMEN (bukan per
kata), jadi TIDAK PERLU pyannote/NeMo/alignment heuristik sama sekali
(lihat `provides_own_diarization = True`).

⚠️ WAJIB DIBACA SEBELUM ENABLE:
Data audio dikirim ke server ElevenLabs (cloud, region resmi yang
dipublikasikan: EU & India — TIDAK ada region Indonesia). Untuk data
nasabah bank, ini WAJIB disetujui tim legal/compliance dulu (UU PDP,
regulasi OJK) sebelum dipakai untuk data produksi asli.

[CONCURRENCY] Langganan ElevenLabs dibatasi MAKS 12 request paralel; kalau
terlampaui API balas HTTP 429 (concurrent_limit_exceeded). Modul ini
membatasi konkurensi via semaphore (default 8) + retry backoff saat 429 —
lihat _ELEVEN_SEM / _call_api. Semaphore ini PER-PROSES; kalau STT jalan di
banyak proses, atur ELEVENLABS_MAX_CONCURRENCY supaya (jumlah_proses ×
nilai) <= ~10.
"""
import logging
import os
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

from elevenlabs.core.api_error import ApiError

from model.speech_stt.asr_backends.base import ASRBackend
from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)

# --- Batas konkurensi ElevenLabs (limit langganan = 12; sisakan headroom) ---
# Semaphore membatasi jumlah panggilan convert() yang jalan bersamaan DALAM
# proses ini. Retry backoff menangani 429 saat limit tetap terlampaui (mis.
# karena proses lain). Keduanya bisa diatur via env.
_ELEVEN_SEM = threading.Semaphore(int(os.getenv("ELEVENLABS_MAX_CONCURRENCY", "5")))
_ELEVEN_RETRIES = int(os.getenv("ELEVENLABS_429_RETRIES", "15"))


class _ElevenLabsWord:
    __slots__ = ("start", "end", "word")

    def __init__(self, start: float, end: float, word: str):
        self.start = start
        self.end   = end
        self.word  = word


class _ElevenLabsSegment:
    __slots__ = ("start", "end", "text", "words", "speaker_id", "speaker_name")

    def __init__(
        self, start: float, end: float, text: str, words: List[_ElevenLabsWord],
        speaker_id: Optional[str] = None, speaker_name: Optional[str] = None,
    ):
        self.start        = start
        self.end          = end
        self.text         = text
        self.words        = words
        self.speaker_id   = speaker_id      # mis. "speaker_0" — sebelumnya dibuang
        self.speaker_name = speaker_name    # mis. "Speaker 0" — sebelumnya dibuang


class _ElevenLabsInfo:
    __slots__ = ("language", "language_probability")

    def __init__(self, language: str, language_probability: float = 1.0):
        self.language             = language
        self.language_probability = language_probability


class ElevenLabsBackend(ASRBackend):
    name = "elevenlabs"
    supports_word_timestamps = True
    provides_own_diarization = True  # [NEW] lihat base.py — skip pyannote/NeMo total

    def __init__(
        self,
        model_id: str,
        api_key: Optional[str],
        num_speakers: int = 2,
        tag_audio_events: bool = False,
        keyterms: Optional[List[str]] = None,
        timeout_sec: float = 300.0,
    ):
        self.model_id         = model_id
        self.api_key          = api_key
        self.num_speakers     = num_speakers
        self.tag_audio_events = tag_audio_events
        self.keyterms         = keyterms or []
        self.timeout_sec      = timeout_sec
        self._client          = None
        # [NEW] Response API MENTAH (dict JSON-serializable) dari panggilan
        # terakhir — dipakai untuk simpan ke DB apa adanya (lihat
        # services/db.py::save_stt_pipeline_results()). Di-set ulang
        # setiap kali transcribe_with_speakers()/transcribe_full() dipanggil.
        self.last_raw_response: Optional[Dict[str, Any]] = None

    def load(self, gpu_id: int) -> None:
        if not self.api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY belum di-set. Backend 'elevenlabs' adalah "
                "cloud API pihak ketiga (BUKAN model self-hosted) — dapatkan "
                "API key dari https://elevenlabs.io/app/settings/api-keys dan "
                "set env var ELEVENLABS_API_KEY.\n"
                "⚠️ PASTIKAN tim legal/compliance Anda sudah menyetujui "
                "pengiriman audio ke cloud pihak ketiga (region EU/India, "
                "TIDAK ada region Indonesia) sebelum mengaktifkan backend ini "
                "untuk data nasabah produksi asli."
            )
        from elevenlabs.client import ElevenLabs
        self._client = ElevenLabs(api_key=self.api_key)
        logger.info(
            "[ElevenLabs] Client siap (model=%s, num_speakers=%d, max_concurrency=%s). "
            "Audio akan dikirim ke cloud ElevenLabs — pastikan sudah "
            "disetujui compliance untuk data produksi.",
            self.model_id, self.num_speakers,
            os.getenv("ELEVENLABS_MAX_CONCURRENCY", "8"),
        )

    def is_available(self) -> bool:
        return self._client is not None

    # -----------------------------------------------------------------
    def _call_api(self, audio_path: str, language: str, diarize: bool):
        """Panggil ElevenLabs convert() dengan:
          - cap konkurensi (semaphore _ELEVEN_SEM) supaya tak menembus limit 12,
          - retry backoff saat HTTP 429 (concurrent_limit_exceeded),
          - fallback param minimal saat SDK menolak parameter (TypeError).
        Slot semaphore DILEPAS sebelum tidur retry supaya job lain bisa jalan.
        """
        with open(audio_path, "rb") as f:
            kwargs: Dict[str, Any] = dict(
                file             = f,
                model_id         = self.model_id,
                language_code    = language,
                diarize          = diarize,
                tag_audio_events = self.tag_audio_events,
            )
            if self.keyterms:
                kwargs["keyterms"] = self.keyterms[:1000]
            if diarize and self.num_speakers:
                kwargs["num_speakers"] = self.num_speakers

            wait = 0
            for attempt in range(_ELEVEN_RETRIES):
                f.seek(0)                       # reset file tiap percobaan
                _ELEVEN_SEM.acquire()           # cap konkurensi
                try:
                    try:
                        return self._client.speech_to_text.convert(**kwargs)
                    except TypeError as e:
                        # Fallback defensif — param num_speakers/keyterms/
                        # tag_audio_events bisa beda nama/tidak ada di versi SDK
                        # tertentu. Best-effort, BUKAN jaminan.
                        logger.warning(
                            "[ElevenLabs] Parameter API tidak dikenali SDK (%s), "
                            "retry dengan parameter minimal.", e,
                        )
                        f.seek(0)
                        return self._client.speech_to_text.convert(
                            file=f, model_id=self.model_id,
                            language_code=language, diarize=diarize,
                        )
                except ApiError as e:
                    # 429 concurrent_limit -> tunggu lalu retry; error lain -> lempar.
                    if e.status_code != 429:
                        raise
                    wait = min(2 ** attempt, 30)   # 1,2,4,8,16,30
                    logger.warning(
                        "[ElevenLabs] 429 concurrent_limit — retry %d/%d dalam %ss",
                        attempt + 1, _ELEVEN_RETRIES, wait,
                    )
                finally:
                    _ELEVEN_SEM.release()          # LEPAS slot sebelum tidur
                time.sleep(wait)

            raise RuntimeError(
                "[ElevenLabs] Gagal setelah retry: 429 concurrent_limit "
                "(kurangi konkurensi via ELEVENLABS_MAX_CONCURRENCY, kurangi "
                "jumlah transcribe paralel di sumbernya, atau upgrade langganan)."
            )

    @staticmethod
    def _response_to_dict(response) -> Dict[str, Any]:
        """
        [NEW] Konversi response SDK ElevenLabs (biasanya Pydantic model)
        jadi dict biasa yang JSON-serializable, untuk disimpan ke DB apa
        adanya. Coba beberapa method berbeda karena versi SDK bisa beda
        konvensi (Pydantic v2 pakai model_dump(), v1 pakai dict()).
        """
        for method_name in ("model_dump", "dict", "to_dict"):
            method = getattr(response, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    continue
        # Fallback terakhir: coba serialize langsung, kalau gagal simpan
        # representasi string apa adanya.
        try:
            import json
            return json.loads(json.dumps(response, default=lambda o: getattr(o, "__dict__", str(o))))
        except Exception:
            return {"_raw_repr": str(response)}

    @staticmethod
    def _extract_words_from_segment(seg) -> List[_ElevenLabsWord]:
        words = []
        for w in (getattr(seg, "words", None) or []):
            text  = getattr(w, "text", "") or ""
            start = getattr(w, "start_time", None)
            end   = getattr(w, "end_time", None)
            if text.strip() and start is not None and end is not None:
                words.append(_ElevenLabsWord(float(start), float(end), text))
        return words

    @staticmethod
    def _extract_speaker_id_name(seg) -> Tuple[Optional[str], Optional[str]]:
        """
        Ekstrak {"id":..., "name":...} dari field `speaker` satu segmen.
        Dipakai bersama oleh transcribe_full() (mode simple) DAN
        transcribe_with_speakers() (mode diarization).
        """
        speaker_obj = getattr(seg, "speaker", None)
        if speaker_obj is None:
            return None, None
        speaker_id   = getattr(speaker_obj, "id", None)
        speaker_name = getattr(speaker_obj, "name", None)
        return speaker_id, speaker_name

    @staticmethod
    def _positional_speaker_label(speaker_id: Optional[str], speaker_name: Optional[str]) -> str:
        """
        [NEW] Pemetaan POSISIONAL khusus ElevenLabs: speaker ke-0 ->
        AGENT_SPEAKER_LABEL, speaker ke-1 -> CUSTOMER_SPEAKER_LABEL.
        ⚠️ Murni berdasarkan URUTAN speaker_id ("speaker_0", "speaker_1"),
        TIDAK dijamin benar kalau customer kebetulan index 0. >2 speaker ->
        fallback ke nama/id asli.
        """
        idx = None
        if speaker_id:
            tail = speaker_id.rsplit("_", 1)[-1]
            if tail.isdigit():
                idx = int(tail)
        if idx == 0:
            return AGENT_SPEAKER_LABEL
        elif idx == 1:
            return CUSTOMER_SPEAKER_LABEL
        else:
            return speaker_name or speaker_id or "unknown"

    @staticmethod
    def _extract_segments_with_speaker(response) -> List[Dict[str, Any]]:
        """
        [FIXED] Ekstrak segmen speaker dari response Scribe v2 (diarize=True).
        Coba DUA jalur (fail-safe):
          1. response.segments (kalau ada di versi SDK Anda)
          2. Fallback: response.words flat, group kata berurutan yang
             speaker_id-nya SAMA jadi 1 segmen.
        """
        segments = []
        # Jalur 1: segments[] bersarang (kalau ada)
        for seg in (getattr(response, "segments", None) or []):
            text  = (getattr(seg, "text", "") or "").strip()
            start = getattr(seg, "start_time", None)
            end   = getattr(seg, "end_time", None)
            if not text or start is None or end is None:
                continue
            speaker_id, speaker_name = ElevenLabsBackend._extract_speaker_id_name(seg)
            speaker = speaker_id or speaker_name or "speaker_0"
            segments.append({
                "start": float(start), "end": float(end),
                "speaker": str(speaker), "text": text,
                "speaker_id": speaker_id, "speaker_name": speaker_name,
            })
        if segments:
            return segments

        # Jalur 2 (FALLBACK): flat words[] dengan speaker_id per-kata.
        words_raw = getattr(response, "words", None) or []
        current_speaker = None
        current_start = None
        current_end = None
        current_text_parts: List[str] = []

        def _flush():
            if current_text_parts and current_start is not None:
                text = " ".join(current_text_parts).strip()
                if text:
                    segments.append({
                        "start": float(current_start), "end": float(current_end),
                        "speaker": str(current_speaker or "speaker_0"), "text": text,
                        "speaker_id": current_speaker, "speaker_name": None,
                    })

        for w in words_raw:
            wtype = getattr(w, "type", None)
            if wtype and wtype != "word":
                continue
            text = (getattr(w, "text", "") or "").strip()
            start = getattr(w, "start", None)
            end = getattr(w, "end", None)
            speaker_id = getattr(w, "speaker_id", None)
            if not text or start is None or end is None:
                continue
            if speaker_id != current_speaker and current_text_parts:
                _flush()
                current_text_parts = []
            if not current_text_parts:
                current_start = start
                current_speaker = speaker_id
            current_end = end
            current_text_parts.append(text)
        _flush()
        return segments

    # -----------------------------------------------------------------
    def transcribe_with_speakers(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
        num_speakers: int = 2,
    ) -> List[Dict[str, Any]]:
        response = self._call_api(audio_path, language, diarize=True)
        # [NEW] Simpan response MENTAH (sebelum di-transform) — dipakai caller
        # (engine.py) untuk disisipkan ke metadata lalu disimpan ke DB.
        self.last_raw_response = self._response_to_dict(response)
        return self._extract_segments_with_speaker(response)

    @staticmethod
    def _group_words_into_sentences(
        words: List["_ElevenLabsWord"], max_duration_sec: float = 30.0,
    ) -> List["_ElevenLabsSegment"]:
        """
        [FIXED] Kelompokkan flat words[] jadi segmen per-kalimat PAKAI
        TIMESTAMP ASLI tiap kata. Pecah di akhir kalimat (. ? !) atau saat
        melebihi max_duration_sec.
        """
        segments = []
        current_words: List["_ElevenLabsWord"] = []
        current_text_parts: List[str] = []
        for w in words:
            current_words.append(w)
            current_text_parts.append(w.word)
            stripped = w.word.rstrip()
            is_sentence_end = bool(stripped) and stripped[-1] in ".?!"
            duration_so_far = w.end - current_words[0].start
            if is_sentence_end or duration_so_far >= max_duration_sec:
                text = " ".join(current_text_parts).strip()
                if text:
                    segments.append(_ElevenLabsSegment(
                        current_words[0].start, current_words[-1].end,
                        text, list(current_words),
                    ))
                current_words = []
                current_text_parts = []
        if current_words:
            text = " ".join(current_text_parts).strip()
            if text:
                segments.append(_ElevenLabsSegment(
                    current_words[0].start, current_words[-1].end,
                    text, list(current_words),
                ))
        return segments

    def transcribe_full(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> Tuple[List[Any], Any]:
        response = self._call_api(audio_path, language, diarize=False)
        # [NEW] Simpan raw response — dipakai save_stt_pipeline_results().
        self.last_raw_response = self._response_to_dict(response)
        # [FIXED] Skema diarize=False FLAT: {"text":..., "words":[...]} di
        # top-level; tiap word punya {"start","end","text","type","speaker_id"}
        # (speaker_id selalu null di mode ini).
        words: List[_ElevenLabsWord] = []
        for w in (getattr(response, "words", None) or []):
            wtype = getattr(w, "type", None)
            if wtype and wtype != "word":
                continue  # skip entri "spacing"
            text  = getattr(w, "text", "") or ""
            start = getattr(w, "start", None)
            end   = getattr(w, "end", None)
            if text.strip() and start is not None and end is not None:
                words.append(_ElevenLabsWord(float(start), float(end), text))
        if words:
            segments = self._group_words_into_sentences(words)
        else:
            # Fallback kalau words[] kosong — pakai audio_duration_secs atau
            # baca durasi file audio langsung.
            duration = getattr(response, "audio_duration_secs", None)
            if duration is None:
                import soundfile as sf
                try:
                    duration = sf.info(audio_path).duration
                except Exception:
                    duration = 0.0
            full_text = getattr(response, "text", "") or ""
            segments = [_ElevenLabsSegment(0.0, float(duration), full_text, [])]
        # response.language_code ISO 639-3 ("ind") vs param language ISO 639-1
        # ("id") — dibiarkan apa adanya (hanya informational).
        detected_language = getattr(response, "language_code", None) or language
        info = _ElevenLabsInfo(language=detected_language, language_probability=1.0)
        return segments, info

    def transcribe_segment(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> str:
        segs, _info = self.transcribe_full(audio_path, language, initial_prompt)
        return " ".join(s.text.strip() for s in segs)