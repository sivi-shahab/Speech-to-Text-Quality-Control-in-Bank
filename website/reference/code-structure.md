# Struktur kode & pattern

```text
.
├── server.py                     Entry point FastAPI (tipis)
├── lightweight_server.py         Proses kedua untuk endpoint data (tanpa torch)
├── routes/                       HTTP layer
│   ├── speech_routes.py          /speech/stt/save, /save_dashboard, status job
│   ├── elevenlabs_stt_routes.py  /speech/stt/elevenlabs (sinkron)
│   ├── diagnostics_routes.py     /health, /api/v1/queue/status, /gpu/memory
│   ├── webhook_callback_routes.py
│   ├── campaign_routes.py        (dipasang di lightweight_server)
│   └── recording_routes.py       (dipasang di lightweight_server)
├── services/                     Orchestration, tidak tahu detail model
│   ├── server_config.py          Env + isolasi CUDA (WAJIB import pertama)
│   ├── stt_singleton.py          get_stt() per proses
│   ├── pipeline.py               Template Method: Legacy vs New dashboard
│   ├── backend_forwarder.py      Strategy: Register vs UploadTranscript
│   ├── job_storage.py, executor.py, webhook_service.py, http_retry.py
│   ├── audio_upload.py, stt_audio_prep.py, tmp_cleanup.py
│   └── db.py, redis_cache.py, minio_cache.py
├── model/speech_stt/             Engine STT
│   ├── engine.py                 Facade: SpeechSTT
│   ├── model_registry.py         Factory: load & share model per GPU
│   ├── asr_backends/             Strategy ASR: whisper, elevenlabs
│   ├── diarization_backends/     Strategy diarization: pyannote, nemo
│   ├── diarization_v2/           Post-processing Scribe v2 (feature flag)
│   ├── alignment/                Strategy alignment (pure aktif, legacy tidak)
│   ├── audio_conversion.py, audio_slicing.py, dsp_analysis.py
│   ├── gender_detection.py, emotion_detection.py
│   ├── formatter.py, quality_checks.py, segment.py, tempfiles.py
│   └── config.py, lexical_rules.py
├── utils/                        vars.py, pdf_builder.py (Builder)
├── tools/diar_eval/              Harness evaluasi offline
├── tests/                        pytest (golden test jalur lama + v2)
└── docs/                         Audit, rencana implementasi, guideline GT
```

## Pattern yang dipakai

| Pattern | Lokasi | Masalah yang diselesaikan |
|---|---|---|
| **Template Method** | `services/pipeline.py` | Dua pipeline dashboard dulu 80% identik hasil copy-paste. `run()` mengunci urutan langkah, subclass hanya meng-override `deliver_result()`. |
| **Strategy** | `backend_forwarder.py`, `asr_backends/`, `diarization_backends/`, `alignment/` | Menambah tujuan forward atau mesin baru cukup dengan satu class baru |
| **Facade** | `engine.py::SpeechSTT` | Dulu God Object ~1700 baris. Sekarang hanya orkestrator, dengan API publik yang tetap sama. |
| **Factory** | `model_registry.py` | Detail file-locking, validasi cache, dan device placement tersembunyi dari caller |
| **Singleton** | `stt_singleton.py` | Satu instance engine per proses. `get_current_instance_or_none()` untuk diagnostik tanpa memicu load. |
| **Builder** | `utils/pdf_builder.py` | Penyusunan PDF transkrip |

## Catatan pemeliharaan

- **Dead code**: `alignment/legacy_alignment.py` (~500 baris) tidak dipanggil pipeline aktif. Kandidat untuk dihapus setelah dikonfirmasi tim.
- **Bug laten**: fallback `from server import get_worker_stt` di `pipeline.py::_get_stt_instance()` selalu gagal karena fungsi itu tidak ada. Dampaknya nol selama `lifespan()` berhasil men-set `app.state.stt_instance`.
- File bersufiks tanggal (mis. `pipeline_16072026.py`, `campaign_routes_23072026.py`) adalah salinan lama dan tidak diimpor.

Riwayat refactor lengkap ada di `README_STAGE1.md`, `README_STAGE2.md`, dan `README_STAGE3.md` di root repo.
