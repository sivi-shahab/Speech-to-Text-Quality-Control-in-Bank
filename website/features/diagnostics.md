# Monitoring & housekeeping

Endpoint diagnostik ada di `routes/diagnostics_routes.py` dan **tidak memicu load model**. Endpoint ini membaca instance STT lewat `get_current_instance_or_none()`.

## `GET /health`

Dipakai juga oleh `HEALTHCHECK` Docker.

```json
{
  "status": "healthy",
  "pid": 12345,
  "assigned_gpu": 0,
  "port": 8000,
  "model_loaded": true,
  "asr_backend": "whisper",
  "diarization_engine": "pyannote",
  "pyannote_available": true
}
```

Dengan `ASR_BACKEND=elevenlabs`, `diarization_engine` bernilai `elevenlabs (diarize=true, built-in)`. Field `pyannote_available` dipertahankan untuk kompatibilitas klien lama.

## `GET /api/v1/queue/status`

Ringkasan antrean di proses ini:

| Field | Isi |
|---|---|
| `total` | Jumlah job di `job_storage` |
| `by_status` | Hitungan per status (`pending`, `processing`, `completed`, `failed`) |
| `webhook_q` | Panjang antrean webhook yang belum terkirim |
| `executor_max_workers` | Batas thread `transcribe_executor` |
| `whisper_loaded`, `pyannote_loaded` | Status model |
| `asr_backend`, `diarization_backend` | Backend aktif |

## `GET /gpu/memory`

GPU fisik dan logis, nama GPU, VRAM teralokasi, dan total VRAM. Gunakan endpoint ini untuk memastikan isolasi `CUDA_VISIBLE_DEVICES` berjalan: `physical_gpu_id` harus sama dengan `ASSIGNED_GPU`, dan `logical_gpu_id` harus 0.

## Sapuan file temp

Cleanup normal per job ada di blok `finally` pipeline. Blok itu tidak jalan kalau worker mati paksa (restart Gunicorn, `kill -9`, OOM, worker timeout), sehingga upload yang sedang diproses tertinggal di `TMPDIR`. `services/tmp_cleanup.py` menyapu file tersebut saat startup dan secara berkala.

| Env var | Default | Arti |
|---|---|---|
| `TMP_CLEANUP_ENABLED` | `true` | Aktifkan sweeper |
| `TMP_CLEANUP_MAX_AGE_HOURS` | 6 | Hanya file yang lebih tua dari ini yang dihapus |
| `TMP_CLEANUP_INTERVAL_MINUTES` | 60 | Interval sapuan |

Hanya file dengan prefix milik aplikasi ini yang disentuh (termasuk body mentah endpoint ElevenLabs). Batas umur mencegah file milik job yang masih berjalan, termasuk dari instance lain yang berbagi `/data/tmp`, ikut terhapus.

## Logging

Log memakai tag seperti `[JOB:<id>]`, `[WEBHOOK]`, `[CONVERT]`, dan `[ELEVENLABS-RAW]`. Teks transkrip tidak dicatat di log.
