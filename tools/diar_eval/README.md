# tools/diar_eval — evaluasi offline diarization v2

Tool ini **tidak pernah memanggil API ElevenLabs**. Satu-satunya akses eksternal adalah membaca DB (sesi read-only) di `cache.py`. Jalankan dari root repo dengan venv dev (lihat `requirements-dev.txt`).

## 1. Cache (berisi PII)

Cache memuat teks transkrip, sehingga:
- **wajib di luar repo** (tool menolak path di dalam repo),
- direktori dibuat dengan izin `0700` dan file `0600`,
- **wajib di-purge** paling lambat 30 hari (`manifest.json` → `purge_before`).

```bash
export DIAR_EVAL_CACHE_DIR=/data/speech_to_text_v4/diar_eval_cache
python -m tools.diar_eval.cache export --env-file .env --campaign Cashline
python -m tools.diar_eval.cache info
python -m tools.diar_eval.cache purge        # setelah evaluasi selesai
```

## 2. Metrik proxy (tanpa ground truth)

```bash
python -m tools.diar_eval.proxy_metrics [--labels stems.json] [--per-call /luar/repo/calls.csv]
```

Isi laporan:
- **legacy vs `cue_appb`**: rasio role tertukar jalur lama menurut algoritme `fix_speaker_roles` App B. Ini referensi independen untuk jalur lama.
- **v2 vs `cue_appb`**: ditandai **SIRKULAR**, karena v2 memakai cue yang sama. Angka ini bukan bukti akurasi.
- **vs `dominant`**: referensi independen dari cue.
- **`cue_undecided`**: call tanpa cue atau dengan cue seri. Di sini tidak ada referensi yang bisa dipakai, dan perbedaan v2 dengan jalur lama di sini hanya bisa dinilai dengan ground truth.

## 3. Ground truth & grid search

Format GT dijelaskan di `metrics.py`, satu JSON per call. Data GT berisi PII dan **tidak boleh masuk repo**.

```bash
python -m tools.diar_eval.grid --gt-dir /luar/repo/gt
```

Metrik:
- DER/JER dihitung dengan `pyannote.metrics` (collar 0,25 s, skip overlap). DER memakai pemetaan label optimal, sehingga **tidak** menangkap role yang tertukar.
- Role yang tertukar diukur oleh `role_accuracy` (berbobot waktu) dan `item_role_accuracy` (per item checklist).
