# Lightweight server & cache

`lightweight_server.py` adalah proses FastAPI kedua (default port **8002**, `LIGHTWEIGHT_PORT`) untuk endpoint yang hanya melakukan query data. Proses ini tidak mengimpor torch, CUDA, atau model STT.

## Kenapa dipisah

Endpoint campaign dan recording dulu ikut di `server.py`. Karena proses itu hanya punya satu worker, request ringan harus antre di belakang transkripsi yang bisa memakan puluhan detik sampai menit, sehingga App B sering kena `Read timed out`. Setelah dipisah:

- tidak ada model yang dimuat, jadi boleh memakai **banyak worker** tanpa risiko OOM GPU,
- request data tidak pernah antre di belakang transkripsi,
- `server.py` tidak perlu di-redeploy saat endpoint data berubah.

CORS proses ini diatur lewat `CORS_ORIGINS_LIGHTWEIGHT`.

## Endpoint

| Endpoint | Fungsi |
|---|---|
| `GET /recording/tms-cashline?load_date=YYYY-MM-DD&context=cashline` | Daftar rekaman TMS yang sudah diproses untuk satu tanggal (default kemarin, WIB), diurutkan per tiket. Tiket juga dicatat ke `dashboard.transcript_processed`. |
| `GET /campaign/cashline-ntb/{result_id}` | Data campaign dan data customer terkait (lewat NIK) dari sumber asli. Hasil terfilter sekaligus disimpan ke cache. `404` kalau campaign tidak ditemukan. |
| `GET /campaign/cashline-ntb-asscend/{result_id}` | Versi cepat: hanya membaca cache. `404` kalau cache kosong; caller sebaiknya fallback ke endpoint di atas. |
| `GET /health` | Health check proses ringan |

## Lapisan cache

```text
GET /campaign/cashline-ntb-asscend/{id}
   │
   ├─ L1 Redis        hit → langsung kembali
   ├─ L2 Postgres     hit → kembali + isi ulang Redis
   └─ keduanya miss   → 404

GET /campaign/cashline-ntb/{id}   (sumber asli)
   └─ tulis ke Postgres (L2) + Redis (L1) + MinIO (backup)
```

| Lapisan | Peran | Konfigurasi |
|---|---|---|
| Redis | L1, di memori, opsional | `REDIS_CACHE_HOST`, `REDIS_CACHE_PORT` (6378), `REDIS_CACHE_DB` (1, karena DB 0 dipakai Celery), `REDIS_CACHE_TTL_SEC` (default 3600) |
| Postgres | L2, sumber kebenaran baca | `DATABASE_URL` |
| MinIO | Backup JSON, **tidak pernah dibaca** saat request | `MINIO_CACHE_ENDPOINT`, `MINIO_CACHE_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_CACHE_SECURE` |

Semua cache bersifat **best-effort**. Kalau Redis atau MinIO mati, fungsi cache gagal tanpa error (get → `None`, set → no-op) dan endpoint tetap menjawab dari Postgres.

## Menjalankan

```bash
python lightweight_server.py
# atau
gunicorn lightweight_server:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:8002
```
