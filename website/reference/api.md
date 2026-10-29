# Endpoint API

FastAPI menyediakan dokumentasi interaktif di `/docs` (Swagger UI) dan `/redoc` di setiap proses.

## `server.py` (port 8000, 1 per GPU)

| Method | Path | Fungsi | Detail |
|---|---|---|---|
| `POST` | `/api/v1/speech/stt/save` | Upload, job async, hasil via webhook | [Job async](/features/async-jobs) |
| `POST` | `/api/v1/speech/stt/save_dashboard` | Upload, job async, forward ke backend utama | [Job async](/features/async-jobs) |
| `GET` | `/api/v1/speech/job/{job_id}` | Status satu job | [Job async](/features/async-jobs#siklus-status) |
| `GET` | `/api/v1/speech/stt/status/{job_id}` | Status satu job (format alternatif) | |
| `GET` | `/api/v1/speech/jobs/status` | Rekap job per status | |
| `POST` | `/api/v1/speech/stt/elevenlabs` | Transkripsi sinkron, body audio mentah | [Endpoint ElevenLabs](/features/elevenlabs-endpoint) |
| `GET` | `/health` | Health check | [Monitoring](/features/diagnostics) |
| `GET` | `/api/v1/queue/status` | Status antrean dan model | [Monitoring](/features/diagnostics) |
| `GET` | `/gpu/memory` | Memori GPU | [Monitoring](/features/diagnostics) |
| `POST` | `/speech/webhook/callback` | Penerima webhook contoh | [Webhook](/features/delivery) |

## `lightweight_server.py` (port 8002)

| Method | Path | Fungsi |
|---|---|---|
| `GET` | `/recording/tms-cashline` | Daftar rekaman TMS yang sudah diproses |
| `GET` | `/campaign/cashline-ntb/{result_id}` | Data campaign + customer dari sumber, sekaligus mengisi cache |
| `GET` | `/campaign/cashline-ntb-asscend/{result_id}` | Data campaign dari cache saja |
| `GET` | `/health` | Health check |

Detail di [Lightweight server & cache](/features/lightweight-server).

## Kode status umum

| Status | Kapan |
|---|---|
| `200` | Sukses. Untuk endpoint async, artinya job **diterima**, bukan selesai. |
| `400` | File kosong, atau input ditolak ElevenLabs |
| `404` | Job, campaign, atau cache tidak ditemukan |
| `500` | Gagal menyimpan upload |
| `502` | Error dari ElevenLabs |
| `503` | Antrean penuh (`MAX_PENDING_JOBS`), API key ElevenLabs kosong, atau rate limit tidak pulih |
