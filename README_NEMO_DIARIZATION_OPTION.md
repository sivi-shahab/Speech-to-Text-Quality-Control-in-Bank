# Opsi Diarization Backend: NeMo (NeuralDiarizer / MSDD Telephonic)

Menambahkan opsi ganti mesin diarization dari pyannote ke
[NVIDIA NeMo](https://github.com/NVIDIA-NeMo/NeMo) — spesifiknya checkpoint
`diar_msdd_telephonic`, yang dilatih KHUSUS di ~1500 jam percakapan
telepon (Fisher Corpus), BUKAN model generik. Default TIDAK BERUBAH —
pyannote tetap dipakai kecuali Anda eksplisit set env var.

## Cara enable

```bash
# .env
DIARIZATION_BACKEND=nemo
NEMO_DIARIZER_CONFIG_PATH=/path/ke/diar_infer_telephonic.yaml   # WAJIB, lihat di bawah
```

Bisa dikombinasikan bebas dengan pilihan ASR backend (whisper/meralion,
lihat `README_MERALION_OPTION.md`) — keduanya sumbu variasi independen:

| `ASR_BACKEND` | `DIARIZATION_BACKEND` | Yang dimuat ke VRAM |
|---|---|---|
| `whisper` (default) | `pyannote` (default) | Whisper + pyannote + gender + emotion — **jalur asli, tidak berubah** |
| `whisper` | `nemo` | Whisper + NeMo (VAD+TitaNet+MSDD) + gender + emotion — pyannote TIDAK ikut dimuat |
| `meralion` | `pyannote` | MERaLiON + pyannote + gender + emotion |
| `meralion` | `nemo` | MERaLiON + NeMo + gender + emotion |

## ⚠️ WAJIB: dapatkan file config resmi dulu

NeMo diarization dikendalikan lewat config hydra/omegaconf yang cukup
dalam (VAD model, speaker embedding model, clustering params, MSDD
params, dsb). Saya **sengaja tidak menulis ulang config itu manual** di
kode ini — skemanya bisa berubah antar rilis NeMo, dan salah tulis field
bisa gagal diam-diam atau error samar. Ambil dari sumber resmi:

```bash
wget https://raw.githubusercontent.com/NVIDIA/NeMo/main/examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml
# simpan di server Anda, lalu:
export NEMO_DIARIZER_CONFIG_PATH=/path/ke/diar_infer_telephonic.yaml
```

Kalau env var ini tidak di-set atau file tidak ditemukan, `load_model()`
akan gagal dengan pesan error yang menjelaskan persis langkah di atas
(bukan silent fail).

## Dependency tambahan

```bash
pip install "nemo_toolkit[asr]"
```

⚠️ **`nemo_toolkit` adalah package besar** dengan banyak dependency
transitif (termasuk versi `pytorch-lightning`, `hydra-core`, `omegaconf`
tertentu) yang berpotensi bentrok dengan `transformers`/`faster-whisper`/
`pyannote.audio` yang sudah ada di environment yang sama. **Test dulu di
staging** sebelum enable di production — kalau bentrok, pertimbangkan
menjalankan worker NeMo di container/virtualenv terpisah.

## Kenapa NeMo relevan untuk telephony (bukan cuma "opsi lain")

Ini jawaban langsung untuk concern Anda sebelumnya soal diarization
telephony: `pyannote/speaker-diarization-3.1` dilatih mayoritas di data
meeting/broadcast. `diar_msdd_telephonic` (NeMo) dilatih **khusus** di
Fisher Corpus — korpus percakapan telepon asli — dengan DER benchmark
yang dipublikasikan resmi untuk domain telephonic. Kalau tuning
hyperparameter pyannote (lihat `README_TELEPHONY_DIARIZATION_TUNING.md`)
masih belum cukup, ini opsi ganti model yang domain-nya memang cocok.

## ⚠️ Perbedaan arsitektural dari pyannote

| Aspek | pyannote (default) | NeMo MSDD |
|---|---|---|
| Cara panggil | In-memory, langsung `pipeline(audio_path)` | Berbasis file: tulis manifest JSON → `diarize()` → baca RTTM dari disk |
| Kecepatan per-request | Cepat (in-memory) | Lebih lambat (I/O manifest+RTTM per panggilan) |
| Concurrency | Semaphore(1) sebagai hardening tambahan (HOTFIX A) | **Lock WAJIB** — [race condition resmi dilaporkan ke NVIDIA](https://github.com/NVIDIA-NeMo/NeMo/issues/12254) kalau dipanggil concurrent tanpa lock (error "Mismatch of counts between embedding vectors and timestamps") |
| Domain training | Meeting/broadcast (AMI, VoxConverse, DIHARD) | **Telepon asli** (Fisher Corpus, ~1500 jam) |

## ⚠️ Soal race condition MSDD — baca ini sebelum production

Saya implementasikan lock per-GPU yang membungkus SETIAP panggilan
`.diarize()` (`get_nemo_diarizer_lock()` di `model_registry.py`) — ini
secara langsung menjawab skenario yang dilaporkan di issue #12254
(mereka memanggil `diarizer(...)` concurrent TANPA lock eksplisit).
Instance `NeuralDiarizer` dimuat SEKALI dan dipakai ulang (default,
demi performa — reload checkpoint VAD+TitaNet+MSDD dari disk itu berat).

**Kalau setelah dikunci pun error itu masih muncul** (belum saya
validasi langsung karena tidak ada GPU di sandbox saya), set:
```bash
NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL=true
```
Ini bikin instance `NeuralDiarizer` baru di SETIAP panggilan (bukan
reuse) — jauh lebih lambat (reload checkpoint tiap request) tapi
menghilangkan kemungkinan state ke-share antar panggilan sama sekali.
Anggap ini sebagai katup pelarian kalau opsi pertama (lock + reuse
instance) ternyata belum cukup di beban produksi Anda.

## File yang ditambahkan/diubah

| File | Perubahan |
|---|---|
| `model/speech_stt/config.py` | + `DIARIZATION_BACKEND`, `NEMO_DIARIZER_MODEL_NAME`, `NEMO_DIARIZER_CONFIG_PATH`, dsb |
| `model/speech_stt/model_registry.py` | + `get_shared_whisper_only()`, `get_shared_pyannote_only()`, `get_shared_gender_emotion_only()` (pecah dari `get_shared_models()` supaya 4 kombinasi ASR×diarization backend tidak saling boros VRAM), + Factory NeMo (`_load_nemo_diarizer_config`, `get_shared_nemo_diarizer_config`, `get_nemo_diarizer_lock`) |
| `model/speech_stt/diarization_backends/base.py` | **BARU** — interface `DiarizationBackend` (Strategy pattern, sumbu variasi baru: "mesin diarization" terpisah dari "mesin ASR") |
| `model/speech_stt/diarization_backends/pyannote_backend.py` | **BARU** — wrapper logic pyannote yang SUDAH ADA, tanpa perubahan logic |
| `model/speech_stt/diarization_backends/nemo_backend.py` | **BARU** — implementasi NeMo MSDD telephonic |
| `model/speech_stt/engine.py` | `load_model()` di-restrukturisasi jadi 3 fase (ASR → gender/emotion → diarization) supaya 4 kombinasi backend saling kompatibel tanpa duplikasi loading; `perform_diarization()` jadi delegasi tipis ke `self._diarizer.diarize()`. **Jalur default (whisper+pyannote) tetap memanggil `get_shared_models()` yang sama persis seperti sebelumnya — tidak berubah.** |

## Yang BELUM saya lakukan

- **Belum smoke-test** — saya tidak punya GPU/checkpoint NeMo di sandbox
  ini. Alur manifest→diarize()→RTTM saya ambil dari beberapa sumber
  independen (dokumentasi resmi NVIDIA, contoh script, artikel
  perbandingan pyannote-vs-NeMo) yang konsisten satu sama lain, tapi
  tetap WAJIB divalidasi dengan audio nyata sebelum production.
- **Belum tuning hyperparameter NeMo** (VAD threshold, clustering params
  di config yaml) untuk data spesifik Anda — sama seperti pyannote,
  config default kemungkinan besar bisa dioptimalkan lebih lanjut
  dengan data telemarketing Anda sendiri.
- **Sortformer** (diarizer end-to-end generasi baru dari NeMo, tanpa
  clustering) sengaja tidak saya jadikan opsi ketiga — checkpoint publik
  yang saya temukan (`diar_sortformer_4spk-v1`) tidak dilabeli
  telephonic-specific seperti MSDD, jadi belum ada alasan kuat pilih itu
  dibanding MSDD untuk use-case Anda. Bisa dieksplorasi kalau MSDD
  telephonic ternyata masih kurang.
