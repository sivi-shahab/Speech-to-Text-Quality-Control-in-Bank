# Backend diarization

Saat ASR tidak membawa diarization sendiri (Whisper), mesin diarization dipilih dengan `DIARIZATION_BACKEND`. Interface-nya `DiarizationBackend` di `model/speech_stt/diarization_backends/base.py`.

| `ASR_BACKEND` | `DIARIZATION_BACKEND` | Yang dimuat ke VRAM |
|---|---|---|
| `whisper` | `pyannote` (default) | Whisper + pyannote + gender + emosi |
| `whisper` | `nemo` | Whisper + NeMo (VAD + TitaNet + MSDD) + gender + emosi |
| `elevenlabs` | diabaikan | Gender + emosi saja |

## pyannote 3.1 dengan tuning telephony

`pyannote/speaker-diarization-3.1` dilatih terutama dengan data meeting dan broadcast. Di panggilan telemarketing mono, dua masalah sering muncul:

1. `clustering.min_cluster_size` default (~15) terlalu besar untuk nasabah yang hanya menjawab singkat, sehingga semua giliran nasabah terserap ke cluster agent.
2. `segmentation.min_duration_off` default terlalu longgar untuk turn-taking cepat di telepon.

Tuning aktif secara default dan **fail-safe**. Kalau parameter gagal di-apply, pipeline tetap jalan dengan default pyannote.

| Env var | Default | Efek |
|---|---|---|
| `PYANNOTE_ENABLE_TELEPHONY_TUNING` | `true` | `false` = kembali ke default pyannote murni |
| `PYANNOTE_MIN_CLUSTER_SIZE` | `6` | Speaker dengan sedikit giliran tidak diserap |
| `PYANNOTE_SEGMENTATION_MIN_DURATION_OFF` | `0.1` | Jeda antar giliran lebih pendek |
| `PYANNOTE_CLUSTERING_THRESHOLD` | kosong | Sengaja tanpa default. Isi hanya setelah tuning dengan data sendiri. |
| `DIARIZATION_MIN_AUDIO_DURATION_SEC` | `15.0` | Audio lebih pendek dari ini dilewati diarization (semua jadi Agent) |

::: info Ini titik awal, bukan hasil tuning final
Ukur DER dengan 10–20 panggilan berlabel lalu pilih kombinasi terbaik. Lihat [Evaluasi diarization](/features/diar-eval). Semua parameter berupa env var, jadi iterasi cukup ubah `.env` lalu restart.
:::

## NeMo MSDD telephonic

`diar_msdd_telephonic` dilatih khusus dengan ~1500 jam percakapan telepon (Fisher Corpus).

```bash
DIARIZATION_BACKEND=nemo
NEMO_DIARIZER_CONFIG_PATH=/path/ke/diar_infer_telephonic.yaml   # wajib
```

Ambil config resmi dari repo NVIDIA NeMo (`examples/speaker_tasks/diarization/conf/inference/diar_infer_telephonic.yaml`). Kalau path tidak di-set atau file tidak ada, `load_model()` gagal dengan pesan yang menjelaskan langkahnya.

| Aspek | pyannote | NeMo MSDD |
|---|---|---|
| Cara panggil | In-memory | Berbasis file: manifest JSON → `diarize()` → RTTM |
| Kecepatan | Cepat | Lebih lambat (I/O per panggilan) |
| Konkurensi | Semaphore(1) | **Lock per GPU wajib** (race condition yang dilaporkan di NeMo issue #12254) |
| Domain training | Meeting/broadcast | Telepon |

Kalau error `Mismatch of counts between embedding vectors and timestamps` tetap muncul walaupun sudah memakai lock, set `NEMO_DIARIZER_FRESH_INSTANCE_PER_CALL=true`. Instance baru dibuat di setiap panggilan, jauh lebih lambat tetapi tidak ada state yang ter-share.

::: warning Dependency besar
`nemo_toolkit[asr]` membawa banyak dependency transitif yang bisa bentrok dengan `transformers`, `faster-whisper`, atau `pyannote.audio`. Uji di staging dulu, atau jalankan worker NeMo di container terpisah.
:::

## Setelah diarization (jalur Whisper)

```text
align_pure_overlap → safe_single_word_correction → pitch_verify_speakers → merge_same_speaker_simple
```

Strategi lama `LegacyHeuristicAlignmentStrategy` masih ada di `alignment/legacy_alignment.py` tetapi **tidak dipanggil** oleh pipeline aktif.
