# Backend ASR

Mesin ASR dipilih dengan `ASR_BACKEND`. Semua backend mengimplementasikan interface `ASRBackend` di `model/speech_stt/asr_backends/base.py` (pola Strategy).

| `ASR_BACKEND` | Implementasi | Lokasi | Diarization |
|---|---|---|---|
| `whisper` (default) | faster-whisper, model dari `MODEL_WHISPER` | GPU lokal | Dari [backend diarization](/features/diarization-backends) |
| `elevenlabs` | ElevenLabs Scribe v2 (`ELEVENLABS_MODEL_ID`) | Cloud | Bawaan API (`provides_own_diarization = True`) |

## Whisper (default)

- Model dimuat sekali per proses lewat `model_registry.py` dan dibagi ke semua thread.
- **Dynamic prompt**: untuk tiket tertentu, prompt domain diambil dari Postgres (`services/db.py`) dan dikirim sebagai `initial_prompt` agar istilah produk ditranskrip dengan benar.
- Hasil ASR di-align ke segmen diarization dengan `PureOverlapAlignmentStrategy`, lalu diverifikasi dengan pitch.

## ElevenLabs Scribe v2

Scribe mengembalikan teks dan label speaker dalam **satu panggilan API**. Karena itu engine:

- tidak memuat pyannote/NeMo sama sekali,
- tidak menjalankan alignment ASR ↔ diarization,
- hanya memetakan speaker ke Agent/Customer lalu menggabungkan segmen berurutan.

Ini jalur paling sederhana karena tidak ada dua model yang perlu dicocokkan.

```bash
ASR_BACKEND=elevenlabs
ELEVENLABS_API_KEY=sk_xxx
ELEVENLABS_MODEL_ID=scribe_v2
ELEVENLABS_NUM_SPEAKERS=2
ELEVENLABS_KEYTERMS=Istilah Produk A,Istilah Produk B
ELEVENLABS_TAG_AUDIO_EVENTS=false
ELEVENLABS_REQUEST_TIMEOUT_SEC=300
```

### Keyterm vs `initial_prompt`

Scribe tidak memakai paragraf prompt ala Whisper. Mekanismenya **keyterm prompting**: daftar istilah (maks. 1000, < 50 karakter, ≤ 5 kata per term) yang akurasinya di-boost. Parameter `initial_prompt` diterima demi konsistensi interface, tetapi diabaikan. Isi `ELEVENLABS_KEYTERMS` secara statis, atau `SCRIBE_KEYTERMS_FILE` (YAML per campaign) untuk diarization v2.

### Panggilan sinkron

Job sudah berjalan di thread background, jadi panggilan API dibuat sinkron di dalam thread itu. Tidak perlu endpoint callback publik untuk ElevenLabs. Untuk panggilan sangat panjang, naikkan `ELEVENLABS_REQUEST_TIMEOUT_SEC`.

### Format response yang dipakai

```json
{
  "language_code": "ind",
  "segments": [
    {
      "text": "…", "start_time": 3.86, "end_time": 7.38,
      "speaker": { "id": "speaker_0", "name": "Speaker 0" },
      "words": [{ "text": "…", "start_time": 3.86, "end_time": 4.04 }]
    }
  ]
}
```

`language_code` memakai ISO 639-3 (`ind`), sedangkan parameter request biasanya ISO 639-1 (`id`). Perbedaan ini hanya memengaruhi metadata `detected_language`.

## Build Docker dengan ElevenLabs

```bash
docker build --build-arg INSTALL_ELEVENLABS=true -t stt-service .
```
