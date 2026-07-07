# Speech STT Service — Struktur Repo Final (Setelah Refactor)

Refactor dikerjakan 3 stage, behavior 100% dipertahankan sama dengan
kode asli. Detail lengkap tiap stage ada di `README_STAGE1.md`,
`README_STAGE2.md`, `README_STAGE3.md`.

```
.
├── server.py                              Entry point FastAPI (tipis)
│
├── routes/                                HTTP layer — terima request, delegasikan ke services/
│   ├── speech_routes.py                   POST /speech/stt/save, /save_dashboard, GET job status
│   ├── diagnostics_routes.py              GET /health, /api/v1/queue/status, /gpu/memory
│   └── webhook_callback_routes.py         POST /speech/webhook/callback
│
├── services/                              Business logic & orchestration (tidak tahu FastAPI)
│   ├── config.py                          Konstanta upload & dashboard-baru
│   ├── server_config.py                   Env/CUDA isolation (WAJIB diimpor pertama di server.py)
│   ├── stt_singleton.py                   [SINGLETON] get_stt() per proses
│   ├── job_storage.py                     ThreadSafeJobStorage
│   ├── webhook_service.py                 Webhook daemon + queue
│   ├── http_retry.py                      post_with_retry()
│   ├── db.py                              Postgres pool + dynamic prompt lookup
│   ├── audio_upload.py                    Validasi upload, copy temp, convert video->audio
│   ├── stt_audio_prep.py                  Wrapper phase-fix sebelum transcribe
│   ├── executor.py                        ThreadPoolExecutor untuk job transcribe
│   ├── backend_forwarder.py               [STRATEGY] Register vs UploadTranscript
│   └── pipeline.py                        [TEMPLATE METHOD] Legacy vs New dashboard pipeline
│
├── model/
│   ├── __init__.py
│   └── speech_stt/                        Engine STT/diarization (dulu 1 file ~1700 baris)
│       ├── __init__.py                    Re-export publik (backward-compat)
│       ├── engine.py                      [FACADE] class SpeechSTT
│       ├── config.py                      Konstanta DSP/DRC/VAD/hallucination
│       ├── lexical_rules.py               Kata & pola heuristik (data only)
│       ├── model_registry.py              [FACTORY] load & share Whisper/pyannote/gender/emotion
│       ├── audio_conversion.py            Phase-fix, resample, DRC
│       ├── audio_slicing.py               Potong audio per speaker/segmen
│       ├── dsp_analysis.py                Analisis pitch (gender & fraud)
│       ├── gender_detection.py            Deteksi gender per speaker
│       ├── emotion_detection.py           Deteksi emosi per speaker/segmen
│       ├── formatter.py                   Format output transkrip + talk-time
│       ├── quality_checks.py              Hallucination filter + validasi diarization
│       ├── segment.py                     class SpeakerSegment
│       ├── tempfiles.py                   Cleanup temp file (tanpa depend ke services/)
│       └── alignment/                     [STRATEGY] algoritma alignment speaker
│           ├── base.py                    Interface AlignmentStrategy
│           ├── pure_alignment.py          PureOverlapAlignmentStrategy — AKTIF dipakai
│           ├── pitch_verification.py      Verifikasi speaker via pitch (v7.1)
│           └── legacy_alignment.py        LegacyHeuristicAlignmentStrategy — TIDAK aktif (lihat README_STAGE2)
│
└── utils/                                 Tidak disentuh — dipindah apa adanya dari repo asli
    ├── vars.py                            Config env var lama
    └── pdf_builder.py                     Builder pattern (sudah rapi sejak awal)
```

## Alur dependency (satu arah, tidak ada circular import)

```
server.py
   └─> routes/  (HTTP boundary)
          └─> services/  (orchestration, Template Method, Strategy)
                 └─> model/speech_stt/  (engine, tidak tahu FastAPI sama sekali)
                 └─> utils/  (helper generik)
```

`model/speech_stt/` sengaja dibuat TIDAK bergantung pada `services/` atau
`routes/` (lihat `tempfiles.py`) — supaya engine STT bisa dipakai berdiri
sendiri (mis. dari script batch/testing) tanpa perlu spin up FastAPI app.

## Cara pasang ke project asli

1. Timpa `speech_routes.py` lama dengan `routes/speech_routes.py` baru.
2. Tambahkan folder `routes/diagnostics_routes.py`, `routes/webhook_callback_routes.py`.
3. Tambahkan seluruh folder `services/`.
4. Hapus `model/speech_stt.py` (file lama), ganti dengan folder `model/speech_stt/` (package baru).
5. Timpa `server.py` lama dengan yang baru.
6. `utils/vars.py` dan `utils/pdf_builder.py` — tidak perlu diubah (sudah sama).
7. Jalankan `python -m py_compile` di semua file dulu untuk sanity check di environment Anda, lalu smoke-test dengan 1 file audio nyata sebelum deploy production.

Tidak ada perubahan env var, endpoint path, request/response schema, atau
model checkpoint yang dibutuhkan.
