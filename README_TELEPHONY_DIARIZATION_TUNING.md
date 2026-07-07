# Optimasi Diarization untuk Telephony (Mono, 1-Channel)

Konfirmasi dari Anda: rekaman telepon sudah **mono/1-channel tercampur
dari sumbernya** — jadi optimasi di sini SEMUA soal tuning pyannote +
alur diarization untuk kondisi itu, BUKAN pemisahan channel.

Semua perubahan **opt-in / punya fallback aman**: kalau env var tidak
di-set, atau API pyannote versi Anda beda dan tuning gagal di-apply,
sistem otomatis jalan dengan hyperparameter default pyannote murni
(behavior lama). Tidak ada kondisi di mana perubahan ini bisa membuat
loading model gagal total.

## Diagnosis akar masalah

`pyannote/speaker-diarization-3.1` dilatih mayoritas pada data
meeting/broadcast (AMI, VoxConverse, DIHARD) — bukan telepon narrowband.
Dua konsekuensi nyata untuk telemarketing:

1. **`clustering.min_cluster_size` default (~15) terlalu besar** untuk
   customer yang sering cuma menjawab singkat berkali-kali ("iya",
   "boleh"). Kalau total durasi/jumlah embedding bicara customer di
   bawah threshold ini, pyannote **menyerap semua turn customer ke
   cluster agent**. Ini persis pola yang sudah divalidasi kode Anda
   sendiri di `quality_checks.py::validate_diarization_output()` —
   warning `"speaker_imbalance_detected"` saat `agent_pct > 85%`.
2. **`segmentation.min_duration_off` default terlalu longgar** untuk
   turn-taking cepat khas telepon (konfirmasi cepat, interupsi) —
   dioptimalkan untuk jeda gaya meeting yang lebih panjang.
3. **Threshold skip-diarization 60 detik terlalu tinggi** — audio di
   bawah itu otomatis di-skip diarization (semua jadi 1 speaker: AGENT_BM).
   Karena `num_speakers=2` sudah di-pass sebagai oracle hint sejak awal,
   pyannote sebenarnya cukup andal bahkan untuk klip lebih pendek.

Referensi teknis (bukan tebakan): [GitHub pyannote/pyannote-audio#1579](https://github.com/pyannote/pyannote-audio/issues/1579)
mengonfirmasi hyperparameter yang valid untuk pipeline 3.1
(`segmentation.min_duration_off`, `clustering.method`,
`clustering.min_cluster_size`, `clustering.threshold`), dan
[blog Hervé Bredin](https://herve.niderb.fr/posts/2022-12-02-how-I-won-2022-diarization-challenges.html)
(pembuat pyannote) menjelaskan cara adaptasi hyperparameter ke domain sendiri.

## Env var baru

| Env var | Default (telephony tuning aktif) | Efek |
|---|---|---|
| `PYANNOTE_ENABLE_TELEPHONY_TUNING` | `true` | Matikan (`false`) untuk kembali 100% ke default pyannote murni |
| `PYANNOTE_MIN_CLUSTER_SIZE` | `6` | Cluster minimal lebih kecil → speaker dengan sedikit turn bicara tidak diserap ke speaker lain |
| `PYANNOTE_SEGMENTATION_MIN_DURATION_OFF` | `0.1` | Jeda minimal antar turn lebih pendek → cocok turn-taking cepat telepon |
| `PYANNOTE_CLUSTERING_THRESHOLD` | *(kosong = pakai default pyannote)* | **Sengaja tidak diberi default** — parameter paling sensitif-data, isi HANYA setelah tuning dengan data Anda sendiri |
| `DIARIZATION_MIN_AUDIO_DURATION_SEC` | `15.0` (sebelumnya hardcode `60.0`) | Audio di bawah durasi ini di-skip diarization sama sekali |

## ⚠️ Ini titik awal, BUKAN hasil tuning final

Nilai default di atas saya pilih berdasarkan karakteristik UMUM panggilan
telemarketing (speaker sangat asimetris, turn-taking cepat) dan panduan
resmi pyannote — **bukan hasil optimasi terhadap data Anda**. Saya tidak
punya akses ke rekaman/GPU untuk validasi langsung.

**Cara tuning yang benar** (ikuti pendekatan Hervé Bredin di atas):
1. Kumpulkan 10-20 panggilan yang sudah Anda beri label manual (siapa
   bicara kapan, ground truth).
2. Hitung **DER (Diarization Error Rate)** dengan beberapa kombinasi nilai
   `PYANNOTE_MIN_CLUSTER_SIZE` / `PYANNOTE_SEGMENTATION_MIN_DURATION_OFF` /
   `PYANNOTE_CLUSTERING_THRESHOLD` (pakai `pyannote.metrics.diarization.DiarizationErrorRate`).
3. Pakai kombinasi dengan DER terendah sebagai default produksi Anda.

Karena semua parameter ini sekarang env-var driven, iterasi tuning bisa
dilakukan **tanpa redeploy kode** — tinggal ubah `.env` dan restart.

## Kalau setelah tuning masih kurang memuaskan

Optimasi lanjutan yang BELUM saya implementasikan (di luar scope
perubahan config murni, butuh keputusan/effort lebih besar):

- **Fine-tune segmentation model pyannote sendiri** pakai data
  telemarketing berlabel Anda (lihat contoh di paper Bangla-WhisperDiar/
  Bangla speech processing yang saya temukan saat riset — mereka
  fine-tune HANYA komponen segmentation, embedding+clustering tetap
  pretrained, dan itu terbukti mengalahkan pipeline modular buatan
  sendiri). Ini butuh dataset berlabel + GPU training terpisah, bukan
  sekadar env var.
- **Model diarization alternatif yang secara spesifik dilatih untuk
  telephony** (mis. NeMo diarization yang dilatih di CallHome/Fisher/
  Switchboard — corpus telepon asli, bukan meeting). Ini perubahan
  arsitektur lebih besar (ganti backend diarization, mirip pola
  `asr_backends/` yang sudah ada untuk ASR — bisa dibuatkan
  `diarization_backends/` serupa kalau Anda mau explore ini).

## File yang diubah

| File | Perubahan |
|---|---|
| `model/speech_stt/config.py` | + `PYANNOTE_ENABLE_TELEPHONY_TUNING`, `PYANNOTE_MIN_CLUSTER_SIZE`, `PYANNOTE_SEGMENTATION_MIN_DURATION_OFF`, `PYANNOTE_CLUSTERING_THRESHOLD`, `DIARIZATION_MIN_AUDIO_DURATION_SEC` |
| `model/speech_stt/model_registry.py::_load_pyannote()` | Terapkan hyperparameter di atas via `pipeline.instantiate()`, dibungkus try/except terpisah (fail-safe — gagal tuning tidak menggagalkan load pipeline) |
| `model/speech_stt/engine.py::perform_diarization()` | Threshold skip-diarization dari hardcode `60.0` jadi `stt_config.DIARIZATION_MIN_AUDIO_DURATION_SEC` |
