# Job transkripsi async

Dua endpoint upload menerima file, langsung mengembalikan `job_id`, lalu memproses transkripsi di background thread. Keduanya memakai pipeline yang sama dan hanya berbeda di cara hasilnya dikirim.

| Endpoint | Dipakai oleh | Hasil dikirim lewat |
|---|---|---|
| `POST /api/v1/speech/stt/save` | Dashboard lama | Webhook ke `webhook_url` (`LegacyDashboardPipeline`) |
| `POST /api/v1/speech/stt/save_dashboard` | Dashboard baru | Forward ke backend utama, plus webhook opsional (`NewDashboardPipeline`) |

## Parameter (multipart form)

| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `audio_file` | file | wajib | Audio atau video. Video dikonversi ke audio dengan ffmpeg. |
| `enable_diarization` | string bool | `"false"` | `true`, `1`, `yes`, `on` dianggap aktif |
| `language` | string | `"id"` | Kode bahasa untuk ASR |
| `webhook_url` | string | – | URL penerima notifikasi status |
| `original_path`, `marker_path` | string | – | Hanya `/save`. Diteruskan apa adanya ke payload webhook. |
| `campaign` | string | – | Hanya `/save_dashboard`. Fallback ke `DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN`. |

```bash
curl -X POST http://host:8000/api/v1/speech/stt/save_dashboard \
  -F "audio_file=@call.wav" \
  -F "enable_diarization=true" \
  -F "campaign=Cashline"
```

::: tip ElevenLabs selalu memakai diarization
Saat `ASR_BACKEND=elevenlabs`, job otomatis di-upgrade ke mode diarization walaupun `enable_diarization=false`. Data speaker datang gratis dari panggilan API yang sama, dan analisis gender/emosi jadi per speaker. Akibatnya teks output memakai format per speaker.
:::

## Siklus status

```text
pending ──▶ processing ──▶ completed
                     └───▶ failed  (error_message terisi)
```

| Endpoint | Fungsi |
|---|---|
| `GET /api/v1/speech/job/{job_id}` | Status satu job (`404` kalau tidak ada) |
| `GET /api/v1/speech/stt/status/{job_id}` | Status satu job, format alternatif |
| `GET /api/v1/speech/jobs/status` | Rekap jumlah job per status |

Status disimpan di memori proses (`ThreadSafeJobStorage`), jadi hilang saat restart. Sumber kebenaran jangka panjang adalah webhook dan backend utama.

## Backpressure

- Kalau jumlah job `pending` + `processing` mencapai `MAX_PENDING_JOBS` (default 200), endpoint menolak dengan **503**. Klien sebaiknya retry dengan backoff.
- Ukuran upload dibatasi `MAX_UPLOAD_SIZE_MB` (default 1000).
- Paralelisme transkripsi diatur `MAX_TRANSCRIBE_WORKERS` (default 50) di `ThreadPoolExecutor`. Akses ke model GPU tetap diserialisasi di dalam engine.

## Validasi upload

- File kosong → **400** `File kosong.`
- Tipe audio tidak dikenali → ditolak oleh `validate_audio_file()`
- Gagal menulis temp file → **500**
- Semua file temp dibersihkan di setiap jalur error. File yatim yang tersisa disapu oleh [tmp cleanup](/features/diagnostics#sapuan-file-temp).
