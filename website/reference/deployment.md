# Deployment

## Aturan utama: 1 proses = 1 GPU

Arsitektur service ini adalah **satu proses + asyncio + ThreadPoolExecutor**. Model dimuat sekali per proses dan dibagi ke semua thread.

::: danger Jangan pakai `-w` lebih dari 1
Setiap worker Gunicorn memuat Whisper, pyannote, atau NeMo sendiri, sehingga VRAM terpakai berlipat dan berakhir OOM. Untuk kapasitas lebih besar, jalankan **container terpisah per GPU**.
:::

## Gunicorn (tanpa Docker)

```bash
DEPLOYMENT_MODE=prod ASSIGNED_GPU=0 \
gunicorn server:app \
  -k uvicorn.workers.UvicornWorker \
  -w 1 \
  --bind 0.0.0.0:8000 \
  --timeout 3600
```

## Docker

Base image `pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime` sudah membawa CUDA, cuDNN, dan PyTorch. Image juga memasang `ffmpeg`, `libsndfile1`, dan `curl`, lalu berjalan sebagai user non-root `appuser`.

```bash
# default: whisper + pyannote
docker build -t stt-service .

# dengan ElevenLabs (SDK kecil)
docker build --build-arg INSTALL_ELEVENLABS=true -t stt-service .

# dengan NeMo (berat)
docker build --build-arg INSTALL_NEMO=true -t stt-service .
```

### Satu container per GPU

```bash
docker run -d --name stt-gpu0 \
  --gpus device=0 -p 8000:8000 \
  -e ASSIGNED_GPU=0 \
  --env-file /etc/stt/stt.env \
  -v /data/models:/data/models \
  -v /data/tmp:/data/tmp \
  stt-service

docker run -d --name stt-gpu1 \
  --gpus device=1 -p 8001:8000 \
  -e ASSIGNED_GPU=1 \
  --env-file /etc/stt/stt.env \
  -v /data/models:/data/models \
  -v /data/tmp:/data/tmp \
  stt-service
```

Rahasia (`HF_TOKEN`, `ELEVENLABS_API_KEY`, `DATABASE_URL`, dll.) **tidak** di-bake ke image. `.dockerignore` mengecualikan `.env`.

`HEALTHCHECK` memanggil `/health` setiap 30 detik dengan `start-period` 120 detik, karena memuat model memakan waktu.

## Lightweight server

Jalankan terpisah dari container GPU. Proses ini tidak butuh GPU dan boleh multi-worker:

```bash
gunicorn lightweight_server:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:8002
```

## Checklist sebelum produksi

- [ ] Smoke test dengan satu rekaman nyata di environment GPU target
- [ ] `/gpu/memory` menunjukkan `physical_gpu_id` = `ASSIGNED_GPU`
- [ ] `/health` → `model_loaded: true` dengan backend yang diharapkan
- [ ] `TMPDIR` di volume dengan ruang cukup, dan tmp cleanup aktif
- [ ] Persetujuan compliance tertulis kalau memakai ElevenLabs untuk data produksi
- [ ] `DIARIZATION_V2_MODE` sesuai keputusan rollout (default `off`)
