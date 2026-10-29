# Endpoint ElevenLabs sinkron

`POST /api/v1/speech/stt/elevenlabs` mentranskrip satu file lewat ElevenLabs Scribe dan langsung mengembalikan hasilnya. Tidak ada job, antrean, PDF, maupun webhook.

Body request adalah **bytes audio mentah** dengan format apa saja, bukan multipart.

```bash
curl -X POST "http://host:8000/api/v1/speech/stt/elevenlabs?diarize=true" \
  -H "Content-Type: audio/mpeg" \
  --data-binary @call.mp3
```

## Query parameter

| Parameter | Default | Efek |
|---|---|---|
| `language` | `id` | Bahasa untuk Scribe |
| `diarize` | `false` | `segments` berisi label speaker. Yang bicara duluan = agent. |
| `analysis` | `false` | Diarization ElevenLabs + blok `analysis` (gender, emosi, fraud alert, talk-time). `segments` berisi giliran bicara yang sudah di-merge, masing-masing dengan `emotion`. |

`analysis=true` memakai model gender/emosi lokal, jadi request ini berjalan di `transcribe_executor`. Endpoint ini bisa dipakai dengan `ASR_BACKEND` apa pun, karena client ElevenLabs dibuat terpisah dari backend ASR utama.

## Contoh response (`analysis=true`)

```json
{
  "text": "…",
  "language": "ind",
  "audio_duration_secs": 182.4,
  "segments": [
    { "start": 3.86, "end": 7.38, "speaker": "Agent", "speaker_id": "speaker_0", "text": "…", "emotion": "NETRAL" }
  ],
  "analysis": {
    "speaker_genders":  { "Agent": "female", "Customer": "male" },
    "speaker_emotions": { "Agent": { "POSITIF": 4, "NEGATIF": 0, "NETRAL": 12, "TOTAL": 16 } },
    "fraud_alerts":     [],
    "talk_time":        { }
  }
}
```

| Field | Isi |
|---|---|
| `segments[].emotion` | `POSITIF`, `NEGATIF`, atau `NETRAL`. Hanya ada saat `analysis=true`. |
| `analysis.speaker_genders` | `male`, `female`, atau `unknown` per speaker |
| `analysis.speaker_emotions` | Hitungan emosi per speaker, plus `TOTAL` |
| `analysis.fraud_alerts` | Daftar alert anomali pitch (lihat [Analisis speaker](/features/speaker-analysis)) |
| `analysis.talk_time` | Porsi waktu bicara per speaker |

Skema lengkapnya ada di model Pydantic `ElevenLabsSTTResponse` di `routes/elevenlabs_stt_routes.py`.

## Error

| Status | Penyebab |
|---|---|
| `400` | ElevenLabs menolak input (`400`/`422` dari API) |
| `502` | Error lain dari ElevenLabs |
| `503` | API key belum diisi, atau rate limit `429` tetap gagal setelah `ELEVENLABS_429_RETRIES` kali retry |

Konkurensi ke API dibatasi semaphore `ELEVENLABS_MAX_CONCURRENCY`. File temp body selalu dihapus di blok `finally`.

::: warning Kepatuhan
Audio dikirim ke server ElevenLabs. Lihat [catatan kepatuhan](/guide/why#kepatuhan-data).
:::
