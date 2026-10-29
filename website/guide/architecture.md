# Arsitektur

## Lapisan dan arah dependency

Dependency mengalir satu arah dan tidak ada circular import.

```text
server.py
   └─▶ routes/              HTTP boundary: terima request, delegasikan
          └─▶ services/      orchestration: job, pipeline, webhook, DB, cache
                 ├─▶ model/speech_stt/   engine STT, tidak tahu FastAPI
                 └─▶ utils/              helper generik (vars, pdf_builder)
```

`model/speech_stt/` sengaja tidak bergantung pada `services/` atau `routes/`, sehingga engine bisa dipakai dari script batch atau test tanpa menyalakan FastAPI.

## Dua proses

| Proses | Port | Isi | Worker |
|---|---|---|---|
| `server.py` | 8000 (8001, … per GPU) | Endpoint STT, diagnostik, webhook callback. Memuat model ke GPU. | **Wajib 1** (`gunicorn -w 1`) |
| `lightweight_server.py` | 8002 (`LIGHTWEIGHT_PORT`) | Endpoint campaign dan recording. Hanya Postgres, Redis, MinIO, tanpa torch. | Boleh banyak |

Endpoint data dipisah karena request ringan dulu harus antre di belakang transkripsi yang bisa berlangsung menit-an, sehingga App B sering kena `Read timed out`.

## Alur job async

```text
POST /api/v1/speech/stt/save
  │  routes/speech_routes.py
  │    ├─ validasi & simpan upload ke TMPDIR (video → audio via ffmpeg)
  │    ├─ tolak 503 kalau job pending+processing ≥ MAX_PENDING_JOBS
  │    └─ job_storage.set(job_id, pending) → submit ke transcribe_executor
  ▼
services/pipeline.py  STTJobPipeline.run()        ◀── Template Method
  1. ambil instance STT (singleton per proses)
  2. konversi audio untuk STT (phase-fix)
  3. engine.transcribe_with_diarization()  /  transcribe tanpa diarization
  4. bangun PDF (utils/pdf_builder.py)
  5. deliver_result()                      ◀── di-override subclass
       ├─ LegacyDashboardPipeline → webhook ke webhook_url
       └─ NewDashboardPipeline    → forward_to_all_branches() (Strategy)
  6. cleanup file temp
```

## Engine STT

`model/speech_stt/engine.py::SpeechSTT` adalah **Facade**: ia hanya mengatur urutan pemanggilan modul-modul kecil.

```text
load_model()
  Fase 1  ASR            asr_backends/      whisper │ elevenlabs
  Fase 2  gender+emosi   model_registry.py  (shared, sekali per proses)
  Fase 3  diarization    diarization_backends/  pyannote │ nemo
                         (dilewati kalau ASR provides_own_diarization)

transcribe_with_diarization()
  ├─ ElevenLabs:  segmen Scribe ─▶ [diarization v2?] ─▶ map Agent/Customer ─▶ merge
  └─ Whisper:     ASR + diarization ─▶ align_pure_overlap
                    ─▶ safe_single_word_correction ─▶ pitch_verify_speakers
                    ─▶ merge_same_speaker_simple
  ─▶ analyze_speakers(): gender, emosi, fraud alert, talk-time
  ─▶ formatter: teks per speaker + metadata
```

## Isolasi GPU

`services/server_config.py` men-set `CUDA_VISIBLE_DEVICES` dari `ASSIGNED_GPU` dan **harus menjadi import pertama** di `server.py`, sebelum `torch` disentuh siapa pun. Kalau urutan ini teracak, proses yang seharusnya hanya melihat satu GPU bisa melihat semua GPU tanpa ada error.

::: danger Jangan ubah urutan import di `server.py`
`server_config.py` sengaja tidak mengimpor modul berat. Komentar di kode menjelaskan alasannya. Tinjau ulang setiap perubahan di blok import paling atas.
:::

Lihat juga [Struktur kode & pattern](/reference/code-structure).
