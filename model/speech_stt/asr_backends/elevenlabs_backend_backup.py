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
regulasi OJK) sebelum dipakai untuk data produksi asli. `load()` di
bawah SENGAJA memvalidasi API key dengan pesan yang mengingatkan hal
ini — tapi validasi compliance itu sendiri ada di LUAR kode ini,
tanggung jawab operator yang men-deploy.

── SKEMA RESPONSE ASLI (dikonfirmasi dari sample JSON nyata, BUKAN lagi
asumsi) ──
    {
      "language_code": "ind",
      "segments": [
        {
          "text": "Halo selamat sore dengan Ibu Suliana? ",
          "start_time": 3.86, "end_time": 7.38,
          "speaker": {"id": "speaker_0", "name": "Speaker 0"},
          "words": [
            {"text": "Halo", "start_time": 3.86, "end_time": 4.04},
            {"text": " ",    "start_time": 4.04, "end_time": 4.5},
            ...
          ]
        },
        ...
      ]
    }

Perbedaan dari asumsi awal (draft pertama modul ini):
  - Top-level key adalah `segments`, BUKAN `words` flat.
  - Tiap SEGMEN sudah berisi speaker + teks lengkap + start_time/end_time
    — ElevenLabs SENDIRI yang sudah menggabungkan kata jadi giliran-bicara
    per speaker. Tidak perlu rekonstruksi manual (_merge_words_by_speaker
    versi lama DIHAPUS, sudah tidak relevan).
  - `words[]` cuma nested di dalam tiap segmen (dipakai HANYA untuk mode
    simple/non-diarization yang butuh word-level timestamp), bukan
    struktur utama untuk mode diarization.
  - Field waktu namanya `start_time`/`end_time`, BUKAN `start`/`end`.
  - `speaker` adalah OBJEK `{"id":..., "name":...}`, bukan string flat.
  - Tidak ada field `type` untuk membedakan entri kata vs spasi — entri
    spasi punya `text=" "` dan otomatis ke-filter oleh `.strip()`.

Beda arsitektural lain dari backend lain:
- Tidak ada model berat yang perlu dimuat ke GPU/VRAM — cuma HTTP client
  ringan. Makanya TIDAK ada Factory/shared-loading di model_registry.py
  untuk backend ini (beda dari whisper).
- `supports_word_timestamps = True` TAPI `provides_own_diarization = True`
  juga — engine.py mengecek flag kedua ini LEBIH DULU, jadi jalur
  alignment kata (pure-overlap) maupun jalur per-segmen (ala MERaLiON)
  SAMA SEKALI tidak dipakai untuk backend ini; speaker label sudah final
  dari API, langsung per-segmen.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from model.speech_stt.asr_backends.base import ASRBackend
from model.speech_stt.config import AGENT_SPEAKER_LABEL, CUSTOMER_SPEAKER_LABEL

logger = logging.getLogger(__name__)


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
            "[ElevenLabs] Client siap (model=%s, num_speakers=%d). "
            "Audio akan dikirim ke cloud ElevenLabs — pastikan sudah "
            "disetujui compliance untuk data produksi.",
            self.model_id, self.num_speakers,
        )

    def is_available(self) -> bool:
        return self._client is not None

    # -----------------------------------------------------------------
    def _call_api(self, audio_path: str, language: str, diarize: bool):
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
            try:
                return self._client.speech_to_text.convert(**kwargs)
            except TypeError as e:
                # Fallback defensif — param num_speakers/keyterms/tag_audio_events
                # bisa saja beda nama/tidak ada di versi SDK tertentu. Skema resmi
                # bisa berubah antar rilis; ini best-effort, BUKAN jaminan.
                logger.warning(
                    "[ElevenLabs] Parameter API tidak dikenali SDK (%s), "
                    "retry dengan parameter minimal.", e,
                )
                f.seek(0)
                return self._client.speech_to_text.convert(
                    file=f, model_id=self.model_id,
                    language_code=language, diarize=diarize,
                )

    @staticmethod
    def _response_to_dict(response) -> Dict[str, Any]:
        """
        [NEW] Konversi response SDK ElevenLabs (biasanya Pydantic model)
        jadi dict biasa yang JSON-serializable, untuk disimpan ke DB apa
        adanya. Coba beberapa method berbeda karena versi SDK bisa beda
        konvensi (Pydantic v2 pakai model_dump(), v1 pakai dict()) — ini
        BELUM divalidasi terhadap SDK asli (butuh API key asli untuk tes),
        jadi sengaja dibuat defensif dengan fallback berlapis, bukan
        asumsi satu method pasti benar.
        """
        for method_name in ("model_dump", "dict", "to_dict"):
            method = getattr(response, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    continue
        # Fallback terakhir: coba serialize langsung, kalau gagal simpan
        # representasi string apa adanya (tidak fatal, cuma kurang berguna
        # buat query JSONB nanti).
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
        transcribe_with_speakers() (mode diarization) — field ini selalu
        ada di response Scribe v2 (dikonfirmasi dari sample nyata),
        terlepas dari parameter `diarize` yang dikirim.
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
        [NEW] Pemetaan POSISIONAL khusus ElevenLabs: speaker ke-0 (index
        pertama) -> AGENT_SPEAKER_LABEL, speaker ke-1 -> CUSTOMER_SPEAKER_LABEL.

        ⚠️ BEDA dari map_speakers_to_binary() (dipakai backend lain) yang
        menentukan Agent berdasarkan SIAPA BICARA PALING LAMA. Ini murni
        berdasarkan URUTAN speaker_id dari ElevenLabs ("speaker_0",
        "speaker_1", dst) — TIDAK dijamin selalu benar kalau ternyata
        customer yang kebetulan diberi index 0 oleh API. Dipakai di sini
        HANYA karena diminta eksplisit khusus untuk backend ElevenLabs.

        Kalau ada speaker ke-3+ (>2 speaker terdeteksi — jarang untuk
        telemarketing 1-lawan-1), fallback ke speaker_name/speaker_id asli
        alih-alih memaksakan salah satu dari 2 label itu.
        """
        idx = None
        if speaker_id:
            # ekstrak angka dari akhir "speaker_0", "speaker_1", dst
            tail = speaker_id.rsplit("_", 1)[-1]
            if tail.isdigit():
                idx = int(tail)

        if idx == 0:
            return AGENT_SPEAKER_LABEL
        elif idx == 1:
            return CUSTOMER_SPEAKER_LABEL
        else:
            # >2 speaker atau index tidak terbaca — jangan menebak, kembalikan asli
            return speaker_name or speaker_id or "unknown"

    @staticmethod
    def _extract_segments_with_speaker(response) -> List[Dict[str, Any]]:
        """
        [FIXED] Ekstrak segmen speaker dari response Scribe v2 (diarize=True).

        Dua test nyata terhadap API (dikonfirmasi user) menunjukkan skema
        respons SEBENARNYA adalah flat "words[]" di top-level (SAMA seperti
        diarize=False), bukan "segments[]" bersarang seperti asumsi awal
        (sample yang dipakai sebelumnya kemungkinan tidak representatif
        untuk versi API/SDK yang sebenarnya dipakai). Bedanya cuma:
        diarize=False -> speaker_id null di semua kata,
        diarize=True  -> speaker_id terisi ("speaker_0", dst) per kata.

        Coba DUA jalur (fail-safe, bukan tebak salah satu):
          1. response.segments (kalau ternyata ADA di versi SDK Anda)
          2. Fallback: response.words flat, group kata berurutan yang
             speaker_id-nya SAMA jadi 1 segmen (persis draft PALING AWAL
             kode ini sebelum "disederhanakan" — ternyata itu yang benar).
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

        # Jalur 2 (FALLBACK, terbukti dari 2 test nyata): flat words[]
        # dengan speaker_id per-kata. Group kata berurutan speaker sama
        # jadi 1 segmen.
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
        # [NEW] Simpan response MENTAH (sebelum di-transform jadi segments
        # internal) — dipakai caller (engine.py) untuk disisipkan ke
        # metadata, lalu disimpan ke DB apa adanya oleh services/db.py.
        self.last_raw_response = self._response_to_dict(response)
        return self._extract_segments_with_speaker(response)

    @staticmethod
    def _group_words_into_sentences(
        words: List["_ElevenLabsWord"], max_duration_sec: float = 30.0,
    ) -> List["_ElevenLabsSegment"]:
        """
        [FIXED] Kelompokkan flat words[] jadi segmen per-kalimat PAKAI
        TIMESTAMP ASLI dari tiap kata — bukan character-proportional
        guessing seperti sebelumnya. Pecah di akhir kalimat (. ? !) atau
        kalau sudah melebihi max_duration_sec (biar tidak ada 1 segmen
        yang terlalu panjang kalau tidak ada tanda baca sama sekali).
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

        # Sisa kata yang belum ke-flush (kalimat terakhir tanpa tanda baca)
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

        # [FIXED] Skema response untuk diarize=False TERNYATA beda TOTAL
        # dari diarize=True — dikonfirmasi dari response API asli (bukan
        # asumsi lagi). TIDAK ADA key "segments" sama sekali; struktur
        # sebenarnya FLAT: {"text": ..., "words": [...]} di top-level,
        # tiap word punya {"start","end","text","type","speaker_id"}
        # (speaker_id selalu null di mode ini, karena diarize=False).
        # Kode SEBELUMNYA salah asumsi ada "segments" (pola dari respons
        # diarize=True), sehingga selalu jatuh ke fallback 1-segmen-raksasa
        # + tebak-tebak proporsional — BUKAN pakai timestamp asli yang
        # sebenarnya tersedia persis di sini.
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
            # Fallback terakhir kalau ternyata words[] juga kosong/tidak
            # terduga — pakai audio_duration_secs (field asli response
            # ini) atau baca file audio langsung kalau itu pun tidak ada.
            duration = getattr(response, "audio_duration_secs", None)
            if duration is None:
                import soundfile as sf
                try:
                    duration = sf.info(audio_path).duration
                except Exception:
                    duration = 0.0
            full_text = getattr(response, "text", "") or ""
            segments = [_ElevenLabsSegment(0.0, float(duration), full_text, [])]

        # Catatan: response.language_code memakai ISO 639-3 (mis. "ind"),
        # sedangkan parameter `language` yang dikirim biasanya ISO 639-1
        # ("id") — dua konvensi berbeda. Dibiarkan apa adanya (bukan
        # dikonversi paksa) supaya tidak menebak-nebak mapping yang belum
        # divalidasi; hanya relevan untuk field informational, tidak
        # mempengaruhi hasil transkrip.
        detected_language = getattr(response, "language_code", None) or language
        info = _ElevenLabsInfo(language=detected_language, language_probability=1.0)
        return segments, info

    def transcribe_segment(
        self, audio_path: str, language: str, initial_prompt: Optional[str] = None,
    ) -> str:
        segs, _info = self.transcribe_full(audio_path, language, initial_prompt)
        return " ".join(s.text.strip() for s in segs)