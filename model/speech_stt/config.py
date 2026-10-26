#!/usr/bin/env python3
"""
model/speech_stt/config.py

Semua konstanta & env var yang sebelumnya ada di bagian atas speech_stt.py
(cache dirs, threshold DSP/DRC, VAD params, hallucination patterns).
Dipindah verbatim — TIDAK ADA nilai yang diubah.
"""

import os
from typing import Optional

WHISPER_CACHE_DIR  = "/data/models/whisper_cache"
PYANNOTE_CACHE_DIR = "/data/models/pyannote_cache"
GENDER_CACHE_DIR   = "/data/models/gender_cache"
EMOTION_CACHE_DIR  = "/data/models/emotion_cache"

os.makedirs(WHISPER_CACHE_DIR,  exist_ok=True)
os.makedirs(PYANNOTE_CACHE_DIR, exist_ok=True)
os.makedirs(GENDER_CACHE_DIR,   exist_ok=True)
os.makedirs(EMOTION_CACHE_DIR,  exist_ok=True)

HF_TOKEN = os.getenv("HF_TOKEN", "")
# EMOTION_ENABLED=false -> audeering emotion classifier TIDAK di-load
# (hemat VRAM); semua pemakai classifier sudah handle None.
EMOTION_ENABLED = os.getenv("EMOTION_ENABLED", "true").strip().lower() \
                  not in ("false", "0", "no", "off")
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "300"))

# =================================================================
# DSP thresholds
# =================================================================
DSP_FEMALE_THRESHOLD_HZ   = float(os.getenv("DSP_FEMALE_THRESHOLD_HZ",   "185.0"))
DSP_MALE_THRESHOLD_HZ     = float(os.getenv("DSP_MALE_THRESHOLD_HZ",     "155.0"))
DSP_HIGHPASS_HZ           = float(os.getenv("DSP_HIGHPASS_HZ",           "140.0"))
DSP_FRAUD_ENABLED         = os.getenv("DSP_FRAUD_ENABLED", "true").strip().lower() \
                            not in ("false", "0", "no", "off")
DSP_FRAUD_STD_HZ          = float(os.getenv("DSP_FRAUD_STD_HZ",          "80.0"))
DSP_FRAUD_JUMP_HZ         = float(os.getenv("DSP_FRAUD_JUMP_HZ",         "80.0"))
DSP_FRAUD_JUMP_WINDOW_SEC = float(os.getenv("DSP_FRAUD_JUMP_WINDOW_SEC", "30.0"))
DSP_MIN_VOICED_FRAMES     = int(os.getenv("DSP_MIN_VOICED_FRAMES",       "5"))
DSP_FRAUD_MIN_DUR_SEC     = float(os.getenv("DSP_FRAUD_MIN_DUR_SEC",     "1.5"))
DSP_FRAUD_COOLDOWN_SEGS   = int(os.getenv("DSP_FRAUD_COOLDOWN_SEGS",     "5"))
DSP_FMIN_HZ               = float(os.getenv("DSP_FMIN_HZ",               "65.0"))
DSP_FMAX_HZ               = float(os.getenv("DSP_FMAX_HZ",              "300.0"))

# =================================================================
# DRC thresholds
# =================================================================
DRC_ENABLED    = os.getenv("DRC_ENABLED", "true").strip().lower() \
                 not in ("false", "0", "no", "off")
DRC_TARGET_DB  = float(os.getenv("DRC_TARGET_DB",  "-20.0"))
DRC_FRAME_MS   = float(os.getenv("DRC_FRAME_MS",    "50.0"))
DRC_HOP_MS     = float(os.getenv("DRC_HOP_MS",      "25.0"))
DRC_MAX_GAIN_DB= float(os.getenv("DRC_MAX_GAIN_DB", "30.0"))
DRC_MIN_GAIN_DB= float(os.getenv("DRC_MIN_GAIN_DB","-20.0"))
DRC_ATTACK_MS  = float(os.getenv("DRC_ATTACK_MS",   "10.0"))
DRC_RELEASE_MS = float(os.getenv("DRC_RELEASE_MS", "100.0"))

_UPLOAD_CHUNK_SIZE = 1 * 1024 * 1024

VAD_PARAMS_TELEPHONY = dict(
    threshold=0.1, min_speech_duration_ms=200,
    min_silence_duration_ms=100, speech_pad_ms=500, max_speech_duration_s=30,
)
VAD_PARAMS_CLEAN = dict(
    threshold=0.45, min_speech_duration_ms=150,
    min_silence_duration_ms=600, speech_pad_ms=200,
    max_speech_duration_s=float("inf"),
)

_HALLUCINATION_PATTERNS = {
    "Terima kasih.", "Terima kasih telah menonton.", "Terima kasih sudah menonton.",
    "Sampai jumpa.", "Sampai jumpa di video berikutnya.",
    "Sampai jumpa di video selanjutnya.", "Sampai jumpa lagi.",
    "Semoga bermanfaat.", "Jangan lupa like dan subscribe.", "Like dan subscribe.",
    "Subtitle by", "Subtitel oleh", "Thanks for watching.", "Thank you for watching.",
    "Please subscribe.", "Subscribe.", "", " ",
}

MIN_SEGMENT_DUR_SEC: float = 1.2

# =================================================================
# [NEW] Label speaker FINAL yang tampil di seluruh output (PDF report,
# talk-time, deteksi emosi/gender, dsb). Sebelumnya hardcoded
# "AGENT_BM"/"NASABAH" di banyak tempat — sekarang satu sumber
# kebenaran di sini. Speaker dengan total durasi bicara TERBANYAK di
# tiap panggilan otomatis dapat label AGENT_SPEAKER_LABEL (lihat
# segment.py::map_speakers_to_binary) — ini TIDAK berubah, cuma teks
# labelnya yang bisa dikustomisasi.
# =================================================================
AGENT_SPEAKER_LABEL    = os.getenv("AGENT_SPEAKER_LABEL", "Agent")
CUSTOMER_SPEAKER_LABEL = os.getenv("CUSTOMER_SPEAKER_LABEL", "Customer")

# =================================================================
# [NEW] Tuning hyperparameter pyannote khusus telephony 2-speaker calls.
#
# Latar belakang: pyannote/speaker-diarization-3.1 dilatih mayoritas
# pada data meeting/broadcast (AMI, VoxConverse, DIHARD) — bukan telepon
# narrowband. Default hyperparameter clustering-nya (min_cluster_size
# ~15) mengasumsikan tiap speaker berbicara CUKUP LAMA. Di telemarketing,
# customer sering cuma menjawab singkat berkali-kali ("iya", "boleh")
# sehingga total durasi bicaranya bisa di bawah threshold itu — pyannote
# lalu MENYERAP semua turn customer ke cluster agent. Ini persis pola
# yang sudah divalidasi kode ini sendiri (lihat quality_checks.py:
# "speaker_imbalance_detected" saat agent_pct > 85%).
#
# Referensi: GitHub pyannote/pyannote-audio#1579 (hyperparameter yang
# valid untuk pipeline 3.1: segmentation.min_duration_off,
# clustering.method, clustering.min_cluster_size, clustering.threshold)
# dan blog Hervé Bredin (pembuat pyannote) soal cara adaptasi ke domain
# sendiri: https://herve.niderb.fr/posts/2022-12-02-how-I-won-2022-diarization-challenges.html
#
# ⚠️ Nilai default di bawah adalah TITIK AWAL berdasarkan karakteristik
# umum panggilan telemarketing (speaker sangat asimetris, turn-taking
# cepat) — BUKAN hasil tuning pada data Anda. Rekomendasi: kumpulkan
# ~10-20 panggilan berlabel (ground-truth siapa-bicara-kapan), hitung
# DER (Diarization Error Rate) dengan beberapa kombinasi nilai, pakai
# yang DER-nya paling rendah. Set ke string kosong / hapus env var untuk
# kembali ke default pyannote murni (fallback aman kalau tuning ini
# ternyata memperburuk kasus Anda).
# =================================================================
def _optional_float(env_name: str) -> Optional[float]:
    val = os.getenv(env_name, "").strip()
    return float(val) if val else None


def _optional_int(env_name: str) -> Optional[int]:
    val = os.getenv(env_name, "").strip()
    return int(val) if val else None


PYANNOTE_ENABLE_TELEPHONY_TUNING = os.getenv(
    "PYANNOTE_ENABLE_TELEPHONY_TUNING", "true"
).strip().lower() not in ("false", "0", "no", "off")

# min_cluster_size lebih kecil dari default (~15) supaya speaker dengan
# sedikit turn/durasi bicara (khas customer di call telemarketing) tidak
# terserap ke cluster speaker lain.
PYANNOTE_MIN_CLUSTER_SIZE = _optional_int("PYANNOTE_MIN_CLUSTER_SIZE")
if PYANNOTE_ENABLE_TELEPHONY_TUNING and PYANNOTE_MIN_CLUSTER_SIZE is None:
    PYANNOTE_MIN_CLUSTER_SIZE = 6

# min_duration_off lebih kecil dari default supaya jeda pendek antar
# giliran bicara (turn-taking cepat khas telepon) tidak membuat 2 segmen
# beda-speaker digabung jadi 1 oleh segmentation model.
PYANNOTE_SEGMENTATION_MIN_DURATION_OFF = _optional_float(
    "PYANNOTE_SEGMENTATION_MIN_DURATION_OFF"
)
if PYANNOTE_ENABLE_TELEPHONY_TUNING and PYANNOTE_SEGMENTATION_MIN_DURATION_OFF is None:
    PYANNOTE_SEGMENTATION_MIN_DURATION_OFF = 0.1

# clustering.threshold — TIDAK diberi default telephony (biarkan None =
# pakai default pyannote) karena parameter ini paling sensitif/spesifik-
# data; ubah HANYA setelah tuning dengan data Anda sendiri (lihat referensi
# di atas). Override manual tetap didukung lewat env var kalau sudah ada
# hasil tuning.
PYANNOTE_CLUSTERING_THRESHOLD = _optional_float("PYANNOTE_CLUSTERING_THRESHOLD")

# [NEW] Threshold skip-diarization — SEBELUMNYA hardcode 60.0 detik
# (audio lebih pendek dari ini otomatis skip diarization, semua jadi 1
# speaker). Karena num_speakers=2 sudah di-pass sebagai oracle hint,
# pyannote cukup andal bahkan untuk klip lebih pendek. Default diturunkan
# ke 15s untuk telephony; set lebih tinggi lagi kalau di data Anda audio
# pendek justru sering salah diarize.
DIARIZATION_MIN_AUDIO_DURATION_SEC = float(
    os.getenv("DIARIZATION_MIN_AUDIO_DURATION_SEC", "15.0")
)

# =================================================================
# [NEW] Diarization backend selection — pyannote (default, tidak
# berubah) vs nemo (NeuralDiarizer/MSDD, opsional). Lihat
# model/speech_stt/diarization_backends/ untuk detail Strategy-nya.
# =================================================================
DIARIZATION_BACKEND = os.getenv("DIARIZATION_BACKEND", "pyannote").strip().lower()  # "pyannote" | "nemo"

# Checkpoint MSDD yang dilatih KHUSUS di data telepon (Fisher Corpus,
# ~1500 jam) — lihat https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/diar_msdd_telephonic
NEMO_DIARIZER_MODEL_NAME = os.getenv("NEMO_DIARIZER_MODEL_NAME", "diar_msdd_telephonic")

# ⚠️ WAJIB diisi manual — lihat README_NEMO_DIARIZATION_OPTION.md untuk
# cara mendapatkan file config resminya. Sengaja TIDAK di-bundle otomatis
# karena skema hydra/omegaconf NeMo cukup dalam & bisa berubah antar
# versi rilis; menulis ulang manual berisiko keliru dan gagal diam-diam.
NEMO_DIARIZER_CONFIG_PATH = os.getenv("NEMO_DIARIZER_CONFIG_PATH", "").strip() or None

NEMO_DIARIZER_VAD_MODEL     = os.getenv("NEMO_DIARIZER_VAD_MODEL", "vad_multilingual_marblenet")
NEMO_DIARIZER_SPEAKER_MODEL = os.getenv("NEMO_DIARIZER_SPEAKER_MODEL", "titanet_large")

NEMO_DIARIZER_WORK_DIR = os.getenv("NEMO_DIARIZER_WORK_DIR", "/data/tmp/nemo_diarizer")
os.makedirs(NEMO_DIARIZER_WORK_DIR, exist_ok=True)

# Lihat README_NEMO_DIARIZATION_OPTION.md bagian "Race condition MSDD" —
# default False (1 instance NeuralDiarizer dipakai ulang, diserialisasi
# lewat lock). Set True HANYA kalau error mismatch embedding/timestamp
# masih muncul walau sudah dikunci — trade-off jauh lebih lambat karena
# reload VAD+speaker+MSDD checkpoint dari disk di SETIAP panggilan.
NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL = os.getenv(
    "NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL", "false"
).strip().lower() not in ("false", "0", "no", "off")

# =================================================================
# [NEW] Hyperparameter tuning inferensi NeMo diarization untuk telephony.
# BEDA dari fine-tuning (training bobot model) — ini cuma menyetel
# parameter pipeline INFERENSI dari model pretrained, pola sama seperti
# PYANNOTE_ENABLE_TELEPHONY_TUNING di atas.
#
# Referensi konsep (BUKAN nama key config — itu dari pengetahuan umum
# skema NeMo, config asli): "Deep-dive into Nemo: How to efficiently
# tune a speaker diarization pipeline?" — La Javaness R&D, 2023.
#
# Semua default None/kosong = TIDAK override apa pun, pakai apa adanya
# dari NEMO_DIARIZER_CONFIG_PATH (config resmi diar_infer_telephonic.yaml
# yang sudah di-tuning NVIDIA untuk telephony) — sama seperti
# PYANNOTE_CLUSTERING_THRESHOLD, isi HANYA setelah Anda validasi sendiri
# dengan data + DER benchmark, jangan asal isi berdasarkan angka umum.
# =================================================================
NEMO_ENABLE_TELEPHONY_TUNING = os.getenv(
    "NEMO_ENABLE_TELEPHONY_TUNING", "false"
).strip().lower() not in ("false", "0", "no", "off")


def _optional_float_list(name: str):
    """Parse '1.5,1.25,1.0,0.75,0.5' -> [1.5, 1.25, 1.0, 0.75, 0.5], atau
    None kalau env var kosong (tidak di-set)."""
    val = os.getenv(name, "").strip()
    if not val:
        return None
    return [float(x.strip()) for x in val.split(",") if x.strip()]


# ── VAD (Voice Activity Detection) — tahap pertama pipeline ──
# Window length & shift kecil -> segmentasi lebih halus (granularitas
# per-kalimat/kata), besar -> segmen bicara lebih panjang/kasar.
NEMO_VAD_WINDOW_LENGTH_SEC = _optional_float("NEMO_VAD_WINDOW_LENGTH_SEC")
NEMO_VAD_SHIFT_LENGTH_SEC  = _optional_float("NEMO_VAD_SHIFT_LENGTH_SEC")
# Onset/offset threshold + padding -> sensitivitas deteksi mulai/akhir bicara
NEMO_VAD_ONSET       = _optional_float("NEMO_VAD_ONSET")
NEMO_VAD_OFFSET      = _optional_float("NEMO_VAD_OFFSET")
NEMO_VAD_PAD_ONSET   = _optional_float("NEMO_VAD_PAD_ONSET")
NEMO_VAD_PAD_OFFSET  = _optional_float("NEMO_VAD_PAD_OFFSET")

# ── Multiscale speaker embedding & clustering ──
# Default NeMo: 5 scale, 0.5s-1.5s. Kasus sederhana (2 speaker jelas,
# giliran bicara panjang) -> 1 scale saja sudah cukup, lebih cepat.
# Kasus kompleks (banyak speaker, overlap) -> tetap pakai multiscale.
NEMO_MULTISCALE_WINDOW_LENGTHS_SEC = _optional_float_list("NEMO_MULTISCALE_WINDOW_LENGTHS_SEC")
NEMO_MULTISCALE_SHIFT_LENGTHS_SEC  = _optional_float_list("NEMO_MULTISCALE_SHIFT_LENGTHS_SEC")
NEMO_MULTISCALE_WEIGHTS            = _optional_float_list("NEMO_MULTISCALE_WEIGHTS")

# ── Batch size inferensi ──
# Menurut studi La Javaness: sweet spot 4-16 (kecepatan optimal, VRAM
# <4GB). >32 tidak menambah kecepatan, VRAM naik eksponensial.
NEMO_VAD_BATCH_SIZE  = _optional_int("NEMO_VAD_BATCH_SIZE")
NEMO_MSDD_BATCH_SIZE = _optional_int("NEMO_MSDD_BATCH_SIZE")

# =================================================================
# [NEW] ASR backend selection — whisper (default, tidak berubah) vs
# elevenlabs (cloud API, opsional). Lihat model/speech_stt/asr_backends/
# untuk detail Strategy pattern-nya.
# =================================================================
ASR_BACKEND = os.getenv("ASR_BACKEND", "whisper").strip().lower()  # "whisper" | "elevenlabs"

# =================================================================
# [NEW] ElevenLabs Scribe — opsi ASR backend ke-2, SATU-SATUNYA yang
# BUKAN model self-hosted (cloud API pihak ketiga).
#
# ⚠️ PERINGATAN DATA RESIDENCY: backend ini mengirim audio ke server
# ElevenLabs (cloud, region EU/India yang dipublikasikan resmi — TIDAK
# ada region Indonesia). Untuk data nasabah bank, ini WAJIB disetujui
# tim legal/compliance dulu (UU PDP, regulasi OJK soal data pribadi
# nasabah) sebelum diaktifkan untuk data produksi asli. JANGAN aktifkan
# ASR_BACKEND=elevenlabs tanpa persetujuan itu.
#
# Beda dari backend lain: TIDAK butuh GPU/VRAM, TIDAK ada "load model"
# sungguhan (cuma validasi API key + init client ringan) — makanya
# tidak ada Factory/shared-loading di model_registry.py untuk backend
# ini, beda dari whisper yang model berat
# in-memory.
# =================================================================
ELEVENLABS_API_KEY   = os.getenv("ELEVENLABS_API_KEY", "").strip() or None
ELEVENLABS_MODEL_ID  = os.getenv("ELEVENLABS_MODEL_ID", "scribe_v2")
ELEVENLABS_NUM_SPEAKERS = int(os.getenv("ELEVENLABS_NUM_SPEAKERS", "2"))
ELEVENLABS_TAG_AUDIO_EVENTS = os.getenv(
    "ELEVENLABS_TAG_AUDIO_EVENTS", "false"
).strip().lower() not in ("false", "0", "no", "off")
# Pengganti/pelengkap initial_prompt dinamis Anda — Scribe v2 punya
# "keyterm prompting" (istilah spesifik-domain), bukan prompt paragraf
# gaya Whisper. Isi dengan istilah domain Anda (mis. "Mega Cashline",
# "Ultima Shield") dipisah koma kalau mau dipakai.
ELEVENLABS_KEYTERMS = [
    t.strip() for t in os.getenv("ELEVENLABS_KEYTERMS", "").split(",") if t.strip()
]
ELEVENLABS_REQUEST_TIMEOUT_SEC = float(os.getenv("ELEVENLABS_REQUEST_TIMEOUT_SEC", "300"))
