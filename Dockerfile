# =================================================================
# Speech STT Service — Dockerfile
#
# GPU-dependent (torch, faster-whisper/CTranslate2, pyannote.audio) —
# base image sudah termasuk CUDA + cuDNN + PyTorch, supaya tidak perlu
# install CUDA toolkit manual (rawan salah versi).
#
# WAJIB baca sebelum build/run:
#   - Arsitektur service ini 1-process + ThreadPoolExecutor (lihat
#     services/config.py MAX_TRANSCRIBE_WORKERS). Container HARUS
#     dijalankan dengan gunicorn -w 1 (SATU worker) -- kalau lebih,
#     model Whisper/pyannote/NeMo akan di-load ULANG per worker process,
#     VRAM terpakai berkali lipat -> OOM. Untuk kapasitas lebih besar,
#     jalankan BEBERAPA CONTAINER terpisah (1 container = 1 GPU), bukan
#     naikkan angka -w di 1 container.
#   - HF_TOKEN, ELEVENLABS_API_KEY, DATABASE_URL, dll TIDAK di-bake ke
#     image ini (alasan keamanan) -- WAJIB di-pass saat `docker run`
#     lewat -e atau --env-file, lihat contoh di akhir file ini.
#
# CATATAN KEYAKINAN: saya TIDAK bisa memverifikasi versi Python persis
# yang dipakai base image pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime
# di sandbox saya (tidak ada akses internet/docker untuk cek langsung).
# Karena itu Dockerfile ini SENGAJA tidak pakai multi-stage copy
# site-packages yang bergantung nebak path versi Python (itu rawan
# gagal diam-diam kalau tebakan salah) -- install dependency langsung
# di 1 stage. Trade-off: image sedikit lebih besar, tapi TIDAK ada
# risiko silent-fail dari tebakan path yang salah. WAJIB build & jalankan
# sendiri untuk verifikasi sebelum dipakai produksi.
#
# Build (default -- whisper + pyannote saja, backend opsional TIDAK ikut):
#   docker build -t stt-service .
#
# Build dengan ElevenLabs (ringan, cukup 1 SDK kecil):
#   docker build --build-arg INSTALL_ELEVENLABS=true -t stt-service .
#
# Build dengan NeMo diarization (BERAT, nemo_toolkit besar):
#   docker build --build-arg INSTALL_NEMO=true -t stt-service .
# =================================================================

FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

ARG INSTALL_ELEVENLABS=false
ARG INSTALL_NEMO=false

# ffmpeg      : WAJIB -- convert audio/video sebelum transcribe
# libsndfile1 : dependency soundfile/librosa
# curl        : dipakai HEALTHCHECK di bawah
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

RUN if [ "$INSTALL_ELEVENLABS" = "true" ]; then \
        pip install --no-cache-dir --break-system-packages elevenlabs; \
    fi
RUN if [ "$INSTALL_NEMO" = "true" ]; then \
        pip install --no-cache-dir --break-system-packages "nemo_toolkit[asr]"; \
    fi

# Copy source code -- .dockerignore mengecualikan file yang tidak perlu
# (finetune_asr/, .git, __pycache__, .env asli, dst)
COPY . .

RUN mkdir -p /data/tmp /data/models && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app /data

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TMPDIR=/data/tmp

EXPOSE 8000

# Healthcheck pakai endpoint /health yang sudah ada di diagnostics_routes.py
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# WAJIB "-w 1" -- lihat penjelasan di komentar atas file ini
CMD ["gunicorn", "server:app", \
     "-w", "1", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "3600"]

# =================================================================
# Contoh menjalankan (1 container = 1 GPU, sesuai arsitektur service):
#
#   docker run -d --name stt-gpu0 \
#       --gpus device=0 \
#       -p 8000:8000 \
#       -e ASSIGNED_GPU=0 \
#       -e HF_TOKEN=hf_xxx \
#       -e DATABASE_URL=postgresql://user:pass@host:5432/da \
#       -e ASR_BACKEND=whisper \
#       -e DIARIZATION_BACKEND=pyannote \
#       -v /data/models:/data/models \
#       -v /data/tmp:/data/tmp \
#       stt-service
#
# Untuk GPU kedua (2xL40S dst), container TERPISAH, port & GPU beda:
#
#   docker run -d --name stt-gpu1 \
#       --gpus device=1 \
#       -p 8001:8000 \
#       -e ASSIGNED_GPU=1 \
#       ... (env var lain sama) \
#       stt-service
#
# Lebih disarankan: pakai --env-file .env daripada -e satu-satu supaya
# tidak lupa satu variabel pun (lihat .env.example yang sudah dibuat
# sebelumnya di sesi ini).
# =================================================================
