# Refactor Stage 2 — `model/speech_stt.py`

Status: **behavior 100% identik**. Satu file ~1700 baris jadi package
`model/speech_stt/` berisi 15 modul kecil. Semua cross-reference internal
sudah diverifikasi valid secara statis (lihat catatan di akhir).

## Pattern yang dipakai

| Pattern | Dipakai di | Alasan |
|---|---|---|
| **Facade** | `engine.py::SpeechSTT` | Class ini dulu God Object — transcribe, diarization, gender, emotion, fraud, formatting semua jadi 1 class ~1700 baris. Sekarang `SpeechSTT` cuma mengatur URUTAN pemanggilan ke modul-modul di bawahnya; public API (constructor + method publik) persis sama supaya `services/pipeline.py` tidak perlu diubah. |
| **Strategy** | `alignment/base.py`, `pure_alignment.py`, `legacy_alignment.py` | Ada 2 algoritma alignment speaker yang saling lepas: `PureOverlapAlignmentStrategy` (yang AKTIF dipakai) dan `LegacyHeuristicAlignmentStrategy` (kode lama, ternyata **tidak dipanggil** di pipeline aktif — lihat temuan di bawah). Keduanya implement interface `AlignmentStrategy.align()` yang sama. |
| **Factory** | `model_registry.py` | Loading Whisper/pyannote/gender/emotion classifier — masing-masing lewat `_load_xxx()`, disatukan lewat `get_shared_models()`. Caller tidak perlu tahu detail file-locking, cache validation, device placement. |
| **Single Responsibility split** | `config.py`, `lexical_rules.py`, `audio_conversion.py`, `dsp_analysis.py`, `segment.py`, `audio_slicing.py`, `gender_detection.py`, `emotion_detection.py`, `formatter.py`, `quality_checks.py`, `tempfiles.py` | Tiap modul satu urusan: konstanta, kata-kata heuristik, konversi audio, analisis pitch, dst. |

## ⚠️ Temuan penting: dead code

Berdasarkan pembacaan `transcribe_with_diarization()` di kode asli, pipeline
yang benar-benar jalan HANYA:

```
align_pure_overlap → safe_single_word_correction → pitch_verify_speakers → merge_same_speaker_simple
```

Sedangkan fungsi-fungsi legacy yang jauh lebih besar dan kompleks —
`align_speakers_with_transcription`, `_merge_consecutive_segments`,
`_lexical_reattribute`, `_split_embedded_customer_responses`,
`_anchor_agent_by_opener`, `_filter_rapid_switches`, `_detect_generic_qa_splits`,
`_detect_reading_blocks`, `_strip_proper_nouns` — **tidak dipanggil dari
mana pun** di kode yang direview. Komentar di source asli sendiri
menyebutnya sudah digantikan ("v7.0 PURE MODE... heuristik kompleks DIHAPUS
karena terbukti saling bertabrakan").

Karena instruksi "behavior 100% sama", semua fungsi itu **tetap saya
pindahkan apa adanya** ke `alignment/legacy_alignment.py` (class
`LegacyHeuristicAlignmentStrategy`) — tidak dihapus, jaga-jaga kalau
dipanggil dari file lain yang tidak saya lihat. Tapi karena tidak
digunakan pipeline aktif, ini kandidat kuat untuk dihapus di masa depan
kalau tim Anda konfirmasi memang tidak dipakai — akan menghemat ~500
baris kode yang harus dipelihara.

## Pemetaan lokasi lama → baru

| Fungsi/kelas lama | Lokasi baru |
|---|---|
| Konstanta cache dir, DSP/DRC threshold, VAD params, hallucination patterns | `config.py` |
| `FORBIDDEN_CUSTOMER_WORDS`, `AGENT_OPENER_PHRASES`, `QA_INLINE_PATTERNS`, dst | `lexical_rules.py` |
| `_load_whisper_shared`, `_load_pyannote`, `get_shared_models`, dict `_SHARED_*` | `model_registry.py` |
| `_classify_phase_issue`, `_convert_to_wav_ffmpeg`, `convert_audio_to_wav`, `apply_dynamic_range_compression` | `audio_conversion.py` |
| `analyze_gender_and_fraud_dsp`, `_compute_median_pitch` | `dsp_analysis.py` |
| `SpeakerSegment`, `_map_speakers_to_binary` | `segment.py` |
| `_extract_speaker_audio`, `_extract_single_segment_audio` | `audio_slicing.py` |
| `_detect_gender_for_speaker`, `_run_gender_detection`, `_build_speaker_label` | `gender_detection.py` |
| `_get_telemarketing_conclusion`, `_detect_emotion_for_speaker` | `emotion_detection.py` |
| `format_output_with_speakers`, `_compute_talk_time`, `_format_time` | `formatter.py` |
| `_is_hallucination`, `_validate_diarization_output` | `quality_checks.py` |
| `_align_pure_overlap`, `_safe_single_word_correction`, `_merge_same_speaker_simple` | `alignment/pure_alignment.py` (**AKTIF**) |
| `_pitch_verify_speakers` | `alignment/pitch_verification.py` (**AKTIF**) |
| `align_speakers_with_transcription`, `_lexical_reattribute`, dkk (lihat di atas) | `alignment/legacy_alignment.py` (**TIDAK AKTIF**) |
| Class `SpeechSTT` itu sendiri (constructor, `load_model`, `transcribe_*`, dst) | `engine.py` |

## Cara pasang

`server.py` dan `services/*` melakukan:
```python
from model.speech_stt import SpeechSTT, convert_audio_to_wav
from model.speech_stt import _SHARED_MODELS, _SHARED_MODELS_LOCK, _SHARED_PYANNOTE
```
Ini **tetap jalan tanpa perubahan** karena `model/speech_stt/__init__.py`
me-re-export semua nama itu. Tinggal:

1. Hapus file lama `model/speech_stt.py`.
2. Copy folder `model/speech_stt/` (package baru) ke tempatnya.
3. Tidak ada perubahan env var, model checkpoint, atau signature publik.

## Verifikasi yang sudah dilakukan

- Seluruh 20 file lolos `py_compile` (syntax valid).
- Cross-reference internal (`from model.speech_stt.X import Y`) diverifikasi
  statis lewat AST — semua `Y` benar-benar didefinisikan di modul `X`
  yang dituju, tidak ada typo/nama hilang.
- **Belum** dijalankan end-to-end (butuh GPU + model checkpoint + audio
  sample asli) — sangat disarankan jalankan smoke test dengan 1 file
  audio nyata sebelum deploy ke production.

## Yang belum disentuh

- `server.py` — masih pakai `model/speech_stt.py` lama secara referensi
  konseptual; karena `__init__.py` backward-compatible, `server.py` bisa
  tetap tidak diubah, TAPI ada baiknya sekalian dirapikan (worker
  singleton `get_stt()` sudah cukup pendek, prioritas rendah).
- `utils/pdf_builder.py` — tidak disentuh (sudah cukup rapi).
