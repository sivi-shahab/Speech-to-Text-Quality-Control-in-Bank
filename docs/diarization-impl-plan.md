# Rencana Implementasi — Diarization v2 dengan ElevenLabs Scribe (Cashline, mono)

- Branch: `feat/diarization-scribe-audit` (deploy menunggu keputusan human)
- Dasar: `docs/diarization-audit.md` (Checkpoint 2 approved, 2026-10-28)
- Pertanyaan terbuka: `docs/questions.md`. Di bawah, kodenya dirujuk sebagai Bx/Cx/Dx/Ex/Fx.

---

## 0. Prinsip

1. **Jalur lama tidak berubah saat flag off.** Semua kode baru ditempatkan di paket baru `model/speech_stt/diarization_v2/`. `engine.py` hanya mendapat satu cabang `if` tambahan. Cabang `else` tetap kode yang sekarang, byte-per-byte.
2. **Urutan mengikuti data, bukan urutan daftar opsi.** Data Cashline menunjukkan role tertukar pada 14–16% call, dan ini masalah terbesar. Masalah ini bisa diperbaiki dan dievaluasi **tanpa memanggil API**, karena 34.533 raw response Scribe sudah ada di DB. Tuning parameter API dan audio dikerjakan belakangan, karena butuh ground truth dan biaya API.
3. **Shadow mode sebelum ganti output.** Post-processing v2 bisa dihitung dari raw response yang sama dengan jalur lama. Dengan begitu v2 bisa berjalan paralel di produksi **tanpa biaya API tambahan** dan tanpa mengubah PDF.
4. **Format PDF ke App B tidak berubah.** Label tetap `Agent` dan `Customer` (`config.py:92-93`), sehingga `pdf_parser.py` di App B tidak perlu diubah.
5. **Tanpa teks transkrip di log.** Yang di-log hanya metrik numerik.

---

## 1. Evaluasi opsi terhadap kode yang ada

| # | Opsi | Kesesuaian dengan kode & data | Keputusan |
|---|---|---|---|
| 1a | Scribe `num_speakers=2` (sekarang) | Sudah berjalan. 98,7% call menghasilkan tepat 2 cluster. Kelemahannya, threshold terkunci di default. | **Baseline A/B (mode A)** |
| 1b | `num_speakers=None` + `diarization_threshold` 0,15–0,30, lalu collapse ke 2 speaker | Satu-satunya cara men-tuning threshold (SDK `client.py:63`). Kandidat untuk mengatasi salah atribusi per potongan (S7). Mode ini butuh collapse karena cluster bisa >2. | **Mode B di A/B**, dievaluasi dengan GT |
| 1c | `language_code="ind"`, `timestamps_granularity="word"` | `"id"` sekarang setara dengan `"ind"` (SDK menerima ISO-639-1 dan -3). `word` adalah default. Keduanya dibuat eksplisit supaya tidak bergantung pada nilai dari client (C1). | Eksplisit di provider v2 |
| 1d | `keyterms` dari YAML | Sekarang ada 7 term di `.env:105`. Dipindah ke YAML per campaign, dengan validasi batas SDK (≤1000 term, <50 karakter, ≤5 kata, tanpa `<>{}[]\`). | Fase 5 (E4) |
| 1e | `detect_speaker_roles=True` (tambahan, ada di SDK 2.56.0) | Role native dari Scribe (+10%). Belum ada di rencana awal. Tidak bisa digabung dengan multi-channel. | **Mode C opsional di A/B** (E3) |
| 2 | Preprocessing: mono, 16 kHz, band 100–3800 Hz, `loudnorm`, tanpa denoise | Sekarang: `pan` (−6 dB pada mono), lalu DRC +40 dB (`audio_conversion.py:133,167-206`). DRC dicurigai menghapus cue loudness. Fungsi baru `prepare_for_scribe()` hanya dipakai di jalur v2. | Fase 5, A/B {DRC lama, prep v2} |
| 3a | Collapse cluster minor | Data: cluster ke-3 muncul pada 0,8% call, dengan porsi median 0,5% kata. Diperlukan sebagai guard, dan **wajib** untuk mode 1b. | Fase 2 |
| 3b | Smoothing flip 1 kata tanpa jeda | Data: median 0,03 per 100 kata. Backchannel ber-jeda (0,54 per 100 kata) dipertahankan. Murah, tapi dampaknya kecil. | Fase 2 |
| 3c | Role assignment berbasis cue + `role_confident` | Dampak terbesar. Cue App B (`call_ownership.py:249-282`) ditambah prior "speaker dominan" (cocok ~96% dengan cue). Menggantikan aturan "bicara-pertama" (`segment.py:46-47`). | **Fase 2 (prioritas 1)** |
| 4 | Fallback LLM on-prem untuk `role_confident=False` | Kandidat ~7% call (cue `none` atau `tie`). Output dibatasi ke ID utterance yang di-flip, plus swap global. | Fase 6 (E5) |
| 5 | `DiarizationProvider` + feature flag default off | Sekarang diarization Scribe terikat ke `ASRBackend.provides_own_diarization`. Provider baru membungkus panggilan Scribe tanpa mengubah `ElevenLabsBackend`. | Fase 1 |
| 6 | Evaluasi: GT 30–50 call, DER (`pyannote.metrics`), role accuracy per item, grid search, cache | `pyannote.metrics` 4.0.0 sudah terpasang di venv runtime. Raw response di DB berfungsi sebagai cache gratis untuk post-processing. | Fase 3–4 |
| 7 | Rekaman dual-channel (`use_multi_channel=true`) | Kalau PBX bisa mengekspor stereo (B5), role cukup ditentukan dari indeks channel, dan sebagian besar masalah hilang secara struktural. Biaya: setiap channel ditagih penuh (2×). Perlu jalur konversi baru, karena `audio_conversion.py` sekarang memaksa `-ac 1`. | **Investigasi paralel**, ditulis sebagai Fase 9 bersyarat |
| 8 | Kepatuhan | Legal OK (A3). `enable_logging=False` bergantung pada status enterprise (E1). Cache lokal berisi teks, jadi harus disimpan di direktori ber-izin terbatas dan masuk gitignore. Opsi hybrid on-prem: pyannote 3.3.2 sudah ada di kode (`diarization_backends/pyannote_backend.py`). | Fase 1, 3, 7, dan §5 |

---

## 2. Fase implementasi

Effort: **S** ≤1 hari, **M** 2–4 hari, **L** ≥1 minggu (termasuk menunggu pihak lain). Setiap fase berakhir dengan test, lint, dan commit kecil, lalu **STOP** untuk approval.

### Fase 0: Fondasi test & tooling (tanpa perubahan perilaku)
- **Tujuan:** App A belum punya test. Fase ini membuat jaring pengaman sebelum ada perubahan apa pun.
- **File baru:**
  - `requirements-dev.txt` (pytest, ruff, dipasang di venv dev terpisah, **bukan** di venv produksi `/data/speech_to_text/.venv`)
  - `pytest.ini`
  - `ruff.toml` (lint hanya untuk `model/speech_stt/diarization_v2/`, `tests/`, `tools/`, supaya kode lama tidak ikut diformat ulang)
  - `tests/__init__.py`
  - `tests/fixtures/scribe/*.json` (response Scribe **sintetis**, tanpa PII)
  - `tests/test_legacy_scribe_path.py`
- **Perubahan fungsi:** tidak ada.
- **Test:** *characterization tests* untuk perilaku sekarang, yaitu `map_speakers_to_binary` (aturan bicara-pertama), `ElevenLabsBackend._extract_segments_with_speaker` (pengelompokan dari words), `merge_same_speaker_simple`, dan `format_output_with_speakers`. Test ini menjadi **golden test** untuk menjamin jalur lama tidak berubah.
- **Migration:** tidak.
- **Effort:** S
- **Risiko:** import `engine.py` menarik torch/whisper. Mitigasi: test hanya meng-import modul ringan (`segment.py`, `elevenlabs_backend.py`, `formatter.py`).
- **Rollback:** hapus file. Runtime tidak tersentuh.

### Fase 1: Abstraksi provider + feature flag
- **Tujuan:** menambah jalur v2 di belakang flag (default off) tanpa mengubah jalur lama.
- **File baru:**
  - `model/speech_stt/diarization_v2/__init__.py`
  - `model/speech_stt/diarization_v2/types.py`: dataclass `Word(text, start, end, speaker)`, `Utterance(id, start, end, speaker, text, words)`, dan `DiarizationResult(words, utterances, roles, role_confident, meta, raw_response)`
  - `model/speech_stt/diarization_v2/provider.py`:
    - `class DiarizationProvider(Protocol)` dengan method `diarize(audio_path, language, *, campaign=None) -> DiarizationResult`
    - `class ScribeDiarizationProvider`, memakai client dari `ElevenLabsBackend` (semaphore dan retry 429 dipakai ulang), dengan kwargs **eksplisit**: `language_code`, `timestamps_granularity="word"`, mode A/B/C, `enable_logging`, dan `request_options={"timeout_in_seconds": ...}` (memperbaiki A4 hanya di v2)
    - **Tanpa** fallback `TypeError` yang diam-diam membuang parameter (A5): parameter yang tidak dikenal langsung error.
    - `class CachedRawResponseProvider`, yang membangun `DiarizationResult` dari raw response yang sudah ada (dipakai untuk shadow mode dan evaluasi)
  - `model/speech_stt/diarization_v2/pipeline.py`: `run_v2(raw_or_audio, ...) -> (List[SpeakerSegment], meta)`. Untuk sementara isinya grouping setara jalur lama (diisi penuh di Fase 2).
- **File diubah:**
  - `model/speech_stt/config.py`: flag baru (nama final menunggu keputusan K1):
    - `DIARIZATION_V2_MODE = off | shadow | on` (default `off`)
    - `SCRIBE_DIAR_MODE = fixed2 | threshold | roles` (default `fixed2`)
    - `SCRIBE_DIAR_THRESHOLD` (default kosong)
    - `SCRIBE_ENABLE_LOGGING` (default kosong, artinya parameter tidak dikirim)
    - `SCRIBE_KEYTERMS_FILE`
  - `model/speech_stt/engine.py` (cabang `provides_own_diarization`, `:303-329`): satu `if` di awal cabang. `off` menjalankan kode sekarang tanpa perubahan. `shadow` menjalankan kode sekarang, ditambah `run_v2()` dari `last_raw_response` yang sama, dan hasilnya hanya masuk `metadata["diarization_v2"]`. `on` memakai hasil v2 untuk `aligned`.
- **Migration:** tidak.
- **Test:**
  - `tests/test_v2_flag_off_identical.py`: golden test, output identik dengan Fase 0 saat `off`
  - `tests/test_scribe_provider_kwargs.py`: SDK di-mock. Kwargs per mode sesuai. `diarization_threshold` hanya dikirim saat `num_speakers=None`. Parameter tidak valid menghasilkan error.
- **Effort:** M
- **Risiko:** cabang `if` salah tempat sehingga jalur lama berubah. Mitigasi: golden test, dan default `off`.
- **Rollback:** `DIARIZATION_V2_MODE=off` (tanpa deploy ulang), atau revert commit.

### Fase 2: Post-processing v2 (role assignment, collapse, smoothing). Prioritas 1
- **Tujuan:** menghapus ~14–16% role tertukar dari hulu, lalu merapikan flip dan cluster minor.
- **File baru:**
  - `model/speech_stt/diarization_v2/postprocess.py`:
    - `collapse_to_two(words) -> words`: semua speaker di luar 2 terbesar (berdasarkan jumlah kata) diberi label tetangga waktu terdekat dari 2 speaker dominan.
    - `smooth_single_word_flips(words, max_gap_sec=0.25) -> (words, n_smoothed)`: pola A-B-A dengan kedua jeda <0,25 s diubah menjadi A-A-A. Kalau salah satu jeda ≥0,25 s, kata dianggap backchannel dan dipertahankan.
    - `group_utterances(words, max_gap_sec=0.6) -> List[Utterance]`
  - `model/speech_stt/diarization_v2/roles.py`:
    - `assign_roles(utterances, cues) -> RoleDecision(mapping, role_confident, evidence)`
    - Skor per speaker: hit `intro` di 8 utterance pertama (bobot 2), hit `script` di seluruh call (bobot 1), hit cue nasabah (mis. "ini dari mana", "siapa ini") (bobot −1), ditambah prior dominansi kata.
    - `role_confident=True` kalau cue dan dominansi sepakat, atau margin cue ≥ `ROLE_CUE_MIN_MARGIN`. Selain itu `False`. Ambang dikalibrasi di Fase 3.
    - `evidence` hanya berisi hitungan, tanpa teks.
  - `model/speech_stt/diarization_v2/role_cues.yaml`: regex `intro`, `script`, dan `customer`, porting dari App B `call_ownership.py:249-282`. Sumber kebenaran menunggu K4.
- **File diubah:** `diarization_v2/pipeline.py` merangkai collapse → smoothing → grouping → roles → `SpeakerSegment`, lalu `merge_same_speaker_simple` yang sudah ada (dipakai ulang, tidak diubah).
- **Migration:** tidak.
- **Test:**
  - `tests/test_v2_postprocess.py`: collapse, smoothing (flip tanpa jeda di-smooth, backchannel ber-jeda dipertahankan, pola di awal/akhir), dan grouping.
  - `tests/test_v2_roles.py`: nasabah bicara duluan dan agent memperkenalkan diri → agent benar. Panggilan susulan ("dengan X kembali") → benar. Tanpa cue tapi dominan → `role_confident=False`. Seri → `False`.
- **Effort:** M
- **Risiko:** regex cue overfit pada Cashline. Mitigasi: evaluasi proxy pada seluruh data Cashline (Fase 3) dan GT (Fase 4). Kalau `role_confident=False`, perilaku default tetap memakai keputusan dominansi (tidak lebih buruk dari sekarang).
- **Rollback:** flag `off`/`shadow`.

### Fase 3: Harness evaluasi offline + cache (tanpa biaya API)
- **Tujuan:** mengukur v2 terhadap jalur lama pada **seluruh** call Cashline dari raw response yang sudah ada, dan menyiapkan perhitungan DER.
- **File baru:**
  - `tools/diar_eval/__init__.py`
  - `tools/diar_eval/cache.py`: export read-only dari `dashboard.result_speech_to_text` (Cashline, ditautkan lewat `results.source_files`) ke `DIAR_EVAL_CACHE_DIR`. Direktori ini di luar repo, `chmod 700`, dan dikecualikan lewat `.gitignore`. Key cache: `stem` + `sha256(params)`.
  - `tools/diar_eval/proxy_metrics.py`: metrik dari audit §5 (rasio swap terhadap cue, cue di 2 label, flip, speaker minoritas), dihitung untuk jalur lama dan v2.
  - `tools/diar_eval/metrics.py`: DER/JER via `pyannote.metrics.diarization`, role accuracy berbobot waktu, dan role accuracy per item checklist (mapping item ke jendela waktu bukti, sesuai D4).
  - `tools/diar_eval/grid.py`: grid search parameter post-processing (`max_gap_sec`, `ROLE_CUE_MIN_MARGIN`, bobot dominansi) di atas cache.
  - `tools/diar_eval/README.md`
- **File diubah:** `.gitignore` (tambah `tools/diar_eval/cache/`, `*.rttm`).
- **Migration:** tidak.
- **Test:** `tests/test_diar_eval_metrics.py`. Fixture RTTM sintetis dengan DER yang diketahui, dan role accuracy untuk kasus swap total (0%) maupun sempurna (100%).
- **Effort:** M
- **Risiko:** cache lokal berisi transkrip (PII). Mitigasi: lokasi terbatas, tidak di-commit, ada perintah purge, dan retensi ditentukan (K8).
- **Rollback:** hapus `tools/diar_eval/`. Runtime tidak tersentuh.

### Fase 4: Ground truth 30–50 call Cashline (proses, sebagian besar non-kode)
- **Tujuan:** membuat GT untuk DER dan role accuracy per item.
- **File baru:**
  - `docs/diarization-gt-guideline.md`: aturan pelabelan (batas utterance, overlap, backchannel)
  - `tools/diar_eval/sample.py`: sampling terstratifikasi dari NQ Cashline, 4 strata × 10–12 call: tertukar / cue di 2 label / speaker minoritas ≤31 kata / normal
  - `tools/diar_eval/gt_io.py`: konversi antara RTTM dan JSON
- **Data GT tidak masuk repo**, karena mengandung PII. Path-nya diberikan lewat env.
- **Migration:** tidak.
- **Test:** `tests/test_gt_io.py`
- **Effort:** L, karena bergantung pada kapasitas pelabel (D1–D3).
- **Risiko:** pelabelan tidak konsisten. Mitigasi: 5 call dilabeli ganda untuk mengukur kesepakatan antar-pelabel.
- **Rollback:** tidak relevan.

### Fase 5: A/B parameter Scribe + preprocessing (**memanggil API, butuh approval terpisah**)
- **Tujuan:** memilih kombinasi mode × audio terbaik berdasarkan GT.
- **File baru:**
  - `model/speech_stt/diarization_v2/preprocess.py`: `prepare_for_scribe(src, dst)` dengan ffmpeg `-ac 1 -ar 16000 -af "highpass=f=100,lowpass=f=3800,loudnorm=I=-20:TP=-2:LRA=11"`. Tanpa DRC, tanpa denoise, tanpa `pan` 0,5 (memperbaiki S5 di v2).
  - `model/speech_stt/diarization_v2/keyterms.py`: `load_keyterms(campaign)`, yang membaca dan memvalidasi `config/scribe_keyterms.yaml` (isi awal: 7 term dari `.env`)
  - `config/scribe_keyterms.yaml`
  - `tools/diar_eval/run_ab.py`: menjalankan konfigurasi pada call GT dan menyimpan response ke cache (tidak memanggil ulang konfigurasi yang sudah ter-cache)
- **Grid:**
  - Audio: {lama: DRC, v2: prep}
  - Mode A `num_speakers=2`
  - Mode B `num_speakers=None` + threshold {0,15; 0,20; 0,25; 0,30}
  - Mode C opsional `detect_speaker_roles` (E3)
  - Total: (1 + 4 [+1]) × 2 = **10–12 konfigurasi × ~40 call ≈ 400–480 request**. Biaya menunggu E2.
- **File diubah:** `diarization_v2/provider.py` (memakai preprocess dan keyterms saat flag `on`).
- **Migration:** tidak.
- **Test:**
  - `tests/test_v2_preprocess.py`: audio sintetis 8 kHz mono → 16 kHz mono, energi di atas 3,8 kHz teredam, level tidak turun 6 dB.
  - `tests/test_keyterms.py`: validasi batas SDK.
- **Effort:** M (+ waktu tunggu API)
- **Risiko:** biaya, dan audio pelanggan dikirim ulang ke cloud. Mitigasi: hanya call GT, cache wajib, dan `enable_logging` sesuai E1.
- **Rollback:** konfigurasi terpilih hanya berupa nilai env.

### Fase 6: Fallback LLM on-prem
- **Tujuan:** menyelesaikan call dengan `role_confident=False` (~7%).
- **File baru:**
  - `model/speech_stt/diarization_v2/llm_fallback.py`: `correct_roles(utterances) -> Correction(swap_all: bool, flip_ids: List[int])`
    - Prompt berisi daftar `id | label | teks` (dipotong per utterance).
    - Output JSON yang divalidasi skemanya.
    - Guard: `flip_ids` ≤20% utterance. Kalau melewati batas, koreksi diabaikan.
    - Timeout, dan saat gagal hasil tidak diubah.
    - Teks tidak di-log.
  - `config.py`: `DIARIZATION_LLM_FALLBACK_ENABLED` (default off), `DIARIZATION_LLM_URL`, `DIARIZATION_LLM_MODEL`
- **File diubah:** `diarization_v2/pipeline.py` (memanggil fallback setelah `assign_roles` kalau tidak confident).
- **Migration:** tidak.
- **Test:** `tests/test_llm_fallback.py`. Client di-mock: JSON valid, JSON rusak, flip berlebihan, timeout.
- **Effort:** M
- **Risiko:** halusinasi LLM. Mitigasi: guard, evaluasi pada GT, dan hanya berjalan untuk call yang tidak confident.
- **Rollback:** flag off.

### Fase 7: Persistensi & observability
- **Tujuan:** menyimpan hasil v2 (shadow maupun on) untuk monitoring, tanpa mengubah PDF.
- **File baru:** `migrations/sql/20261028_result_speech_to_text_add_diarization_v2.sql` (**ditulis saja, tidak dijalankan**). Isinya `ALTER TABLE dashboard.result_speech_to_text ADD COLUMN IF NOT EXISTS diarization_v2 JSONB;`, dengan blok rollback `DROP COLUMN IF EXISTS`. Pemilik tabel perlu dikonfirmasi (K2), karena tabel ini tidak dibuat oleh migration di repo mana pun.
- **File diubah:**
  - `services/db.py::save_stt_pipeline_results`: parameter opsional `diarization_v2=None`. Kolom hanya ditulis kalau nilainya tidak `None` **dan** kolomnya ada (dicek sekali). Saat flag off, SQL tetap sama persis.
  - `services/pipeline.py:208-215`: meneruskan `metadata.get("diarization_v2")`.
  - Log satu baris metrik per call: `n_spk_raw`, `n_collapsed`, `n_smoothed`, `role_confident`, `cue_src`, `llm_used`. Tanpa teks.
- **Migration:** **ya**, berupa file SQL saja.
- **Test:**
  - `tests/test_db_save_v2.py`: cursor di-mock. Flag off menghasilkan SQL identik.
  - `tests/test_no_transcript_in_logs.py`: caplog memastikan teks fixture tidak muncul di log.
- **Effort:** S
- **Risiko:** kolom belum ada di DB. Mitigasi: cek keberadaan kolom, fail-soft (perilaku sekarang juga non-fatal, `pipeline.py:216-220`).
- **Rollback:** flag off, lalu jalankan blok rollback SQL (oleh DBA).

### Fase 8: Shadow → canary → on (keputusan human)
- **Tujuan:** rilis bertahap.
- **Langkah:**
  1. `shadow` 1–2 minggu (biaya API nol, karena raw response yang sama). Bandingkan metrik proxy v2 dan lama di `diarization_v2`.
  2. `on` untuk Cashline saja (`DIARIZATION_V2_CAMPAIGNS=Cashline`).
  3. Evaluasi interaksi dengan `fix_speaker_roles` App B (K3).
- **File diubah:** `config.py` (`DIARIZATION_V2_CAMPAIGNS`), `engine.py`/`pipeline.py` (meneruskan campaign. `NewDashboardPipeline` sudah membawa `campaign`, `pipeline.py:377`).
- **Migration:** tidak.
- **Test:** `tests/test_v2_campaign_gate.py`
- **Effort:** S
- **Risiko:** perubahan label mengubah hasil scorecard tiket yang di-reprocess. Mitigasi: canary, dan diumumkan ke tim QC.
- **Rollback:** `DIARIZATION_V2_MODE=shadow`/`off`.

### Fase 9 (bersyarat B5): dual-channel
- Hanya dikerjakan kalau PBX bisa mengekspor stereo.
- `ScribeDiarizationProvider` mendapat mode `multichannel` (`use_multi_channel=True`, `multichannel_output_style="combined"`, role dari `channel_index` + mapping channel→role dari PBX).
- `preprocess.py` mendapat varian stereo (tanpa `-ac 1`). `audio_conversion.py` lama tetap tidak diubah.
- Effort M. Biaya API 2×. Kemungkinan menggantikan Fase 5–6 untuk call stereo.

---

## 3. Kriteria sukses terukur

| # | Metrik | Baseline | Target | Cara ukur |
|---|---|---|---|---|
| K-1 | Rasio role tertukar (proxy cue) pada Cashline NQ dan PASS | 14,3% / 15,8% | **≤ 2%** | Fase 3 `proxy_metrics.py`, seluruh cache, tanpa API |
| K-2 | Role accuracy berbobot waktu (GT) | diukur di Fase 4 | **≥ 97%** | `metrics.py` |
| K-3 | Role accuracy per item checklist kritis (D4) | diukur di Fase 4 | **≥ 98%** per item | `metrics.py` |
| K-4 | DER (collar 0,25 s, tanpa overlap) | diukur di Fase 4 | **turun ≥ 25% relatif** dari baseline (target absolut ditetapkan setelah baseline) | `pyannote.metrics` |
| K-5 | Cue agent muncul di ≥2 label (indikasi S7) | 15,0% | **≤ 7%** | proxy, setelah Fase 5 |
| K-6 | Call dengan `role_confident=False` | — | **≤ 10%** call, dan fallback LLM benar ≥ 80% pada GT | Fase 6 |
| K-7 | Flag `off` tidak mengubah apa pun | — | golden test 100% identik | Fase 0–1 |
| K-8 | Tidak ada teks transkrip di log | — | test caplog lulus | Fase 7 |
| K-9 | Overhead | — | latensi p95 naik ≤ 10%, biaya API per call naik ≤ angka E2 | log shadow |

---

## 4. Keputusan yang perlu Anda ambil

| # | Keputusan | Rekomendasi | Terkait |
|---|---|---|---|
| K1 | Nama flag. Diarization **sudah** aktif di produksi, jadi `DIARIZATION_ENABLED=false` akan menyesatkan. | Pakai `DIARIZATION_V2_MODE=off\|shadow\|on` | prompt §5 |
| K2 | Siapa pemilik tabel `dashboard.result_speech_to_text`, dan siapa yang menjalankan migration SQL? | DBA/owner App A menjalankan file SQL manual | Fase 7 |
| K3 | `fix_speaker_roles` App B setelah v2 aktif | **Pertahankan** sebagai safety net. Cue-nya sama, jadi idealnya no-op. Pantau berapa kali swap di App B masih terpicu. | C3, C4 |
| K4 | Sumber kebenaran regex cue role | Satu file YAML. App A memakai salinan ber-versi, dan App B tetap memakai versinya sendiri sampai disatukan. | C3 |
| K5 | Izin dan pelabel GT, 40 call terstratifikasi | Tim QC, 5 call dilabeli ganda | D1–D3 |
| K6 | Budget A/B API (~400–480 request) dan apakah mode C (`detect_speaker_roles`) diikutkan | Ikutkan C (biaya +10% saja, pembanding gratis untuk `roles.py`) | E2, E3 |
| K7 | `enable_logging=False` | Aktifkan kalau akun enterprise (E1). Kalau bukan, catat risiko retensi ke compliance. | E1 |
| K8 | Lokasi dan retensi cache evaluasi lokal (berisi transkrip) | Direktori terbatas di server ini, purge setelah evaluasi selesai, maksimal 30 hari | Fase 3 |
| K9 | Tooling dev (pytest, ruff) | Pasang di venv dev terpisah. **Jangan** di venv produksi. | Fase 0 |
| K10 | LLM fallback: model dan endpoint on-prem | Container `ollama` yang sudah ada. Model ditentukan Anda. | E5 |
| K11 | DRC di jalur v2 | Matikan di v2 (`prepare_for_scribe`), jalur lama tetap memakai DRC. Keputusan final setelah A/B. | B4 |
| K12 | Investigasi dual-channel PBX secara paralel | Ya. Ini paling berpotensi menyelesaikan masalah secara struktural. | B5 |
| K13 | Push branch ke remote | Tunggu instruksi | F2 |

---

## 5. Kepatuhan (UU PDP / OJK)

- **Yang dikirim ke cloud:** file audio dan keyterms (tanpa PII). Tidak ada teks DB atau prompt campaign yang dikirim. Fallback LLM bersifat **on-prem**.
- **Retensi:** `enable_logging=False` kalau akun enterprise (K7). Raw response di DB internal sudah tercatat sebagai PII dalam inventaris data (P3).
- **Log:** hanya metrik (Fase 7, K-8).
- **Cache evaluasi dan GT:** di luar repo, akses terbatas, ada retensi (K8).
- **Hybrid on-prem:** jalur pyannote dan Whisper masih ada di kode (`engine.py:330-391`) sebagai alternatif untuk campaign yang tidak boleh ke cloud. Jalur ini tidak diubah oleh plan ini.

---

## 6. Ringkasan urutan

```
F0 test fondasi ─► F1 provider+flag ─► F2 post-processing (role) ─► F3 eval offline ─► F7 persistensi ─► F8 shadow
                                                                        │
                                              F4 GT (paralel, menunggu pelabel) ─► F5 A/B API ─► F6 LLM fallback ─► F8 canary/on
F9 dual-channel: bersyarat B5, investigasi paralel
```
Fase 0–3 dan 7 tidak membutuhkan biaya API maupun keputusan eksternal, kecuali K1, K2, dan K9. Fase 0–3 sudah bisa menghasilkan angka K-1 dari data Cashline.
