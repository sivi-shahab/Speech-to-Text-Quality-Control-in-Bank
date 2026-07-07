# Refactor Stage 1 — `speech_routes.py`

Status: **behavior 100% identik** dengan v3.3. Ini murni reorganisasi kode
memakai design pattern, tidak ada logic yang diubah, dihapus, atau
"disederhanakan" secara diam-diam.

## Pattern yang dipakai & alasannya

| Pattern | Dipakai di | Masalah yang diselesaikan |
|---|---|---|
| **Template Method** | `services/pipeline.py` | `process_and_upload_with_webhook()` dan `process_and_forward_to_new_dashboard()` di versi lama 80% identik (get STT → convert audio → transcribe → build PDF → cleanup), tapi karena copy-paste, kalau ada bug di langkah bersama (mis. urutan cleanup), harus difix di 2 tempat. Sekarang `STTJobPipeline.run()` mengunci urutan langkah, subclass (`LegacyDashboardPipeline`, `NewDashboardPipeline`) cuma override `deliver_result()`. |
| **Strategy** | `services/backend_forwarder.py` | Dua cara "kirim hasil ke backend utama" (`register_result_to_main_backend` vs `upload_transcript_to_main_backend`) sekarang jadi 2 class (`RegisterResultStrategy`, `UploadTranscriptStrategy`) yang implement interface `ResultForwarder` yang sama. Menambah cabang forward ke-3 nanti tinggal tambah 1 class, tidak perlu sentuh pipeline. |
| **Single Responsibility split** | `services/job_storage.py`, `webhook_service.py`, `http_retry.py`, `audio_upload.py`, `stt_audio_prep.py`, `db.py`, `config.py`, `executor.py` | File asli 900+ baris mencampur: config, db pool, HTTP retry, webhook daemon, job storage, validasi upload, konversi video, dan 2 job orchestrator dalam 1 file. Sekarang tiap modul punya satu urusan, gampang di-unit-test terpisah. |

## Pemetaan fungsi lama → lokasi baru

| Fungsi/kelas lama (`speech_routes.py` v3.3) | Lokasi baru |
|---|---|
| `_post_with_retry` | `services/http_retry.py::post_with_retry` |
| `_webhook_daemon`, `send_webhook_notification`, `_webhook_queue` | `services/webhook_service.py` |
| `ThreadSafeJobStorage`, `job_storage` | `services/job_storage.py` |
| `_validate_audio_file`, `_copy_upload_to_temp`, `_cleanup_temp_file`, `_is_video_file`, `_convert_video_to_audio` | `services/audio_upload.py` |
| `_convert_audio_for_stt` | `services/stt_audio_prep.py` |
| `_get_dynamic_prompt_from_db`, `db_pool` | `services/db.py` |
| `register_result_to_main_backend` | `services/backend_forwarder.py::RegisterResultStrategy` |
| `upload_transcript_to_main_backend` | `services/backend_forwarder.py::UploadTranscriptStrategy` |
| `process_and_upload_with_webhook` | `services/pipeline.py::LegacyDashboardPipeline` |
| `process_and_forward_to_new_dashboard` | `services/pipeline.py::NewDashboardPipeline` |
| `_transcribe_executor` | `services/executor.py::transcribe_executor` |
| konstanta (`ALLOWED_AUDIO_TYPES`, dsb) | `services/config.py` |
| endpoint & response model | `routes/speech_routes.py` (sekarang ramping, ~250 baris vs ~900 baris) |

## Cara pasang (tanpa mengubah `server.py` dulu)

`server.py` versi lama melakukan:
```python
from routes.speech_routes import job_storage, _webhook_queue, _transcribe_executor
```
Supaya `server.py` **tidak perlu diubah dulu** di stage ini, `routes/speech_routes.py`
tetap re-export `job_storage` dan `_transcribe_executor` (lihat komentar
"Backward-compat re-exports" di akhir file). Jadi Anda tinggal:

1. Copy folder `services/`, `routes/` ke project Anda (timpa `routes/speech_routes.py` lama).
2. Pastikan `utils/vars.py` dan `utils/pdf_builder.py` tetap di tempat yang sama (tidak disentuh stage ini).
3. Jalankan seperti biasa — tidak ada perubahan env var atau endpoint.

## Yang BELUM disentuh (stage berikutnya)

- `model/speech_stt.py` (`SpeechSTT` class — God Object, ~1700 baris). Ini
  akan dipecah pakai **Facade** (class `SpeechSTT` jadi orchestrator tipis)
  + **Strategy** (alignment algorithm) + module terpisah untuk DSP
  gender/fraud, emotion, lexical correction, formatter.
- `server.py` — akan disederhanakan setelah `speech_stt.py` selesai, karena
  `get_stt()` singleton-nya bergantung pada struktur `SpeechSTT` yang baru.
- `utils/pdf_builder.py` — sudah cukup rapi (Builder pattern sudah ada),
  paling hanya perlu polish kecil, prioritas rendah.

Balas "lanjut" kalau mau saya teruskan ke `speech_stt.py`.
