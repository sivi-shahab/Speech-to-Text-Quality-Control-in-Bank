# Evaluasi diarization

`tools/diar_eval/` adalah harness offline untuk mengukur kualitas diarization v2. Tool ini **tidak pernah memanggil API ElevenLabs**. Raw response Scribe yang sudah tersimpan di DB dipakai sebagai cache gratis, dan satu-satunya akses eksternal adalah membaca DB lewat sesi read-only.

Jalankan dari root repo dengan venv dev:

```bash
pip install -r requirements-dev.txt
```

## 1. Cache (berisi PII)

```bash
export DIAR_EVAL_CACHE_DIR=/data/speech_to_text_v4/diar_eval_cache   # di LUAR repo
python -m tools.diar_eval.cache export --env-file .env --campaign Cashline
python -m tools.diar_eval.cache info
python -m tools.diar_eval.cache purge
```

::: danger Aturan cache
- Path di dalam repo **ditolak** oleh tool.
- Direktori dibuat dengan izin `0700` dan file `0600`.
- Wajib di-purge paling lambat 30 hari (`manifest.json` → `purge_before`).
- `.gitignore` sudah mengecualikan `diar_eval_cache/`, `tools/diar_eval/cache/`, dan `*.rttm`.
:::

## 2. Metrik proxy (tanpa ground truth)

```bash
python -m tools.diar_eval.proxy_metrics [--labels stems.json] [--per-call /luar/repo/calls.csv]
```

| Perbandingan | Arti |
|---|---|
| legacy vs `cue_appb` | Rasio role tertukar di jalur lama menurut algoritme cue App B. Referensi independen. |
| v2 vs `cue_appb` | Ditandai **SIRKULAR** karena v2 memakai cue yang sama. Bukan bukti akurasi. |
| vs `dominant` | Referensi independen dari cue |
| `cue_undecided` | Call tanpa cue atau cue seri. Hanya bisa dinilai dengan ground truth. |

## 3. Ground truth & grid search

Format GT dijelaskan di `tools/diar_eval/metrics.py`, satu JSON per call. Panduan pelabelan ada di `docs/diarization-gt-guideline.md`. Data GT berisi PII dan **tidak boleh masuk repo**.

```bash
python -m tools.diar_eval.grid --gt-dir /luar/repo/gt
```

| Metrik | Catatan |
|---|---|
| DER / JER | Dihitung dengan `pyannote.metrics` (collar 0,25 detik, overlap dilewati). DER memakai pemetaan label optimal, sehingga **tidak** menangkap role tertukar. |
| `role_accuracy` | Akurasi role berbobot waktu |
| `item_role_accuracy` | Akurasi role per item checklist QC |

## Test

```bash
pytest tests/test_diar_eval_metrics.py tests/test_gt_io.py
```
