---
layout: home

hero:
  name: STT Quality Control
  text: Transkripsi & diarization untuk QC telemarketing bank
  tagline: Satu layanan FastAPI berbasis GPU yang mengubah rekaman telepon menjadi transkrip per speaker (Agent / Customer), lengkap dengan gender, emosi, deteksi anomali suara, dan PDF siap audit.
  image:
    src: /logo.svg
    alt: STT Quality Control
  actions:
    - theme: brand
      text: Quick start
      link: /guide/quick-start
    - theme: alt
      text: Kenapa layanan ini?
      link: /guide/why
    - theme: alt
      text: Endpoint API
      link: /reference/api

features:
  - icon: 🎙️
    title: Upload, lupakan, terima webhook
    details: Endpoint async menerima audio atau video, mengantrekan job di ThreadPoolExecutor, lalu mengirim hasilnya lewat webhook atau forward ke backend utama.
    link: /features/async-jobs
    linkText: Job transkripsi async
  - icon: 🔀
    title: Backend ASR bisa diganti
    details: faster-whisper self-hosted (default) atau ElevenLabs Scribe v2 di cloud. Cukup ubah ASR_BACKEND, tanpa mengubah kode.
    link: /features/asr-backends
    linkText: Backend ASR
  - icon: 🗣️
    title: Diarization khusus telepon
    details: pyannote 3.1 dengan tuning telephony, atau NeMo MSDD yang dilatih di korpus telepon. Bisa dikombinasikan bebas dengan backend ASR mana pun.
    link: /features/diarization-backends
    linkText: Backend diarization
  - icon: 🧭
    title: Role Agent / Customer yang tepat
    details: Diarization v2 menentukan role dari cue skrip dan dominansi bicara, bukan dari siapa yang bicara duluan. Bisa diaktifkan bertahap lewat mode off, shadow, atau on.
    link: /features/diarization-v2
    linkText: Diarization v2
  - icon: 📊
    title: Analisis per speaker
    details: Gender berbasis pitch, emosi per giliran bicara, talk-time, dan alert anomali pitch (indikasi suara berganti orang).
    link: /features/speaker-analysis
    linkText: Analisis speaker
  - icon: ⚡
    title: Endpoint sinkron ringan
    details: Kirim body audio mentah ke /api/v1/speech/stt/elevenlabs dan langsung dapat transkrip JSON, opsional dengan diarization dan analisis.
    link: /features/elevenlabs-endpoint
    linkText: Endpoint ElevenLabs
  - icon: 📨
    title: Pengiriman hasil yang tahan gagal
    details: Webhook daemon dengan antrean dan retry, plus strategi forward ke backend utama (Register / UploadTranscript).
    link: /features/delivery
    linkText: Webhook & forwarding
  - icon: 🪶
    title: Query data tanpa antre GPU
    details: Endpoint campaign dan recording berjalan di proses terpisah (port 8002) dengan cache Redis L1, Postgres L2, dan MinIO.
    link: /features/lightweight-server
    linkText: Lightweight server
  - icon: 🩺
    title: Siap dipantau
    details: /health, status antrean, memori GPU, dan sapuan otomatis file temp yatim agar disk tidak penuh.
    link: /features/diagnostics
    linkText: Monitoring
  - icon: 🧪
    title: Evaluasi offline yang aman PII
    details: Harness DER/JER dan role accuracy dari raw response yang sudah ada, tanpa memanggil API. Cache dijaga di luar repo dengan izin 0700.
    link: /features/diar-eval
    linkText: Evaluasi diarization
---

<div class="vp-doc home-extra">

## Cara kerja

Setiap jalur masuk melewati langkah yang sama sebelum hasil keluar ke dashboard.

```text
  Dashboard lama ──┐   POST /api/v1/speech/stt/save
  Dashboard baru ──┼─▶ POST /api/v1/speech/stt/save_dashboard
  Klien lain     ──┘   POST /api/v1/speech/stt/elevenlabs (sinkron)
                         │
                         ▼
 ┌───────────────────────────── server.py (1 proses / 1 GPU) ─────────────────────────────┐
 │ 1. Terima      validasi upload, video → audio (ffmpeg), simpan ke TMPDIR                │
 │ 2. Antre       job_storage + ThreadPoolExecutor (batas MAX_PENDING_JOBS)                │
 │ 3. Siapkan     phase-fix, resample, dynamic range compression                           │
 │ 4. Transkrip   ASR backend: faster-whisper │ ElevenLabs Scribe v2                       │
 │ 5. Diarize     pyannote │ NeMo MSDD │ Scribe (bawaan)  ──▶ diarization v2 (opsional)    │
 │ 6. Analisis    gender, emosi, talk-time, alert anomali pitch                            │
 │ 7. Kirim       PDF transkrip ──▶ webhook │ forward ke backend utama                     │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
                         │
                         ▼
        Backend utama / App B (dashboard QC)  ◀──  lightweight_server.py :8002
                                                   (campaign, recording, cache)
```

## Pilih jalur

| Saya ingin… | Mulai dari |
|---|---|
| Menjalankan service di mesin dev | [Quick start](/guide/quick-start) |
| Mengirim rekaman dan menerima hasil lewat webhook | [Job transkripsi async](/features/async-jobs) |
| Transkrip cepat satu file tanpa antrean | [Endpoint ElevenLabs sinkron](/features/elevenlabs-endpoint) |
| Mengganti Whisper ke ElevenLabs (atau sebaliknya) | [Backend ASR](/features/asr-backends) |
| Memperbaiki Agent/Customer yang tertukar | [Diarization v2](/features/diarization-v2) |
| Men-tuning diarization untuk audio telepon mono | [Backend diarization](/features/diarization-backends) |
| Mengukur akurasi diarization dengan ground truth | [Evaluasi diarization](/features/diar-eval) |
| Men-deploy ke produksi (Docker, multi-GPU) | [Deployment](/reference/deployment) |
| Melihat semua env var | [Konfigurasi](/reference/configuration) |

</div>
