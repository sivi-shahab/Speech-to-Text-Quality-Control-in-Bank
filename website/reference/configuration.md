# Konfigurasi (env var)

Semua konfigurasi dibaca dari environment, biasanya lewat `.env` yang dimuat `python-dotenv`. `.env` ada di `.gitignore`. Di Docker, rahasia tidak di-bake ke image dan harus dikirim lewat `-e` atau `--env-file`.

::: warning Rahasia
`HF_TOKEN`, `ELEVENLABS_API_KEY`, `DATABASE_URL`, `MAIN_BACKEND_API_KEY`, `API_KEY`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, dan `OPENAI_API_KEY` adalah rahasia. Jangan commit nilainya, dan rotasi kalau pernah bocor.
:::

## Server & proses

| Env var | Default | Keterangan |
|---|---|---|
| `DEPLOYMENT_MODE` | `dev` | Selain `dev`, `python server.py` menolak start (pakai Gunicorn) |
| `PORT` | `8000` | Port `server.py` |
| `LIGHTWEIGHT_PORT` | `8002` | Port `lightweight_server.py` |
| `ASSIGNED_GPU` | `0` | GPU fisik untuk proses ini (di-set ke `CUDA_VISIBLE_DEVICES`) |
| `ESTIMATED_MODEL_MEMORY_GB` | `4.0` | Estimasi VRAM model untuk diagnostik |
| `CORS_ORIGINS` | `*` | Daftar origin dipisah koma |
| `CORS_ORIGINS_LIGHTWEIGHT` | – | CORS untuk lightweight server |
| `TMPDIR` | sistem | Direktori file temp (Docker: `/data/tmp`) |

## Upload & antrean

| Env var | Default | Keterangan |
|---|---|---|
| `MAX_UPLOAD_SIZE_MB` | `1000` | Batas ukuran upload |
| `MAX_PENDING_JOBS` | `200` | Batas job pending + processing sebelum 503 |
| `MAX_TRANSCRIBE_WORKERS` | `50` | Thread `transcribe_executor` |
| `FFMPEG_TIMEOUT_SECONDS` | `300` | Timeout konversi ffmpeg |

## Integrasi

| Env var | Default | Keterangan |
|---|---|---|
| `DATABASE_URL` | – | Postgres untuk dynamic prompt, campaign, cache L2 |
| `MAIN_BACKEND_BASE_URL` | `http://api:4000` | Backend utama untuk `/upload_transcript` |
| `MAIN_BACKEND_RESULT_URL` | – | Endpoint register hasil |
| `MAIN_BACKEND_API_KEY` | `API_KEY` | Header `X-API-Key` |
| `DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN` | – | Campaign default untuk upload transcript |
| `URL_SAVE_DB` | – | Endpoint simpan hasil dashboard lama |

## Model & ASR

| Env var | Default | Keterangan |
|---|---|---|
| `ASR_BACKEND` | `whisper` | `whisper` atau `elevenlabs` |
| `MODEL_WHISPER` | – | Nama/path model faster-whisper |
| `HF_TOKEN` | – | Token Hugging Face (pyannote) |
| `HF_HUB_OFFLINE` | – | `1` untuk memakai cache model lokal saja |
| `EMOTION_ENABLED` | `true` | Deteksi emosi |
| `TEXT_NORM_ENABLED` | `false` | Normalisasi teks setelah transkripsi |
| `AGENT_SPEAKER_LABEL` | `Agent` | Label agent (dibaca parser PDF App B) |
| `CUSTOMER_SPEAKER_LABEL` | `Customer` | Label nasabah |

## ElevenLabs Scribe

| Env var | Default | Keterangan |
|---|---|---|
| `ELEVENLABS_API_KEY` | – | Wajib untuk backend/endpoint ElevenLabs |
| `ELEVENLABS_MODEL_ID` | `scribe_v2` | Model Scribe |
| `ELEVENLABS_NUM_SPEAKERS` | `2` | Petunjuk jumlah speaker |
| `ELEVENLABS_KEYTERMS` | – | Keyterm dipisah koma |
| `ELEVENLABS_TAG_AUDIO_EVENTS` | `false` | Tag `[laughter]`, `[music]`, dsb. |
| `ELEVENLABS_REQUEST_TIMEOUT_SEC` | `300` | Timeout panggilan API |
| `ELEVENLABS_MAX_CONCURRENCY` | `5` | Batas panggilan paralel |
| `ELEVENLABS_429_RETRIES` | `15` | Retry saat rate limit |

## Diarization

| Env var | Default | Keterangan |
|---|---|---|
| `DIARIZATION_BACKEND` | `pyannote` | `pyannote` atau `nemo` |
| `DIARIZATION_MIN_AUDIO_DURATION_SEC` | `15.0` | Audio lebih pendek dilewati diarization |
| `PYANNOTE_ENABLE_TELEPHONY_TUNING` | `true` | Tuning telephony pyannote |
| `PYANNOTE_MIN_CLUSTER_SIZE` | `6` | |
| `PYANNOTE_SEGMENTATION_MIN_DURATION_OFF` | `0.1` | |
| `PYANNOTE_CLUSTERING_THRESHOLD` | kosong | Isi setelah tuning |
| `NEMO_DIARIZER_CONFIG_PATH` | – | Wajib untuk NeMo |
| `NEMO_DIARIZER_MODEL_NAME` | `diar_msdd_telephonic` | |
| `NEMO_DIARIZER_VAD_MODEL` | `vad_multilingual_marblenet` | |
| `NEMO_DIARIZER_SPEAKER_MODEL` | `titanet_large` | |
| `NEMO_DIARIZER_WORK_DIR` | `/data/tmp/nemo_diarizer` | Manifest dan RTTM |
| `NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL` | `false` | Katup darurat race condition |

## Diarization v2

| Env var | Default | Keterangan |
|---|---|---|
| `DIARIZATION_V2_MODE` | `off` | `off`, `shadow`, atau `on` |
| `SCRIBE_DIAR_MODE` | `fixed2` | `fixed2`, `threshold`, atau `roles` |
| `SCRIBE_DIAR_THRESHOLD` | – | Untuk mode `threshold` |
| `SCRIBE_ENABLE_LOGGING` | kosong | `false` = zero-retention (enterprise) |
| `SCRIBE_KEYTERMS_FILE` | – | YAML keyterm per campaign |
| `DIAR_EVAL_CACHE_DIR` | – | Cache evaluasi, wajib di luar repo |

## DSP: gender & fraud

| Env var | Default |
|---|---|
| `DSP_FEMALE_THRESHOLD_HZ` | `185.0` |
| `DSP_MALE_THRESHOLD_HZ` | `155.0` |
| `DSP_HIGHPASS_HZ` | `140.0` |
| `DSP_FMIN_HZ` / `DSP_FMAX_HZ` | `65.0` / `300.0` |
| `DSP_MIN_VOICED_FRAMES` | `5` |
| `DSP_FRAUD_ENABLED` | `true` |
| `DSP_FRAUD_STD_HZ` | `80.0` |
| `DSP_FRAUD_JUMP_HZ` | `80.0` |
| `DSP_FRAUD_JUMP_WINDOW_SEC` | `30.0` |
| `DSP_FRAUD_MIN_DUR_SEC` | `1.5` |
| `DSP_FRAUD_COOLDOWN_SEGS` | `5` |

## Dynamic range compression

| Env var | Default |
|---|---|
| `DRC_ENABLED` | `true` |
| `DRC_TARGET_DB` | `-20.0` |
| `DRC_MAX_GAIN_DB` / `DRC_MIN_GAIN_DB` | `30.0` / `-20.0` |
| `DRC_FRAME_MS` / `DRC_HOP_MS` | `50.0` / `25.0` |
| `DRC_ATTACK_MS` / `DRC_RELEASE_MS` | `10.0` / `100.0` |

## Cache & housekeeping

| Env var | Default |
|---|---|
| `REDIS_CACHE_HOST` / `REDIS_CACHE_PORT` / `REDIS_CACHE_DB` | `localhost` / `6378` / `1` |
| `REDIS_CACHE_TTL_SEC` | `3600` |
| `MINIO_CACHE_ENDPOINT` | `localhost:4003` |
| `MINIO_CACHE_BUCKET` | `cashline-ntb-asscend` |
| `MINIO_CACHE_SECURE` | `false` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | – |
| `TMP_CLEANUP_ENABLED` | `true` |
| `TMP_CLEANUP_MAX_AGE_HOURS` | `6` |
| `TMP_CLEANUP_INTERVAL_MINUTES` | `60` |
