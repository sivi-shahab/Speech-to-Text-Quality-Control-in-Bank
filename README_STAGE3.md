# Refactor Stage 3 — `server.py`

Status: **behavior 100% identik**. `server.py` (~250 baris, 1 file) jadi
5 file: 1 entry point tipis + 4 modul pendukung.

## Pattern yang dipakai

| Pattern | Dipakai di | Alasan |
|---|---|---|
| **Singleton** | `services/stt_singleton.py::get_stt()` | Sudah singleton secara implisit di versi asli (double-checked locking manual), sekarang dipisah jadi modul sendiri dengan nama yang eksplisit menunjukkan pattern-nya. Endpoint diagnostik (`/health`, `/gpu/memory`) butuh baca instance TANPA memicu load model — makanya ada `get_current_instance_or_none()` terpisah dari `get_stt()`. |
| **Config isolation** | `services/server_config.py` | Semua env var & CUDA isolation setup dikumpulkan di 1 tempat, dengan peringatan eksplisit di docstring soal urutan import kritikal (lihat di bawah). |
| **Single Responsibility split** | `routes/diagnostics_routes.py`, `routes/webhook_callback_routes.py` | Endpoint monitoring (`/health`, `/api/v1/queue/status`, `/gpu/memory`) dan endpoint callback (`/speech/webhook/callback`) sebelumnya didefinisikan langsung di `server.py`, campur dengan setup FastAPI app & CORS. Sekarang masing-masing jadi router terpisah. |

## ⚠️ Kendala teknis yang WAJIB dijaga: urutan import CUDA

Ini bagian paling riskan di stage ini. Di versi asli, `CUDA_VISIBLE_DEVICES`
di-set SEBELUM `import torch` di baris pertama file. Setelah refactor
stage 1 & 2, `routes.speech_routes` sekarang **transitif** meng-import
`model.speech_stt` yang meng-import `torch` di level module. Kalau
`services.server_config` (yang men-set env var itu) tidak jadi import
PALING PERTAMA di `server.py`, isolasi GPU antar proses bisa rusak diam-diam
(gejala: proses yang harusnya cuma lihat GPU 1 malah bisa akses semua GPU).

Saya jaga ini dengan:
1. `services/server_config.py` sengaja TIDAK mengimpor apa pun yang berat
   (tidak ada `torch`, `fastapi`, dll) — jadi aman diimpor kapan saja
   tanpa memicu inisialisasi CUDA prematur.
2. `server.py` mengimpor `services.server_config` sebagai baris kode
   PERTAMA, dengan komentar besar yang menjelaskan kenapa, sebelum
   `import torch` dan sebelum `from routes import speech_routes`.
3. Komentar peringatan di docstring `server_config.py` dan di atas blok
   import `server.py` supaya siapa pun yang edit nanti tidak
   mengacak urutan ini tanpa sadar.

## 🐛 Bug laten yang ditemukan (dipertahankan, TIDAK diperbaiki)

Di `services/pipeline.py::STTJobPipeline._get_stt_instance()` (dipindah
dari `speech_routes.py` versi asli), ada fallback:
```python
from server import get_worker_stt
stt = get_worker_stt()
```
Fungsi `get_worker_stt` **tidak pernah didefinisikan** di `server.py`
manapun (asli maupun hasil refactor) — yang ada `get_stt()`. Fallback ini
akan selalu gagal dengan `ImportError`/`AttributeError` (langsung ke-catch
oleh `except Exception`), sehingga secara efektif dead code. Dalam praktik
tidak berdampak karena `app.state.stt_instance` selalu sudah di-set lewat
`lifespan()` sebelum request masuk — tapi kalau suatu saat lifespan gagal
jalan, fallback ini TIDAK akan menyelamatkan apa pun.

Sesuai instruksi "behavior 100% sama", saya **tidak memperbaiki** ini
(mengubahnya jadi `from services.stt_singleton import get_stt` akan
mengubah behavior kalau ada skenario yang bergantung pada kegagalan itu).
Saya hanya melaporkan — silakan tim Anda putuskan mau diperbaiki atau tidak.

## Pemetaan lokasi lama → baru

| Bagian lama (`server.py` v4.0) | Lokasi baru |
|---|---|
| Env var, CUDA isolation, cache paths, `LOGICAL_GPU_INDEX`, CORS parsing | `services/server_config.py` |
| `get_stt()`, `_stt_instance`, `_stt_lock` | `services/stt_singleton.py` |
| `@app.get("/health")` | `routes/diagnostics_routes.py` |
| `@app.get("/api/v1/queue/status")` | `routes/diagnostics_routes.py` |
| `@app.get("/gpu/memory")` | `routes/diagnostics_routes.py` |
| `@app.post("/speech/webhook/callback")` | `routes/webhook_callback_routes.py` |
| `lifespan()`, `app = FastAPI(...)`, CORS middleware, entry point `if __name__` | `server.py` (tetap, jadi jauh lebih pendek) |

## Verifikasi yang sudah dilakukan

- Semua file lolos `py_compile`.
- Cross-reference internal (`from services.X import Y`, `from routes.X import Y`,
  `from model.X import Y`) diverifikasi statis lewat AST di seluruh repo
  (stage 1+2+3 sekaligus) — tidak ada nama hilang/typo.
- Urutan import di `server.py` saya baca ulang manual baris-per-baris untuk
  pastikan `services.server_config` benar-benar jadi import pertama
  sebelum apa pun yang menyentuh `torch`.
- **Belum** dijalankan end-to-end dengan GPU asli — sangat disarankan
  smoke-test `python server.py` di environment dengan GPU sebelum deploy,
  khususnya untuk memverifikasi `CUDA_VISIBLE_DEVICES` benar-benar
  ke-apply sebelum `torch.cuda` dipanggil di modul manapun.

## Status keseluruhan refactor (3 stage)

Semua 4 file asli sudah direfactor dengan behavior dipertahankan 100%:
- `speech_routes.py` → `routes/speech_routes.py` + `services/*` (stage 1)
- `model/speech_stt.py` → `model/speech_stt/*` (stage 2)
- `server.py` → `server.py` (tipis) + `services/server_config.py` +
  `services/stt_singleton.py` + `routes/diagnostics_routes.py` +
  `routes/webhook_callback_routes.py` (stage 3)
- `utils/pdf_builder.py` — tidak disentuh (sudah cukup rapi, Builder
  pattern sudah diterapkan di versi asli).
