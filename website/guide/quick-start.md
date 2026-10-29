# Quick start

Halaman ini menjalankan service di mesin dev dengan GPU, lalu mengirim satu rekaman.

## Prasyarat

- Python 3.11, GPU NVIDIA dengan CUDA (default memakai faster-whisper + pyannote di GPU)
- `ffmpeg` dan `libsndfile1` terpasang di sistem
- Token Hugging Face (`HF_TOKEN`) yang punya akses ke model pyannote
- Postgres (`DATABASE_URL`) untuk lookup dynamic prompt dan data campaign

## 1. Pasang dependency

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Opsional
pip install elevenlabs              # kalau memakai ASR_BACKEND=elevenlabs
pip install "nemo_toolkit[asr]"     # kalau memakai DIARIZATION_BACKEND=nemo (berat)
```

## 2. Siapkan `.env`

`.env` sudah masuk `.gitignore`, jadi isi rahasia tidak akan ikut ter-commit. Contoh minimal:

```bash
DEPLOYMENT_MODE=dev
PORT=8000
ASSIGNED_GPU=0
TMPDIR=/data/tmp

HF_TOKEN=hf_xxx
DATABASE_URL=postgresql://user:pass@host:5432/db

ASR_BACKEND=whisper            # whisper | elevenlabs
DIARIZATION_BACKEND=pyannote   # pyannote | nemo
```

Daftar lengkap ada di [Konfigurasi](/reference/configuration).

## 3. Jalankan

```bash
python server.py
```

`python server.py` hanya jalan saat `DEPLOYMENT_MODE=dev`. Di mode lain, server menolak start dan meminta Gunicorn (lihat [Deployment](/reference/deployment)).

Model dimuat sekali saat startup lewat `lifespan()`. Cek statusnya:

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "model_loaded": true,
  "asr_backend": "whisper",
  "diarization_engine": "pyannote"
}
```

## 4. Kirim rekaman pertama

```bash
curl -X POST http://localhost:8000/api/v1/speech/stt/save \
  -F "audio_file=@call.wav" \
  -F "enable_diarization=true" \
  -F "language=id" \
  -F "webhook_url=http://localhost:8000/speech/webhook/callback"
```

Response langsung kembali dengan `job_id`, dan transkripsi berjalan di background:

```json
{ "job_id": "…", "status": "accepted", "message": "…", "worker_pid": 12345 }
```

Pantau statusnya:

```bash
curl http://localhost:8000/api/v1/speech/job/<job_id>
```

## 5. Endpoint sinkron (opsional)

Kalau `ELEVENLABS_API_KEY` sudah diisi, Anda bisa mendapat transkrip langsung tanpa antrean:

```bash
curl -X POST "http://localhost:8000/api/v1/speech/stt/elevenlabs?diarize=true" \
  -H "Content-Type: audio/mpeg" --data-binary @call.mp3
```

## Menjalankan test

```bash
pip install -r requirements-dev.txt
pytest
ruff check model/speech_stt/diarization_v2 tests tools
```

## Selanjutnya

- [Arsitektur](/guide/architecture): bagaimana request mengalir dari route ke engine
- [Job transkripsi async](/features/async-jobs): parameter lengkap endpoint upload
