#!/usr/bin/env python3
"""
model/speech_stt/engine.py

[FACADE PATTERN]
SpeechSTT sekarang jadi orchestrator TIPIS — semua "cara kerja detail"
(load model, alignment, DSP gender/fraud, emotion, format output) sudah
pindah ke modul masing-masing. Class ini cuma mengatur URUTAN pemanggilan
persis sama seperti versi asli, supaya publicnya (constructor + method
publik: load_model, transcribe_with_diarization, transcribe_simple_complete,
process_audio_file, save_to_temp_file, cleanup_temp_file, close) tetap
kompatibel dengan pemanggil lama (services/pipeline.py, server.py).

TIDAK ADA PERUBAHAN LOGIC/URUTAN dari versi asli.
"""

import os
import time
import uuid
import weakref
import tempfile
import traceback
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
from fastapi import HTTPException, UploadFile

from model.speech_stt.config import VAD_PARAMS_TELEPHONY, VAD_PARAMS_CLEAN, \
    _UPLOAD_CHUNK_SIZE, DSP_FRAUD_ENABLED, DSP_FRAUD_MIN_DUR_SEC, \
    DSP_FRAUD_COOLDOWN_SEGS
from model.speech_stt.segment import SpeakerSegment, map_speakers_to_binary
from model.speech_stt.tempfiles import cleanup_temp_file as _cleanup_temp_file_fn
from model.speech_stt.audio_conversion import convert_audio_to_wav
from model.speech_stt.audio_slicing import extract_single_segment_audio
from model.speech_stt.dsp_analysis import analyze_gender_and_fraud_dsp
from model.speech_stt.gender_detection import run_gender_detection
from model.speech_stt.emotion_detection import get_telemarketing_conclusion
from model.speech_stt.formatter import compute_talk_time, accum_speaker_stats, \
    format_output_with_speakers, split_long_segment, format_time
from model.speech_stt.quality_checks import is_hallucination, validate_diarization_output
from model.speech_stt.model_registry import (
    _SHARED_MODELS, _SHARED_MODELS_LOCK, _SHARED_PYANNOTE, _SHARED_GENDER,
    _SHARED_EMOTION, get_shared_models, unregister_worker, cleanup_worker_on_gc,
)
from model.speech_stt.alignment.pure_alignment import PureOverlapAlignmentStrategy, \
    merge_same_speaker_simple
from model.speech_stt.alignment.pitch_verification import pitch_verify_speakers
from model.speech_stt.asr_backends.whisper_backend import WhisperBackend
from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt import config as stt_config

logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger(__name__)


class SpeechSTT:
    def __init__(
        self,
        model_name:      str,
        max_file_size:   int,
        worker_id:       int           = 0,
        gpu_id:          Optional[int] = None,
        audio_profile:   str           = "telephony",
        max_concurrent:  int           = 1,
        workers_per_gpu: int           = 1,
        asr_backend:     Optional[str] = None,
        diarization_backend: Optional[str] = None,
    ):
        self.worker_id      = worker_id
        self.model_name     = model_name
        self.MAX_FILE_SIZE  = max_file_size * 1024 * 1024
        self.audio_profile  = audio_profile
        self.vad_params     = (
            VAD_PARAMS_TELEPHONY if audio_profile == "telephony" else VAD_PARAMS_CLEAN)
        logger.info("[INIT] audio_profile=%s | vad_params=%s", audio_profile, self.vad_params)
        # [NEW] Pemilihan ASR backend — default "whisper" (behavior identik
        # versi sebelumnya). Set asr_backend="elevenlabs" atau env var
        # ASR_BACKEND=elevenlabs untuk pakai ElevenLabs Scribe v2 (cloud API).
        self.asr_backend_name = (asr_backend or stt_config.ASR_BACKEND).strip().lower()
        # [NEW] Pemilihan diarization backend — default "pyannote" (behavior
        # identik versi sebelumnya). Set diarization_backend="nemo" atau env
        # var DIARIZATION_BACKEND=nemo untuk pakai NeMo NeuralDiarizer (MSDD
        # telephonic).
        self.diarization_backend_name = (
            diarization_backend or stt_config.DIARIZATION_BACKEND
        ).strip().lower()
        self.gpu_id = gpu_id if gpu_id is not None else 0
        if torch.cuda.is_available():
            device_count = torch.cuda.device_count()
            if self.gpu_id >= device_count:
                logger.warning("gpu_id=%d >= device_count=%d, clamp ke 0",
                               self.gpu_id, device_count)
                self.gpu_id = 0
            self.device = torch.device("cuda:%d" % self.gpu_id)
        else:
            self.device = torch.device("cpu")
        self.model              = None
        self.pyannote_pipeline  = None
        self.gender_classifier  = None
        self.emotion_classifier = None
        self.nemo_diarizer      = None
        self._pitch_history: Dict[str, Any] = {}
        self.nemo_available     = False
        self._closed            = False
        self._asr               = None  # ASRBackend, di-set penuh di load_model()
        self._diarizer          = None  # DiarizationBackend, di-set penuh di load_model()
        self._finalizer = weakref.finalize(self, cleanup_worker_on_gc, self.gpu_id)

        # Strategi alignment aktif — lihat model/speech_stt/alignment/
        # untuk LegacyHeuristicAlignmentStrategy (tidak dipakai pipeline ini).
        self._alignment_strategy = PureOverlapAlignmentStrategy()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._finalizer()

    def load_model(self):
        pyannote_loaded_in_phase1 = False

        # ── Phase 1: ASR backend ──
        if self.asr_backend_name == "elevenlabs":
            # [NEW] Cloud API — TIDAK ada model berat, cuma validasi API
            # key + init client ringan. Lihat peringatan data-residency di
            # asr_backends/elevenlabs_backend.py.
            self._asr = ElevenLabsBackend(
                stt_config.ELEVENLABS_MODEL_ID,
                stt_config.ELEVENLABS_API_KEY,
                num_speakers=stt_config.ELEVENLABS_NUM_SPEAKERS,
                tag_audio_events=stt_config.ELEVENLABS_TAG_AUDIO_EVENTS,
                keyterms=stt_config.ELEVENLABS_KEYTERMS,
                timeout_sec=stt_config.ELEVENLABS_REQUEST_TIMEOUT_SEC,
            )
            self._asr.load(self.gpu_id)
            self.model = None

        else:
            # ── Backend default: faster-whisper "vanilla" ──
            if self.diarization_backend_name == "pyannote":
                # ── Jalur ASLI 100% default (whisper+pyannote) — TIDAK DIUBAH ──
                shared = get_shared_models(self.gpu_id, self.model_name)
                self.model = shared["whisper"]
                self.gender_classifier  = _SHARED_GENDER.get(self.gpu_id)
                self.emotion_classifier = _SHARED_EMOTION.get(self.gpu_id)
                pyannote_loaded_in_phase1 = True
                with _SHARED_MODELS_LOCK:
                    if not shared["warmup_done"]:
                        shared["warmup_done"] = True
                        logger.info("[GPU %d] Warmup selesai.", self.gpu_id)
            else:
                # whisper + nemo: whisper dimuat SENDIRI, tidak ikut memuat
                # pyannote yang tidak akan dipakai (hemat VRAM).
                from model.speech_stt.model_registry import get_shared_whisper_only
                whisper_shared = get_shared_whisper_only(self.gpu_id, self.model_name)
                self.model = whisper_shared["whisper"]
                with _SHARED_MODELS_LOCK:
                    if not whisper_shared["warmup_done"]:
                        whisper_shared["warmup_done"] = True
                        logger.info("[GPU %d] Warmup selesai.", self.gpu_id)
            self._asr = WhisperBackend(self.model_name, self.gpu_id, self.vad_params)
            self._asr.model = self.model

        # ── Phase 2: gender + emotion classifier (skip kalau sudah ke-load
        # bareng whisper bundle default di Phase 1) ──
        if not pyannote_loaded_in_phase1:
            from model.speech_stt.model_registry import get_shared_gender_emotion_only
            support = get_shared_gender_emotion_only(self.gpu_id)
            self.gender_classifier  = support["gender"]
            self.emotion_classifier = support["emotion"]

        # ── Phase 3: diarization backend ──
        if getattr(self._asr, "provides_own_diarization", False):
            # [NEW] Backend ASR (mis. ElevenLabs) sudah menyediakan speaker
            # label sendiri dalam 1 API call — TIDAK perlu pyannote/NeMo
            # sama sekali. self._diarizer sengaja dibiarkan None; caller
            # (transcribe_with_diarization) mengecek provides_own_diarization
            # LEBIH DULU sebelum menyentuh self._diarizer.
            self._diarizer = None
            # Sentinel truthy — dipakai backward-compat oleh caller lain
            # (services/pipeline.py, diagnostics_routes.py) yang cuma cek
            # `if stt.pyannote_pipeline:` sebagai flag "diarization tersedia",
            # BUKAN benar-benar pyannote Pipeline object.
            self.pyannote_pipeline = self._asr if self._asr.is_available() else None
        elif self.diarization_backend_name == "nemo":
            from model.speech_stt.diarization_backends.nemo_backend import NeMoDiarizationBackend
            self._diarizer = NeMoDiarizationBackend(stt_config.NEMO_DIARIZER_MODEL_NAME, self.gpu_id)
            self._diarizer.load(self.gpu_id)
        else:
            from model.speech_stt.diarization_backends.pyannote_backend import PyannoteDiarizationBackend
            self._diarizer = PyannoteDiarizationBackend(self.gpu_id)
            if pyannote_loaded_in_phase1:
                # pyannote sudah ke-load bareng whisper bundle di Phase 1
                # (jalur asli) — tinggal pakai, tidak perlu load ulang.
                self._diarizer.pipeline = _SHARED_PYANNOTE.get(self.gpu_id)
            else:
                self._diarizer.load(self.gpu_id)

        if self._diarizer is not None:
            self.pyannote_pipeline = self._diarizer if self._diarizer.is_available() else None
        self.nemo_available    = self.pyannote_pipeline is not None

        # [FIXED] Sebelumnya log self.diarization_backend_name APA ADANYA
        # dari config, tanpa cek apakah ASR backend ini sebenarnya skip
        # diarization total (ElevenLabs, provides_own_diarization=True).
        # Akibatnya log startup bilang "Diarization backend: pyannote"
        # padahal baris "Loading pyannote/speaker-diarization-3.1..."
        # tidak pernah muncul sama sekali — pyannote TIDAK PERNAH dimuat.
        # Sama seperti fix di diagnostics_routes.py sebelumnya.
        diarization_backend_logged = (
            "elevenlabs (diarize=true, built-in)"
            if self.asr_backend_name == "elevenlabs"
            else self.diarization_backend_name
        )
        logger.info(
            "STT loaded | GPU:%d | ASR backend: %s | Diarization backend: %s | "
            "diarization: %s | emotion: %s",
            self.gpu_id, self.asr_backend_name, diarization_backend_logged,
            "ok" if self.pyannote_pipeline  is not None else "N/A",
            "ok" if self.emotion_classifier is not None else "N/A",
        )

    def _check_audio_has_speech(self, audio_path: str, threshold_rms: float = 0.0005) -> bool:
        try:
            data, _ = sf.read(audio_path, always_2d=False)
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            logger.info("[Worker:%d] Audio RMS: %.6f (threshold: %.6f)",
                        self.worker_id, rms, threshold_rms)
            if rms < threshold_rms:
                logger.warning("[Worker:%d] RMS sangat rendah (%.6f).", self.worker_id, rms)
                return False
            return True
        except Exception as e:
            logger.warning("[Worker:%d] _check_audio_has_speech gagal: %s", self.worker_id, e)
            return True

    def perform_diarization(self, audio_file_path: str) -> List[Dict[str, Any]]:
        if self._diarizer is None or not self._diarizer.is_available():
            logger.warning("[Worker:%d] Diarization backend (%s) tidak tersedia.",
                            self.worker_id, self.diarization_backend_name)
            return []
        try:
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", audio_file_path],
                    capture_output=True, timeout=10,
                )
                duration_sec = float(result.stdout.decode().strip())
            except Exception:
                duration_sec = 999.0
            if duration_sec < stt_config.DIARIZATION_MIN_AUDIO_DURATION_SEC:
                logger.warning(
                    "[Worker:%d] Audio terlalu pendek (%.1fs < %.1fs), skip diarization.",
                    self.worker_id, duration_sec, stt_config.DIARIZATION_MIN_AUDIO_DURATION_SEC)
                return []
            logger.info("[Worker:%d] Memulai diarization (%s)...",
                        self.worker_id, self.diarization_backend_name)
            segments = self._diarizer.diarize(audio_file_path, num_speakers=2)
            segments = map_speakers_to_binary(segments)
            logger.info("[Worker:%d] Diarization selesai: %d segmen.",
                        self.worker_id, len(segments))
            return segments
        except Exception as e:
            logger.error("[Worker:%d] perform_diarization error: %s\n%s",
                         self.worker_id, e, traceback.format_exc())
            return []

    def _build_transcribe_kwargs(
        self, language: str, initial_prompt: Optional[str] = None
    ) -> dict:
        if isinstance(self._asr, WhisperBackend):
            return self._asr._build_kwargs(language, initial_prompt)
        raise NotImplementedError(
            "_build_transcribe_kwargs hanya berlaku untuk ASR backend 'whisper' "
            "(backend aktif: %s)" % self.asr_backend_name
        )

    def transcribe_with_diarization(
        self,
        audio_path:        str,
        language:          str           = "id",
        initial_prompt:    Optional[str] = None,
        original_filename: Optional[str] = None,
    ) -> Tuple[str, float, dict]:
        import concurrent.futures

        start = time.time()

        if not self._check_audio_has_speech(audio_path):
            return "", 0.0, {
                "mode": "diarization+transcription",
                "processing_time": time.time() - start,
                "worker_id": self.worker_id, "gpu_id": self.gpu_id,
                "warning": "phase_cancellation_detected",
            }

        if getattr(self._asr, "provides_own_diarization", False):
            # ════════════════════════════════════════════════════════
            # [NEW] Backend ASR sudah menyediakan diarization sendiri
            # (mis. ElevenLabs Scribe) — SATU panggilan API menghasilkan
            # teks + speaker label sekaligus. Tidak menyentuh pyannote/
            # NeMo, tidak menyentuh alignment strategy manapun sama sekali.
            # ════════════════════════════════════════════════════════
            raw_segments = self._asr.transcribe_with_speakers(
                audio_path, language, initial_prompt, num_speakers=2,
            )
            raw_segments = map_speakers_to_binary(raw_segments)
            # [FIX] diar_segs WAJIB di-set di sini — dipakai run_gender_detection()
            # di bawah (butuh list of dict {"start","end","speaker"} per speaker
            # untuk ekstrak audio & analisis pitch). Tanpa ini, gender detection
            # akan NameError untuk backend yang provides_own_diarization=True.
            diar_segs = raw_segments
            aligned = [
                SpeakerSegment(s["start"], s["end"], s.get("text", "").strip(),
                                s["speaker"], confidence=0.9)
                for s in raw_segments if s.get("text", "").strip()
            ]
            reattributed_count     = 0
            segments_before_merge  = len(aligned)
            aligned = merge_same_speaker_simple(aligned, max_gap_sec=0.6)

            detected_language     = language
            language_probability = 1.0

        elif self._asr.supports_word_timestamps:
            # ════════════════════════════════════════════════════════
            # Jalur ASLI (Whisper) — TIDAK ADA PERUBAHAN LOGIC dari versi
            # sebelumnya. v7.0 PURE MODE: pyannote overlap saja + 1
            # koreksi aman (lihat model/speech_stt/alignment/pure_alignment.py)
            # ════════════════════════════════════════════════════════
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                fut_diar = ex.submit(self.perform_diarization, audio_path)
                fut_trans = ex.submit(
                    self._asr.transcribe_full, audio_path, language, initial_prompt,
                )
                diar_segs          = fut_diar.result()
                whisper_segs, info = fut_trans.result()

            aligned, reattributed_count = self._alignment_strategy.align(
                whisper_segs, diar_segs, audio_path=audio_path,
            )

            # v7.1: Pitch-based verification — perkuat pyannote dgn sinyal
            #     fisik F0. Fail-safe: kalau pitch 2 speaker mirip, no-op.
            aligned, pitch_corrections = pitch_verify_speakers(aligned, audio_path)
            reattributed_count += pitch_corrections
            segments_before_merge = len(aligned)
            aligned = merge_same_speaker_simple(aligned, max_gap_sec=0.6)

            detected_language     = info.language
            language_probability  = info.language_probability

        else:
            # ════════════════════════════════════════════════════════
            # [NEW] Jalur backend TANPA word timestamp (backend yang
            # supports_word_timestamps=False, mis. instruction-following
            # Speech-LLM). pyannote tetap menentukan batas waktu & speaker tiap segmen
            # (sudah akurat) — ASR backend hanya dipanggil PER-SEGMEN
            # untuk transkrip teksnya. Tidak ada word-alignment/pitch-
            # verification/reattribution sama sekali karena speaker sudah
            # pasti benar dari pyannote (bukan hasil overlap kata).
            # ════════════════════════════════════════════════════════
            diar_segs = self.perform_diarization(audio_path)
            aligned: List[SpeakerSegment] = []
            for seg in diar_segs:
                seg_audio = extract_single_segment_audio(
                    audio_path, seg["start"], seg["end"], seg["speaker"])
                if seg_audio is None:
                    continue
                try:
                    text = self._asr.transcribe_segment(seg_audio, language, initial_prompt)
                finally:
                    self.cleanup_temp_file(seg_audio)
                if text and text.strip():
                    aligned.append(SpeakerSegment(
                        seg["start"], seg["end"], text.strip(), seg["speaker"],
                        confidence=0.9,
                    ))

            reattributed_count     = 0
            segments_before_merge  = len(aligned)
            aligned = merge_same_speaker_simple(aligned, max_gap_sec=0.6)

            detected_language     = language
            language_probability  = 1.0

        # Gender detection
        self._pitch_history.clear()
        gender_map = run_gender_detection(audio_path, diar_segs, self._pitch_history)

        # Emotion + Fraud
        classifier      = _SHARED_EMOTION.get(self.gpu_id)
        segment_emotions: List[str]      = []
        speaker_stats:    Dict[str, dict] = {}
        fraud_alerts:     List[Dict]      = []
        fraud_cooldown:   Dict[str, int]  = {}
        self._pitch_history.clear()

        logger.info("[Worker:%d] Memulai emotion+fraud detection (%d segmen)...",
                    self.worker_id, len(aligned))

        for seg in aligned:
            if is_hallucination(seg.text):
                segment_emotions.append("NETRAL")
                accum_speaker_stats(speaker_stats, seg.speaker, "NETRAL")
                continue

            tmp_seg_wav = extract_single_segment_audio(
                audio_path, seg.start, seg.end, seg.speaker)

            seg_dur     = seg.end - seg.start
            in_cooldown = fraud_cooldown.get(seg.speaker, 0) > 0
            if in_cooldown:
                fraud_cooldown[seg.speaker] -= 1

            if DSP_FRAUD_ENABLED:
                if tmp_seg_wav and seg_dur >= DSP_FRAUD_MIN_DUR_SEC and not in_cooldown:
                    try:
                        dsp_result = analyze_gender_and_fraud_dsp(
                            seg.speaker, tmp_seg_wav, self._pitch_history,
                            seg_timestamp=seg.start,
                        )
                        if "suspect_fraud" in dsp_result:
                            fraud_alerts.append({
                                "speaker":   seg.speaker,
                                "timestamp": format_time(seg.start),
                                "type":      "pitch_unstable" if "unstable" in dsp_result
                                             else "pitch_jump",
                                "detail":    dsp_result,
                            })
                            fraud_cooldown[seg.speaker] = DSP_FRAUD_COOLDOWN_SEGS
                            logger.warning("[Fraud] %s @ %s: %s", seg.speaker,
                                           format_time(seg.start), dsp_result)
                    except Exception as e:
                        logger.warning("[Fraud/DSP] Error: %s", e)
                elif tmp_seg_wav and seg_dur < DSP_FRAUD_MIN_DUR_SEC:
                    try:
                        analyze_gender_and_fraud_dsp(
                            seg.speaker, tmp_seg_wav, self._pitch_history,
                            seg_timestamp=seg.start)
                    except Exception:
                        pass

            if tmp_seg_wav and classifier is not None:
                try:
                    results     = classifier(tmp_seg_wav)
                    emo_metrics: Dict[str, float] = {}
                    for res in results:
                        lbl = res["label"].lower().strip()
                        if "arousal"   in lbl: emo_metrics["arousal"]   = float(res["score"])
                        elif "dominance" in lbl: emo_metrics["dominance"] = float(res["score"])
                        elif "valence"   in lbl: emo_metrics["valence"]   = float(res["score"])
                    for key in ("arousal", "dominance", "valence"):
                        emo_metrics.setdefault(key, 0.5)
                    conclusion = get_telemarketing_conclusion(emo_metrics)
                except Exception as e:
                    logger.warning("[Emotion/seg] Error: %s", e)
                    conclusion = "NETRAL"
                finally:
                    self.cleanup_temp_file(tmp_seg_wav)
            else:
                if tmp_seg_wav:
                    self.cleanup_temp_file(tmp_seg_wav)
                conclusion = "NETRAL"

            segment_emotions.append(conclusion)
            accum_speaker_stats(speaker_stats, seg.speaker, conclusion)

        # Talk-time
        talk_time = compute_talk_time(aligned, audio_path)
        logger.info("[TalkTime] %s", talk_time)
        if fraud_alerts:
            logger.warning("[Fraud] Total alerts: %d", len(fraud_alerts))

        audio_meta = {
            "file_name":    original_filename or os.path.basename(audio_path),
            "language":     detected_language,
            "proc_time":    "%.2fs" % (time.time() - start),
            "talk_time":    talk_time,
            "mode":         "diarization+transcription",
            "fraud_alerts": fraud_alerts,
        }
        text, stats = format_output_with_speakers(
            aligned, gender_map, segment_emotions, speaker_stats, audio_meta
        )

        # FIX #5: validasi dengan pre-merge denominator
        stats = validate_diarization_output(
            stats, aligned, reattributed_count, segments_before_merge)

        # [NEW] Response API mentah ElevenLabs — HANYA terisi kalau backend
        # ini yang aktif; NULL untuk semua backend lain (whisper, dst).
        # Dipakai services/pipeline.py untuk disimpan ke dashboard.result_speech_to_text.
        elevenlabs_raw_response = None
        if self.asr_backend_name == "elevenlabs":
            elevenlabs_raw_response = getattr(self._asr, "last_raw_response", None)

        return text, language_probability, {
            "mode":               "diarization+transcription",
            "processing_time":    time.time() - start,
            "detected_language":  detected_language,
            "worker_id":          self.worker_id,
            "gpu_id":             self.gpu_id,
            "speaker_genders":    gender_map,
            "speaker_stats_aggr": speaker_stats,
            "talk_time":          talk_time,
            "reattributed_count": reattributed_count,
            "fraud_alerts":       fraud_alerts,
            "elevenlabs_raw_response": elevenlabs_raw_response,
            **stats,
        }

    def transcribe_simple_complete(
        self, audio_path: str, language: str = "id", initial_prompt: Optional[str] = None
    ) -> Tuple[str, float, dict]:
        start = time.time()
        if not self._check_audio_has_speech(audio_path):
            return "", 0.0, {
                "mode": "simple", "processing_time": time.time() - start,
                "audio_profile": self.audio_profile,
                "worker_id": self.worker_id, "gpu_id": self.gpu_id,
                "warning": "phase_cancellation_detected",
            }
        whisper_segs, info = self._asr.transcribe_full(audio_path, language, initial_prompt)
        lines, skipped = [], 0
        for s in whisper_segs:
            if is_hallucination(s.text):
                skipped += 1
                continue
            sub_lines = split_long_segment(s.start, s.end, s.text.strip())
            lines.extend(sub_lines)
        if skipped:
            logger.info("[Worker:%d] %d segmen hallucination di-skip.", self.worker_id, skipped)
        # [NEW] Sama seperti transcribe_with_diarization() — cuma terisi
        # kalau backend ElevenLabs, NULL untuk backend lain.
        elevenlabs_raw_response = None
        if self.asr_backend_name == "elevenlabs":
            elevenlabs_raw_response = getattr(self._asr, "last_raw_response", None)

        return "\n".join(lines), info.language_probability, {
            "mode": "simple", "processing_time": time.time() - start,
            "audio_profile": self.audio_profile,
            "worker_id": self.worker_id, "gpu_id": self.gpu_id,
            "segments_skipped": skipped,
            "elevenlabs_raw_response": elevenlabs_raw_response,
        }

    def process_audio_file(
        self, file: UploadFile, language: str = "id", enable_diarization: bool = False,
    ) -> Tuple[str, float, dict]:
        temp_path = self.save_to_temp_file(file)
        try:
            if enable_diarization and self.pyannote_pipeline:
                return self.transcribe_with_diarization(temp_path, language)
            return self.transcribe_simple_complete(temp_path, language)
        finally:
            self.cleanup_temp_file(temp_path)

    def save_to_temp_file(self, file: UploadFile) -> str:
        unique_id        = uuid.uuid4().hex[:8]
        fd_raw, raw_path = tempfile.mkstemp(
            suffix=".raw", prefix="stt_raw_%s_" % unique_id)
        try:
            total_size = 0
            with os.fdopen(fd_raw, "wb") as f:
                while True:
                    chunk = file.file.read(_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > self.MAX_FILE_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail="File terlalu besar (max %d MB)." % (
                                self.MAX_FILE_SIZE // (1024 * 1024)))
                    f.write(chunk)
            if total_size == 0:
                raise HTTPException(status_code=400, detail="File kosong.")
            fd_wav, wav_path = tempfile.mkstemp(
                suffix=".wav", prefix="stt_%s_" % unique_id)
            os.close(fd_wav)
            try:
                convert_audio_to_wav(raw_path, wav_path)
            except subprocess.TimeoutExpired:
                self.cleanup_temp_file(wav_path)
                raise HTTPException(status_code=408, detail="Konversi audio timeout.")
            except Exception as e:
                self.cleanup_temp_file(wav_path)
                logger.error("[Worker:%d] Konversi audio gagal (%s): %s",
                             self.worker_id, type(e).__name__, e)
                raise HTTPException(
                    status_code=422,
                    detail="Audio corrupt atau format tidak didukung: %s" % type(e).__name__)
            return wav_path
        finally:
            self.cleanup_temp_file(raw_path)

    def cleanup_temp_file(self, path: str):
        _cleanup_temp_file_fn(path)