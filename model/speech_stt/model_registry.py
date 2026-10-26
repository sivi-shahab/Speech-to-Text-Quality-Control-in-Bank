#!/usr/bin/env python3
"""
model/speech_stt/model_registry.py

[FACTORY PATTERN]
Semua fungsi "muat model dan simpan supaya bisa dipakai ulang lintas
thread/worker" (Whisper, pyannote, gender, emotion) dikelompokkan di sini.
Sebelumnya fungsi-fungsi ini + dict/lock globalnya tersebar di tengah-tengah
speech_stt.py bercampur dengan class SpeechSTT.

Kenapa Factory cocok: caller (SpeechSTT.load_model()) tidak perlu tahu
DETAIL bagaimana tiap model dimuat (file lock, cache validation, device
placement) — cukup panggil get_shared_models(gpu_id, model_name) dan
terima objek jadi. Menambah jenis model baru di masa depan tinggal
tambah 1 _load_xxx() + daftarkan di get_shared_models().

TIDAK ADA PERUBAHAN LOGIC dari versi asli.
"""

import os
import shutil
import threading
import logging
import traceback
from typing import Any, Dict

import torch
from faster_whisper import WhisperModel, BatchedInferencePipeline

from model.speech_stt.config import WHISPER_CACHE_DIR, PYANNOTE_CACHE_DIR, HF_TOKEN

logger = logging.getLogger(__name__)

# =================================================================
# Shared model storage — state global, sama seperti versi asli
# =================================================================
_SHARED_MODELS:        Dict[int, Dict]              = {}
_SHARED_MODELS_LOCK:   threading.Lock               = threading.Lock()
_GPU_TRANSCRIBE_LOCKS: Dict[int, threading.Lock]    = {}

_SHARED_PYANNOTE:      Dict[int, Any]               = {}
_SHARED_PYANNOTE_LOCK: threading.Lock               = threading.Lock()
_PYANNOTE_SEMAPHORES:  Dict[int, threading.Semaphore] = {}

_SHARED_GENDER:        Dict[int, Any]               = {}
_SHARED_GENDER_LOCK:   threading.Lock               = threading.Lock()

_SHARED_EMOTION:       Dict[int, Any]               = {}
_SHARED_EMOTION_LOCK:  threading.Lock               = threading.Lock()


def get_gpu_for_worker(worker_id: int, workers_per_gpu: int = 4) -> int:
    return worker_id // workers_per_gpu


def get_worker_group(worker_id: int, workers_per_gpu: int = 4) -> str:
    gpu_id   = get_gpu_for_worker(worker_id, workers_per_gpu)
    local_id = worker_id % workers_per_gpu
    return "GPU%d-W%d" % (gpu_id, local_id)


def get_gpu_transcribe_lock(gpu_id: int) -> threading.Lock:
    if gpu_id not in _GPU_TRANSCRIBE_LOCKS:
        with _SHARED_MODELS_LOCK:
            if gpu_id not in _GPU_TRANSCRIBE_LOCKS:
                _GPU_TRANSCRIBE_LOCKS[gpu_id] = threading.Lock()
    return _GPU_TRANSCRIBE_LOCKS[gpu_id]


def get_pyannote_semaphore(gpu_id: int) -> threading.Semaphore:
    # HOTFIX A: max_concurrent=1 mencegah cross-contamination antar job
    if gpu_id not in _PYANNOTE_SEMAPHORES:
        with _SHARED_PYANNOTE_LOCK:
            if gpu_id not in _PYANNOTE_SEMAPHORES:
                _PYANNOTE_SEMAPHORES[gpu_id] = threading.Semaphore(1)
    return _PYANNOTE_SEMAPHORES[gpu_id]


def _is_model_cache_valid(model_path: str, marker_file: str) -> bool:
    if not os.path.exists(marker_file) or not os.path.isdir(model_path):
        return False
    try:
        entries = os.listdir(model_path)
        if not entries:
            return False
        return any(
            e.endswith((".bin", ".pt", ".gguf", ".ctranslate2"))
            or os.path.isdir(os.path.join(model_path, e))
            for e in entries
        )
    except OSError:
        return False


def _check_ffmpeg() -> bool:
    if not hasattr(_check_ffmpeg, "_result"):
        _check_ffmpeg._result = shutil.which("ffmpeg") is not None
        if not _check_ffmpeg._result:
            logger.warning("ffmpeg tidak ditemukan di PATH.")
    return _check_ffmpeg._result


def _load_gender_classifier(gpu_id: int):
    logger.info("[GPU %d] Gender: DSP mode (no model).", gpu_id)
    return None


def _load_emotion_classifier(gpu_id: int):
    from model.speech_stt.config import EMOTION_ENABLED
    if not EMOTION_ENABLED:
        logger.info("[GPU %d] Emotion classifier dimatikan (EMOTION_ENABLED=false).", gpu_id)
        return None
    try:
        from transformers import pipeline as hf_pipeline
        from model.speech_stt.config import EMOTION_CACHE_DIR
        device = min(gpu_id, torch.cuda.device_count() - 1) if torch.cuda.is_available() else -1
        logger.info("[GPU %d] Loading audeering emotion classifier...", gpu_id)
        classifier = hf_pipeline(
            task="audio-classification",
            model="audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim",
            device=device,
            model_kwargs={"cache_dir": EMOTION_CACHE_DIR},
        )
        logger.info("[GPU %d] Emotion classifier loaded.", gpu_id)
        return classifier
    except Exception as e:
        logger.warning("[GPU %d] Emotion classifier gagal: %s", gpu_id, e)
        return None


def _load_whisper_shared(model_name: str, gpu_id: int) -> BatchedInferencePipeline:
    model_path  = os.path.join(WHISPER_CACHE_DIR, model_name)
    marker_file = os.path.join(WHISPER_CACHE_DIR, ".%s.ready" % model_name)
    lock_file   = os.path.join(WHISPER_CACHE_DIR, ".%s.lock"  % model_name)
    try:
        from filelock import FileLock
        file_lock = FileLock(lock_file, timeout=600)
    except ImportError:
        file_lock = None

    def _do_load():
        is_cached = _is_model_cache_valid(model_path, marker_file)
        if not is_cached and os.path.exists(marker_file):
            try:
                os.remove(marker_file)
            except OSError:
                pass
        base_model = WhisperModel(
            model_name, device="cuda", device_index=gpu_id,
            compute_type="float16", download_root=WHISPER_CACHE_DIR,
            local_files_only=is_cached, num_workers=1,
        )
        if not is_cached:
            try:
                os.makedirs(model_path, exist_ok=True)
                with open(marker_file, "w") as f:
                    f.write("Downloaded\n")
            except Exception:
                pass
        return BatchedInferencePipeline(model=base_model)

    if file_lock is not None:
        with file_lock:
            return _do_load()
    return _do_load()


def _load_pyannote(gpu_id: int):
    try:
        from pyannote.audio import Pipeline
        if not HF_TOKEN:
            logger.warning("HF_TOKEN tidak di-set — pyannote tidak dimuat.")
            return None
        device = torch.device("cuda:%d" % min(gpu_id, torch.cuda.device_count() - 1)) \
                 if torch.cuda.is_available() else torch.device("cpu")
        os.environ["HF_HOME"]        = PYANNOTE_CACHE_DIR
        os.environ["PYANNOTE_CACHE"] = PYANNOTE_CACHE_DIR
        try:
            from pyannote.audio.core.task import Specifications
            from pyannote.core import Segment
            torch.serialization.add_safe_globals([
                torch.torch_version.TorchVersion, Specifications, Segment, set, frozenset,
            ])
        except Exception as eg:
            logger.warning("add_safe_globals partial fail: %s", eg)
        logger.info("[GPU %d] Loading pyannote/speaker-diarization-3.1...", gpu_id)
        try:
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=HF_TOKEN, cache_dir=PYANNOTE_CACHE_DIR,
            )
        except Exception as e:
            logger.error("PYANNOTE GAGAL: %s\n%s", e, traceback.format_exc())
            return None
        pipeline = pipeline.to(device)

        # ── [NEW] Telephony hyperparameter tuning (opt-in, fail-safe) ──
        # Dibungkus try/except terpisah dari load pipeline utama: kalau API
        # `.parameters()`/`.instantiate()` berbeda di versi pyannote lain,
        # pipeline tetap jalan dengan hyperparameter default bawaan model
        # (tidak pernah membuat loading gagal total gara-gara tuning ini).
        from model.speech_stt.config import (
            PYANNOTE_ENABLE_TELEPHONY_TUNING, PYANNOTE_MIN_CLUSTER_SIZE,
            PYANNOTE_SEGMENTATION_MIN_DURATION_OFF, PYANNOTE_CLUSTERING_THRESHOLD,
        )
        if PYANNOTE_ENABLE_TELEPHONY_TUNING:
            try:
                current = pipeline.parameters(instantiated=True)
                overrides = {}
                if PYANNOTE_MIN_CLUSTER_SIZE is not None:
                    overrides.setdefault("clustering", {})["min_cluster_size"] = \
                        PYANNOTE_MIN_CLUSTER_SIZE
                if PYANNOTE_CLUSTERING_THRESHOLD is not None:
                    overrides.setdefault("clustering", {})["threshold"] = \
                        PYANNOTE_CLUSTERING_THRESHOLD
                if PYANNOTE_SEGMENTATION_MIN_DURATION_OFF is not None:
                    overrides.setdefault("segmentation", {})["min_duration_off"] = \
                        PYANNOTE_SEGMENTATION_MIN_DURATION_OFF

                if overrides:
                    merged = dict(current)
                    for section, values in overrides.items():
                        merged[section] = {**current.get(section, {}), **values}
                    pipeline.instantiate(merged)
                    logger.info(
                        "[Pyannote] Telephony tuning diterapkan: %s (default sebelumnya: %s)",
                        overrides, current,
                    )
            except Exception as e:
                logger.warning(
                    "[Pyannote] Gagal apply telephony tuning (%s) — pakai "
                    "hyperparameter default pyannote. Ini TIDAK fatal.", e,
                )

        logger.info("[GPU %d] pyannote pipeline loaded.", gpu_id)
        return pipeline
    except Exception as e:
        logger.warning("[GPU %d] pyannote gagal dimuat: %s", gpu_id, e)
        return None


def get_shared_models(gpu_id: int, model_name: str) -> dict:
    global _SHARED_MODELS, _SHARED_PYANNOTE
    with _SHARED_MODELS_LOCK:
        if gpu_id not in _SHARED_MODELS:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            whisper_model = _load_whisper_shared(model_name, gpu_id)
            _SHARED_MODELS[gpu_id] = {
                "whisper": whisper_model, "worker_count": 1, "warmup_done": False}
            logger.info("[GPU %d] Whisper shared model loaded.", gpu_id)
        else:
            _SHARED_MODELS[gpu_id]["worker_count"] += 1
    with _SHARED_PYANNOTE_LOCK:
        if gpu_id not in _SHARED_PYANNOTE:
            _SHARED_PYANNOTE[gpu_id] = _load_pyannote(gpu_id)
    with _SHARED_GENDER_LOCK:
        if gpu_id not in _SHARED_GENDER:
            _SHARED_GENDER[gpu_id] = _load_gender_classifier(gpu_id)
    with _SHARED_EMOTION_LOCK:
        if gpu_id not in _SHARED_EMOTION:
            _SHARED_EMOTION[gpu_id] = _load_emotion_classifier(gpu_id)
    return _SHARED_MODELS[gpu_id]


def get_shared_whisper_only(gpu_id: int, model_name: str) -> dict:
    """[NEW] Muat HANYA whisper — dipakai saat diarization_backend != 'pyannote'
    supaya tidak ikut memuat pyannote yang tidak akan dipakai (hemat VRAM)."""
    with _SHARED_MODELS_LOCK:
        if gpu_id not in _SHARED_MODELS:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            whisper_model = _load_whisper_shared(model_name, gpu_id)
            _SHARED_MODELS[gpu_id] = {
                "whisper": whisper_model, "worker_count": 1, "warmup_done": False}
            logger.info("[GPU %d] Whisper shared model loaded (whisper-only).", gpu_id)
        else:
            _SHARED_MODELS[gpu_id]["worker_count"] += 1
    return _SHARED_MODELS[gpu_id]


def get_shared_pyannote_only(gpu_id: int):
    """[NEW] Muat HANYA pyannote — dipakai saat ASR backend != 'whisper'
    (mis. backend tanpa word-timestamp) tapi diarization_backend tetap 'pyannote'."""
    with _SHARED_PYANNOTE_LOCK:
        if gpu_id not in _SHARED_PYANNOTE:
            _SHARED_PYANNOTE[gpu_id] = _load_pyannote(gpu_id)
    return _SHARED_PYANNOTE.get(gpu_id)


def get_shared_gender_emotion_only(gpu_id: int) -> dict:
    """[NEW] Muat gender + emotion classifier — TIDAK termasuk pyannote/nemo
    (diarization backend dipilih & dimuat terpisah, lihat diarization_backends/)."""
    with _SHARED_GENDER_LOCK:
        if gpu_id not in _SHARED_GENDER:
            _SHARED_GENDER[gpu_id] = _load_gender_classifier(gpu_id)
    with _SHARED_EMOTION_LOCK:
        if gpu_id not in _SHARED_EMOTION:
            _SHARED_EMOTION[gpu_id] = _load_emotion_classifier(gpu_id)
    return {
        "gender":  _SHARED_GENDER.get(gpu_id),
        "emotion": _SHARED_EMOTION.get(gpu_id),
    }


def unregister_worker(gpu_id: int):
    with _SHARED_MODELS_LOCK:
        if gpu_id in _SHARED_MODELS:
            _SHARED_MODELS[gpu_id]["worker_count"] = max(
                0, _SHARED_MODELS[gpu_id]["worker_count"] - 1)



def cleanup_worker_on_gc(gpu_id: int):
    try:
        unregister_worker(gpu_id)
    except Exception:
        pass


# =================================================================
# [NEW] NeMo diarizer (MSDD telephonic) — Factory + lock khusus.
#
# ⚠️ Race condition: NVIDIA sendiri punya open issue soal MSDD yang
# error ("Mismatch of counts between embedding vectors and timestamps")
# kalau dipanggil concurrent dari multi-thread tanpa lock eksplisit
# (https://github.com/NVIDIA-NeMo/NeMo/issues/12254). _NEMO_DIARIZER_LOCKS
# WAJIB dipakai membungkus setiap panggilan .diarize() — lihat
# diarization_backends/nemo_backend.py.
# =================================================================
_SHARED_NEMO_DIARIZER:      Dict[int, Any]  = {}
_SHARED_NEMO_DIARIZER_LOCK: threading.Lock  = threading.Lock()
_NEMO_DIARIZER_LOCKS:       Dict[int, threading.Lock] = {}


def _load_nemo_diarizer_config(gpu_id: int, model_name: str):
    """
    Return TEMPLATE config (OmegaConf) yang sudah diisi path model —
    BUKAN instance NeuralDiarizer yang sudah dimuat ke VRAM (itu terjadi
    lazy di NeMoDiarizationBackend._get_model(), supaya device string
    sempat di-resolve dengan benar per proses).
    """
    import torch
    from omegaconf import OmegaConf
    from model.speech_stt.config import (
        NEMO_DIARIZER_CONFIG_PATH, NEMO_DIARIZER_VAD_MODEL,
        NEMO_DIARIZER_SPEAKER_MODEL, NEMO_DIARIZER_WORK_DIR,
    )

    if not NEMO_DIARIZER_CONFIG_PATH or not os.path.exists(NEMO_DIARIZER_CONFIG_PATH):
        raise RuntimeError(
            "NEMO_DIARIZER_CONFIG_PATH belum di-set atau file tidak ditemukan "
            "('%s'). Download config resmi NeMo untuk telephonic diarization dari:\n"
            "  https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/speaker_tasks/"
            "diarization/conf/inference/diar_infer_telephonic.yaml\n"
            "simpan ke disk, lalu set env var NEMO_DIARIZER_CONFIG_PATH ke path file itu.\n"
            "Config ini SENGAJA tidak ditulis ulang manual di sini — skema hydra/omegaconf "
            "NeMo cukup dalam & bisa berubah antar rilis, mengambil dari sumber resmi NVIDIA "
            "jauh lebih aman daripada menebak skemanya." % (NEMO_DIARIZER_CONFIG_PATH or "")
        )

    cfg = OmegaConf.load(NEMO_DIARIZER_CONFIG_PATH)
    cfg.diarizer.out_dir                              = NEMO_DIARIZER_WORK_DIR
    cfg.diarizer.oracle_vad                           = False
    cfg.diarizer.vad.model_path                       = NEMO_DIARIZER_VAD_MODEL
    cfg.diarizer.speaker_embeddings.model_path        = NEMO_DIARIZER_SPEAKER_MODEL
    cfg.diarizer.msdd_model.model_path                = model_name
    cfg.diarizer.clustering.parameters.oracle_num_speakers = True
    cfg.device = "cuda:%d" % gpu_id if torch.cuda.is_available() else "cpu"

    # [NEW] Hyperparameter tuning inferensi untuk telephony — HANYA kalau
    # NEMO_ENABLE_TELEPHONY_TUNING=true DAN masing-masing parameter
    # eksplisit di-set (default None = tidak disentuh, pakai apa adanya
    # dari config resmi diar_infer_telephonic.yaml). Dibungkus try/except
    # PER PARAMETER (bukan 1 try besar) — supaya kalau 1 nama key ternyata
    # salah/berubah di versi NeMo Anda, parameter LAIN yang benar tetap
    # ke-apply, bukan semuanya gagal gara-gara 1 key salah.
    from model.speech_stt.config import (
        NEMO_ENABLE_TELEPHONY_TUNING,
        NEMO_VAD_WINDOW_LENGTH_SEC, NEMO_VAD_SHIFT_LENGTH_SEC,
        NEMO_VAD_ONSET, NEMO_VAD_OFFSET, NEMO_VAD_PAD_ONSET, NEMO_VAD_PAD_OFFSET,
        NEMO_MULTISCALE_WINDOW_LENGTHS_SEC, NEMO_MULTISCALE_SHIFT_LENGTHS_SEC,
        NEMO_MULTISCALE_WEIGHTS, NEMO_VAD_BATCH_SIZE, NEMO_MSDD_BATCH_SIZE,
    )
    if NEMO_ENABLE_TELEPHONY_TUNING:
        def _try_set(path_desc: str, setter):
            try:
                setter()
                logger.info("[NeMo tuning] %s berhasil di-set", path_desc)
            except Exception as e:
                logger.warning(
                    "[NeMo tuning] Gagal set %s (kemungkinan nama key beda "
                    "di versi NeMo Anda) — dilewati, config lain tetap "
                    "jalan. Error: %s", path_desc, e,
                )

        if NEMO_VAD_WINDOW_LENGTH_SEC is not None:
            _try_set("vad.parameters.window_length_in_sec",
                      lambda: setattr(cfg.diarizer.vad.parameters, "window_length_in_sec", NEMO_VAD_WINDOW_LENGTH_SEC))
        if NEMO_VAD_SHIFT_LENGTH_SEC is not None:
            _try_set("vad.parameters.shift_length_in_sec",
                      lambda: setattr(cfg.diarizer.vad.parameters, "shift_length_in_sec", NEMO_VAD_SHIFT_LENGTH_SEC))
        if NEMO_VAD_ONSET is not None:
            _try_set("vad.parameters.onset",
                      lambda: setattr(cfg.diarizer.vad.parameters, "onset", NEMO_VAD_ONSET))
        if NEMO_VAD_OFFSET is not None:
            _try_set("vad.parameters.offset",
                      lambda: setattr(cfg.diarizer.vad.parameters, "offset", NEMO_VAD_OFFSET))
        if NEMO_VAD_PAD_ONSET is not None:
            _try_set("vad.parameters.pad_onset",
                      lambda: setattr(cfg.diarizer.vad.parameters, "pad_onset", NEMO_VAD_PAD_ONSET))
        if NEMO_VAD_PAD_OFFSET is not None:
            _try_set("vad.parameters.pad_offset",
                      lambda: setattr(cfg.diarizer.vad.parameters, "pad_offset", NEMO_VAD_PAD_OFFSET))
        if NEMO_VAD_BATCH_SIZE is not None:
            _try_set("vad.parameters.batch_size",
                      lambda: setattr(cfg.diarizer.vad.parameters, "batch_size", NEMO_VAD_BATCH_SIZE))

        if NEMO_MULTISCALE_WINDOW_LENGTHS_SEC is not None:
            _try_set("speaker_embeddings.parameters.window_length_in_sec",
                      lambda: setattr(cfg.diarizer.speaker_embeddings.parameters,
                                       "window_length_in_sec", NEMO_MULTISCALE_WINDOW_LENGTHS_SEC))
        if NEMO_MULTISCALE_SHIFT_LENGTHS_SEC is not None:
            _try_set("speaker_embeddings.parameters.shift_length_in_sec",
                      lambda: setattr(cfg.diarizer.speaker_embeddings.parameters,
                                       "shift_length_in_sec", NEMO_MULTISCALE_SHIFT_LENGTHS_SEC))
        if NEMO_MULTISCALE_WEIGHTS is not None:
            _try_set("speaker_embeddings.parameters.multiscale_weights",
                      lambda: setattr(cfg.diarizer.speaker_embeddings.parameters,
                                       "multiscale_weights", NEMO_MULTISCALE_WEIGHTS))
        if NEMO_MSDD_BATCH_SIZE is not None:
            _try_set("speaker_embeddings.parameters.batch_size",
                      lambda: setattr(cfg.diarizer.speaker_embeddings.parameters,
                                       "batch_size", NEMO_MSDD_BATCH_SIZE))

    return cfg


def get_shared_nemo_diarizer_config(gpu_id: int, model_name: str):
    with _SHARED_NEMO_DIARIZER_LOCK:
        if gpu_id not in _SHARED_NEMO_DIARIZER:
            logger.info("[GPU %d] Menyiapkan konfigurasi NeMo diarizer (%s)...", gpu_id, model_name)
            _SHARED_NEMO_DIARIZER[gpu_id] = _load_nemo_diarizer_config(gpu_id, model_name)
    return _SHARED_NEMO_DIARIZER[gpu_id]


def get_nemo_diarizer_lock(gpu_id: int) -> threading.Lock:
    if gpu_id not in _NEMO_DIARIZER_LOCKS:
        with _SHARED_NEMO_DIARIZER_LOCK:
            if gpu_id not in _NEMO_DIARIZER_LOCKS:
                _NEMO_DIARIZER_LOCKS[gpu_id] = threading.Lock()
    return _NEMO_DIARIZER_LOCKS[gpu_id]
