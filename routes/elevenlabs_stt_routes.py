#!/usr/bin/env python3
# =================================================================
# routes/elevenlabs_stt_routes.py
#
# Endpoint STT SINKRON langsung ke ElevenLabs Scribe — terpisah dari
# /speech/stt/save* (yang async: job -> PDF -> forward ke backend).
#
#   POST /api/v1/speech/stt/elevenlabs?language=id&diarize=false&analysis=false
#   Body: bytes audio MENTAH (bukan multipart), format apa saja.
#
# Dua mode:
#   analysis=false (default) — cepat: 1 panggilan API ElevenLabs, audio
#     diteruskan apa adanya (ElevenLabs sendiri yang mendeteksi format).
#   analysis=true — konversi WAV -> diarization ElevenLabs (diarize=true)
#     -> SpeechSTT.analyze_speakers() (gender + emotion + fraud DSP +
#     talk-time) pada segmen ElevenLabs itu. Diarization SELALU dari
#     ElevenLabs, jadi jalan apa pun ASR_BACKEND server (whisper juga);
#     engine cuma dipakai untuk model gender/emotion. Lebih lambat, jalan
#     di transcribe_executor.
#
# Label speaker di `segments` memakai map_speakers_to_binary() — aturan
# yang sama dengan engine (yang bicara duluan = agent), supaya kuncinya
# cocok dengan speaker_genders / speaker_emotions di mode analysis.
#
# ⚠️ Audio dikirim ke cloud ElevenLabs (region EU/India) — lihat
# README_ELEVENLABS_OPTION.md soal persetujuan compliance.
# =================================================================

import asyncio
import logging
import mimetypes
import os
import tempfile
import threading
import uuid
from typing import Any, Dict, List, Optional

from elevenlabs.core.api_error import ApiError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from model.speech_stt import config as stt_config
from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt.engine import scribe_segments_to_aligned
from model.speech_stt.segment import map_speakers_to_binary
from services.audio_upload import cleanup_temp_file
from services.config import MAX_UPLOAD_SIZE_BYTES
from services.executor import transcribe_executor
from services.stt_audio_prep import convert_audio_for_stt

logger = logging.getLogger(__name__)

router = APIRouter()

_backend: Optional[ElevenLabsBackend] = None
_backend_lock = threading.Lock()


class ElevenLabsSegment(BaseModel):
    start:      float
    end:        float
    speaker:    str
    speaker_id: Optional[str] = None
    text:       str
    emotion:    Optional[str] = None  # hanya analysis=true (POSITIF/NEGATIF/NETRAL)


class ElevenLabsAnalysis(BaseModel):
    speaker_genders:  Dict[str, str]
    speaker_emotions: Dict[str, Dict[str, int]]  # hitungan POSITIF/NEGATIF/NETRAL/TOTAL
    fraud_alerts:     List[Dict[str, Any]]
    talk_time:        Optional[Dict[str, Any]] = None


class ElevenLabsSTTResponse(BaseModel):
    text:                str
    language:            Optional[str] = None
    audio_duration_secs: Optional[float] = None
    segments:            Optional[List[ElevenLabsSegment]] = None
    analysis:            Optional[ElevenLabsAnalysis] = None


def get_elevenlabs_backend() -> ElevenLabsBackend:
    """Client ElevenLabs dibuat sekali per proses (cuma HTTP client ringan)."""
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is None:
            backend = ElevenLabsBackend(
                stt_config.ELEVENLABS_MODEL_ID,
                stt_config.ELEVENLABS_API_KEY,
                num_speakers     = stt_config.ELEVENLABS_NUM_SPEAKERS,
                tag_audio_events = stt_config.ELEVENLABS_TAG_AUDIO_EVENTS,
                keyterms         = stt_config.ELEVENLABS_KEYTERMS,
                timeout_sec      = stt_config.ELEVENLABS_REQUEST_TIMEOUT_SEC,
            )
            backend.load(gpu_id=-1)  # raise RuntimeError kalau API key kosong
            _backend = backend
    return _backend


def get_stt_engine(request: Request):
    """Instance SpeechSTT yang sama dengan pipeline /speech/stt/save* —
    di sini cuma dipakai untuk model gender/emotion (analyze_speakers)."""
    stt = getattr(request.app.state, "stt_instance", None)
    if stt is not None:
        return stt
    from services.stt_singleton import get_stt  # lazy: import torch
    return get_stt()


def _suffix_from_content_type(content_type: str) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return (mimetypes.guess_extension(mime) if mime else None) or ".bin"


async def _save_body_to_temp(request: Request) -> str:
    """Stream body ke temp file dengan size guard. Caller wajib cleanup."""
    fd, path = tempfile.mkstemp(
        suffix = _suffix_from_content_type(request.headers.get("content-type", "")),
        prefix = "eleven_raw_%d_" % os.getpid(),
    )
    written = 0
    try:
        with os.fdopen(fd, "wb") as tmp:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Ukuran file melebihi batas %d MB."
                               % (MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)),
                    )
                tmp.write(chunk)
    except BaseException:
        cleanup_temp_file(path)
        raise

    if written == 0:
        cleanup_temp_file(path)
        raise HTTPException(400, "Body kosong — kirim bytes audio sebagai request body.")
    return path


def _speaker_segments(response: Any) -> List[ElevenLabsSegment]:
    raw = ElevenLabsBackend._extract_segments_with_speaker(response)
    return [
        ElevenLabsSegment(
            start      = s["start"],
            end        = s["end"],
            speaker    = s["speaker"],
            speaker_id = s["speaker_original"],
            text       = s["text"],
        )
        for s in map_speakers_to_binary(raw)
    ]


def _build_response(response: Any, diarize: bool) -> ElevenLabsSTTResponse:
    return ElevenLabsSTTResponse(
        text                = (getattr(response, "text", "") or "").strip(),
        language            = getattr(response, "language_code", None),
        audio_duration_secs = getattr(response, "audio_duration_secs", None),
        segments            = _speaker_segments(response) if diarize else None,
    )


def _run_analysis(
    backend: ElevenLabsBackend, stt, audio_path: str, language: str,
) -> ElevenLabsSTTResponse:
    """Jalan di transcribe_executor (blocking: konversi + API + GPU emotion)."""
    tag = "eleven-raw-%s" % uuid.uuid4().hex[:8]
    try:
        converted = convert_audio_for_stt(audio_path, tag)
    except RuntimeError as e:
        logger.warning("[%s] Konversi gagal, fallback ke file asli: %s", tag, e)
        converted = None
    wav_path = converted or audio_path
    try:
        response = backend._call_api(wav_path, language, True)
        diar_segs, aligned, _ = scribe_segments_to_aligned(
            ElevenLabsBackend._extract_segments_with_speaker(response),
        )
        speakers = stt.analyze_speakers(wav_path, aligned, diar_segs)
    finally:
        if converted:
            cleanup_temp_file(converted)

    result = _build_response(response, diarize=False)
    # Segmen = giliran bicara yang SAMA dengan yang dianalisis emotion-nya.
    result.segments = [
        ElevenLabsSegment(
            start=seg.start, end=seg.end, speaker=seg.speaker, text=seg.text,
            emotion=emotion,
        )
        for seg, emotion in zip(aligned, speakers["segment_emotions"])
    ]
    result.analysis = ElevenLabsAnalysis(
        speaker_genders  = speakers["speaker_genders"],
        speaker_emotions = speakers["speaker_stats"],
        fraud_alerts     = speakers["fraud_alerts"],
        talk_time        = speakers["talk_time"],
    )
    return result


@router.post("/speech/stt/elevenlabs", response_model=ElevenLabsSTTResponse)
async def speech_to_text_elevenlabs(
    request:  Request,
    language: str = "id",
    diarize:  bool = False,
    analysis: bool = False,
):
    """
    Transkripsi sinkron via ElevenLabs Scribe. Body = bytes audio mentah
    (format apa saja), contoh:

        curl -X POST "http://host:8000/api/v1/speech/stt/elevenlabs?analysis=true" \\
             -H "Content-Type: audio/mpeg" --data-binary @call.mp3

    diarize=true  -> `segments` berisi label speaker (bicara duluan = agent).
    analysis=true -> diarization ElevenLabs + `analysis` (gender, emotion,
                     fraud, talk-time); `segments` = giliran bicara (sudah
                     di-merge) + `emotion` per giliran. Jalan dengan ASR_BACKEND
                     apa pun.
    """
    try:
        backend = get_elevenlabs_backend()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    stt = get_stt_engine(request) if analysis else None

    temp_path = await _save_body_to_temp(request)
    logger.info(
        "[ELEVENLABS-RAW] content-type=%s size=%d language=%s diarize=%s analysis=%s",
        request.headers.get("content-type"), os.path.getsize(temp_path),
        language, diarize, analysis,
    )
    try:
        if analysis:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                transcribe_executor, _run_analysis, backend, stt, temp_path, language,
            )
        response = await asyncio.to_thread(backend._call_api, temp_path, language, diarize)
        return _build_response(response, diarize)
    except ApiError as e:
        status = 400 if e.status_code in (400, 422) else 502
        raise HTTPException(status, "ElevenLabs error (%s): %s" % (e.status_code, e.body))
    except RuntimeError as e:  # 429 tetap gagal setelah semua retry
        raise HTTPException(503, str(e))
    finally:
        cleanup_temp_file(temp_path)
