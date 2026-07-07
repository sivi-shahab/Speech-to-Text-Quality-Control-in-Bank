# Opsi ASR Backend: ElevenLabs Scribe v2 (Cloud, Unified ASR+Diarization)

Menambahkan [ElevenLabs Scribe v2](https://elevenlabs.io/docs/overview/capabilities/speech-to-text)
sebagai opsi ASR ke-4. **BEDA FUNDAMENTAL** dari 3 opsi sebelumnya
(whisper/meralion/whisperx): ini **cloud API pihak ketiga**, bukan model
self-hosted. Default TIDAK BERUBAH — tetap faster-whisper kecuali Anda
set env var eksplisit.

## ⚠️ WAJIB: status compliance

Sebelum bagian ini ditulis, sudah dikonfirmasi bahwa **tim legal/compliance
sudah/akan mengecek soal data residency** (audio dikirim ke server
ElevenLabs, region resmi yang dipublikasikan: **EU & India** — TIDAK ada
region Indonesia). Kode di sini SENGAJA menaruh pesan pengingat compliance
ini di `ElevenLabsBackend.load()` — tapi keputusan sebenarnya ada di
LUAR kode, di tangan tim legal/compliance Anda. **Jangan enable
`ASR_BACKEND=elevenlabs` untuk data nasabah produksi asli tanpa
konfirmasi tertulis dari mereka.**

## Cara enable

```bash
# .env
ASR_BACKEND=elevenlabs
ELEVENLABS_API_KEY=sk_xxxxx          # dari https://elevenlabs.io/app/settings/api-keys
ELEVENLABS_MODEL_ID=scribe_v2        # default
ELEVENLABS_NUM_SPEAKERS=2            # default, sesuai agent+customer
```

Opsional:
```bash
ELEVENLABS_KEYTERMS=Mega Cashline,Ultima Shield,Megalife   # pengganti initial_prompt-style
ELEVENLABS_TAG_AUDIO_EVENTS=false     # true kalau mau tag [laughter]/[music] dsb di transkrip
```

## Kenapa arsitekturnya beda dari 3 opsi sebelumnya

Scribe v2 mengembalikan **teks + speaker label per kata dalam SATU
panggilan API** — bukan 2 model terpisah (ASR + diarization) yang perlu
"dicocokkan". Karena itu saya tambahkan flag baru di interface
`ASRBackend`:

```python
provides_own_diarization: bool = False   # default False untuk semua backend lain
```

Kalau `True` (ElevenLabs), `engine.py`:
- **Tidak memuat pyannote/NeMo sama sekali** (hemat resource, tidak ada
  yang perlu di-load untuk diarization)
- **Tidak memanggil `PureOverlapAlignmentStrategy` atau jalur per-segmen
  ala MERaLiON** — speaker label sudah final dari API, tinggal lewat
  `map_speakers_to_binary()` yang sama (supaya konsisten AGENT_BM/NASABAH
  dengan backend lain) lalu `merge_same_speaker_simple()` untuk
  menggabung kata berurutan jadi segmen.

Ini pipeline PALING SEDERHANA di antara semua opsi — tidak ada mesin
koreksi/alignment yang bisa salah, karena tidak ada 2 model yang perlu
dicocokkan.

## Kenapa tidak ada Factory/shared-loading di model_registry.py

Semua backend lain (Whisper, MERaLiON, WhisperX, NeMo, pyannote) adalah
model BERAT yang perlu dimuat ke VRAM sekali dan di-share lintas thread
— makanya semua punya pola `_SHARED_XXX` dict + lock di
`model_registry.py`. ElevenLabs backend **tidak punya model** sama
sekali di sisi Anda — cuma HTTP client ringan ke API eksternal. Tidak
ada VRAM yang perlu dikelola, tidak ada "warmup", tidak ada concern
concurrency GPU. `load()` di backend ini cuma memvalidasi API key &
membuat instance client — instant, tidak perlu di-cache lintas thread
(SDK client ElevenLabs sendiri thread-safe untuk request paralel,
sesuai desain REST client pada umumnya).

## Keyterm prompting vs `initial_prompt`

Sistem Anda melakukan lookup dynamic prompt per tiket (deteksi Mega
Cashline) dan mengirim paragraf panjang sebagai `initial_prompt` ke
Whisper. Scribe v2 punya mekanisme berbeda: **"keyterm prompting"** —
daftar sampai 1000 istilah/frasa spesifik yang di-boost akurasinya,
BUKAN paragraf instruksi gaya prompt Whisper. Saya map `initial_prompt`
parameter (untuk konsistensi interface `ASRBackend`) tapi **backend ini
mengabaikannya** — kalau mau manfaatkan fitur serupa, isi
`ELEVENLABS_KEYTERMS` dengan istilah domain Anda (mis. "Mega Cashline",
"Ultima Shield") secara statis di config, BUKAN dinamis per-tiket seperti
mekanisme DB lookup yang sudah ada. Kalau butuh keyterms dinamis per-tiket
juga, itu perlu penyesuaian tambahan (bisa saya buat kalau diperlukan).

## Catatan implementasi lain

- **Sinkron, bukan async+webhook**: ElevenLabs mendukung async+webhook
  untuk file sangat panjang, tapi karena job Anda sudah berjalan di
  background thread sendiri (ThreadPoolExecutor), saya pakai panggilan
  API **sinkron/blocking** di dalam thread itu — lebih sederhana, tidak
  perlu expose endpoint publik tambahan untuk callback ElevenLabs.
  Kalau nanti ada call sangat panjang (>10 menit?) yang bikin request
  timeout, pertimbangkan `ELEVENLABS_REQUEST_TIMEOUT_SEC` lebih besar
  dulu, baru evaluasi perlu tidaknya jalur async+webhook resmi mereka.
- **Skema parameter API best-effort**: nama parameter (`num_speakers`,
  `keyterms`, `tag_audio_events`) saya ambil dari dokumentasi resmi per
  saat ini, dibungkus fallback (`try/except TypeError`) kalau SDK versi
  Anda ternyata beda skema — tapi tetap validasi sendiri sebelum
  production.

## File yang ditambahkan/diubah

| File | Perubahan |
|---|---|
| `model/speech_stt/config.py` | + `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL_ID`, `ELEVENLABS_NUM_SPEAKERS`, `ELEVENLABS_KEYTERMS`, dsb |
| `model/speech_stt/asr_backends/base.py` | + `provides_own_diarization` flag, + method `transcribe_with_speakers()` di interface `ASRBackend` |
| `model/speech_stt/asr_backends/elevenlabs_backend.py` | **BARU** — implementasi `ElevenLabsBackend` |
| `model/speech_stt/engine.py` | Phase 1 (load ASR) + Phase 3 (skip diarization backend) + `transcribe_with_diarization()` (cabang baru paling atas) menangani `provides_own_diarization=True`. **Jalur default (whisper+pyannote) tidak berubah.** |

## ✅ Skema response SUDAH divalidasi dengan data nyata (bukan asumsi lagi)

Draft awal modul ini salah asumsi struktur response (mengira ada array flat
`words[]` per-kata yang perlu digabung manual). Setelah Anda kasih contoh
JSON asli hasil test langsung ke API, ternyata strukturnya:

```json
{
  "language_code": "ind",
  "segments": [
    {
      "text": "Halo selamat sore dengan Ibu Suliana? ",
      "start_time": 3.86, "end_time": 7.38,
      "speaker": {"id": "speaker_0", "name": "Speaker 0"},
      "words": [{"text": "Halo", "start_time": 3.86, "end_time": 4.04}, ...]
    }
  ]
}
```

Perbedaan dari asumsi awal:
- Key top-level `segments` (bukan `words` flat).
- Tiap segmen **sudah** berisi speaker + teks lengkap + `start_time`/`end_time`
  — ElevenLabs sendiri sudah menggabungkan kata jadi giliran-bicara per
  speaker. Kode saya SEDERHANAKAN (fungsi `_merge_words_by_speaker` versi
  draft awal dihapus, sudah tidak dibutuhkan).
- Field waktu `start_time`/`end_time` (bukan `start`/`end`).
- `speaker` adalah objek `{"id":..., "name":...}`, bukan string flat.
- `words[]` cuma nested untuk word-level timestamp di mode simple
  (non-diarization), bukan struktur utama.

**Sudah divalidasi otomatis** — saya simulasikan object response dari JSON
asli Anda dan jalankan `_extract_segments_with_speaker()` langsung, hasilnya
cocok persis (speaker, teks, timestamp semua benar). Ini validasi
fungsional beneran, bukan cuma `py_compile`.

## ⚠️ Yang masih perlu Anda perhatikan

- `response.language_code` pakai ISO 639-3 (`"ind"`), sementara parameter
  `language` yang dikirim ke API biasanya ISO 639-1 (`"id"`) — dua konvensi
  beda. Dibiarkan apa adanya di kode (tidak dikonversi paksa) karena cuma
  memengaruhi field informational (`detected_language` di metadata), bukan
  hasil transkrip. Kalau butuh konsisten, sesuaikan sendiri saat baca
  metadata-nya.
- Parameter `num_speakers` untuk oracle speaker count **masih belum
  terverifikasi langsung** dari sample JSON yang Anda kasih (sample-nya
  respons hasil transcribe, bukan skema request). Kalau parameter itu
  ternyata salah nama, fallback `except TypeError` akan retry tanpa
  parameter itu — tapi hasil diarization mungkin kurang optimal untuk
  kasus 2-speaker yang sudah pasti.


