# Analisis speaker

Setelah transkrip dan label speaker siap, `SpeechSTT.analyze_speakers()` menghitung empat hal per speaker. Fungsi ini dipakai pipeline async dan endpoint ElevenLabs `analysis=true`, sehingga hasil keduanya konsisten.

| Output | Modul | Nilai |
|---|---|---|
| Gender | `gender_detection.py`, `dsp_analysis.py` | `male`, `female`, `unknown` |
| Emosi | `emotion_detection.py` | `POSITIF`, `NEGATIF`, `NETRAL` per speaker dan per giliran |
| Fraud alert | `dsp_analysis.py` | Daftar anomali pitch |
| Talk-time | `formatter.py` | Porsi waktu bicara per speaker |

## Label speaker

Speaker akhir dipetakan ke dua label tetap, `AGENT_SPEAKER_LABEL` (default `Agent`) dan `CUSTOMER_SPEAKER_LABEL` (default `Customer`). Label ini dibaca parser PDF di App B, jadi **jangan diubah** tanpa koordinasi.

## Gender berbasis pitch

1. Audio speaker di-high-pass (`DSP_HIGHPASS_HZ`, default 140 Hz).
2. F0 diestimasi dengan YIN di rentang `DSP_FMIN_HZ`–`DSP_FMAX_HZ` (65–300 Hz).
3. Kalau frame bersuara kurang dari `DSP_MIN_VOICED_FRAMES`, hasilnya `unknown`.
4. Median pitch > `DSP_FEMALE_THRESHOLD_HZ` (185 Hz) → `female`. Di bawah `DSP_MALE_THRESHOLD_HZ` (155 Hz) → `male`. Rentang di antaranya diputuskan classifier gender.

## Emosi

Model audeering wav2vec2 menghasilkan skor arousal, dominance, dan valence. Skor ini diringkas menjadi konklusi telemarketing:

| Valence | Kondisi tambahan | Konklusi |
|---|---|---|
| > 0,60 | arousal dan dominance < 0,40 | `NETRAL` |
| > 0,60 | lainnya | `POSITIF` |
| < 0,40 | arousal dan dominance < 0,40 | `NETRAL` |
| < 0,40 | lainnya | `NEGATIF` |
| 0,40–0,60 | arousal dan dominance > 0,60 | `NEGATIF` |
| 0,40–0,60 | lainnya | `NETRAL` |

Matikan dengan `EMOTION_ENABLED=false` untuk menghemat VRAM.

## Fraud alert (anomali pitch)

Alert menandai kemungkinan **suara berganti orang** di label speaker yang sama, misalnya nasabah diganti orang lain di tengah panggilan.

| Env var | Default | Arti |
|---|---|---|
| `DSP_FRAUD_ENABLED` | `true` | Aktifkan deteksi |
| `DSP_FRAUD_JUMP_HZ` | 80 | Lonjakan median pitch antar segmen yang dianggap anomali |
| `DSP_FRAUD_JUMP_WINDOW_SEC` | 30 | Lonjakan hanya dihitung dalam jendela waktu ini |
| `DSP_FRAUD_STD_HZ` | 80 | Ambang simpangan pitch di dalam satu segmen (adaptif untuk segmen pendek) |
| `DSP_FRAUD_MIN_DUR_SEC` | 1,5 | Segmen lebih pendek diabaikan |
| `DSP_FRAUD_COOLDOWN_SEGS` | 5 | Jarak minimal antar alert untuk speaker yang sama |

::: info Alert adalah sinyal, bukan putusan
Pitch bisa melonjak karena emosi, noise, atau kualitas jalur telepon. Alert dimaksudkan untuk ditinjau auditor.
:::

## Dynamic range compression

Sebelum ASR, audio melewati DRC (`DRC_ENABLED`, target `DRC_TARGET_DB` −20 dB, gain maksimal `DRC_MAX_GAIN_DB`) agar suara pelan terdengar. Audit diarization v2 mencurigai DRC ikut menghapus cue loudness antar speaker. Preprocessing alternatif `prepare_for_scribe()` sedang dievaluasi untuk jalur v2.
