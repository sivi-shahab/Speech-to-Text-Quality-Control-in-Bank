# Pertanyaan yang Perlu Dikonfirmasi — Diarization Scribe (Cashline)

- Branch: `feat/diarization-scribe-audit`
- Terkait: `docs/diarization-audit.md`
- Status per 2026-10-28

Isi kolom **Jawaban** lalu beri tahu saya. Pertanyaan bertanda ⭐ memblokir atau mengubah isi rencana Fase 3.

---

## A. Sudah terjawab

| # | Pertanyaan | Jawaban |
|---|---|---|
| A1 | Kode yang berjalan di port 8000/8001 | Repo `refactored` |
| A2 | Format rekaman asli | GSM, dikonversi ke WAV oleh user sebelum di-upload |
| A3 | Persetujuan legal/compliance untuk ElevenLabs | Sudah disetujui |
| A4 | Sumber data analisis | Tiket Not Qualified di dashboard, fokus campaign Cashline |
| A5 | Deploy | Dikerjakan di branch baru, `main` tidak diubah, deploy menunggu keputusan human |

---

## B. Audio & preprocessing

| # | Pertanyaan | Kenapa penting | Jawaban |
|---|---|---|---|
| B1 ⭐ | Konversi GSM→WAV memakai tool/perintah apa (mis. `sox`, `ffmpeg`, script PBX)? Mohon kirim perintah persisnya. | Menentukan apakah ada double-transcode, resample, atau filter tersembunyi sebelum audio sampai ke App A (temuan S6). | |
| B2 ⭐ | Spesifikasi output WAV: sample rate (8 kHz?), bit depth/encoding (PCM16 atau μ-law/A-law), mono? | Kalau μ-law atau sudah 16 kHz, rantai preprocessing di Fase 3 berbeda. | |
| B3 | Apakah ada normalisasi, boost, atau noise reduction saat konversi GSM→WAV? | DRC App A (+40 dB) bisa menumpuk dengan boost di hulu (temuan S3). | |
| B4 | Untuk apa `DRC_*` di `.env` di-tuning (target −15 dBFS, +40 dB, release 50 ms): Whisper, emotion, atau Scribe? | Kalau bukan untuk Scribe, DRC bisa dimatikan khusus di jalur Scribe tanpa memengaruhi fitur lain. | |
| B5 | Bisakah PBX mengekspor rekaman **dual-channel** (agent dan nasabah di channel terpisah)? | Kalau bisa, sebagian besar masalah diarization hilang secara struktural (`use_multi_channel`). | |

## C. Upstream & data

| # | Pertanyaan | Kenapa penting | Jawaban |
|---|---|---|---|
| C1 | Siapa yang memanggil `/speech/stt/save` dan `/speech/stt/save_dashboard`? Apakah selalu mengirim `language=id`? | Kalau ada yang mengirim kosong, Scribe akan auto-detect bahasa. | |
| C2 | 95 tiket NQ Collection memakai pola file lama (`Nama_telepon-...pdf`) dan tidak tertaut ke raw response App A. Apakah file-file itu diproses lewat jalur STT lain? Apakah Collection perlu masuk scope? | Menentukan apakah Collection ikut dievaluasi atau scope cukup Cashline. | |
| C3 | Repo App B mana yang kanonik untuk `compliance/*` (termasuk `fix_speaker_roles`): `telemarketing-qc-worker`, `telemarketing-qc-core`, atau `telemarketing-qc-system`? | Perbaikan role di App A harus selaras dengan, atau menggantikan, tambalan regex di App B. | |
| C4 | Kalau role sudah benar di App A, apakah `fix_speaker_roles` di App B **dipertahankan** sebagai safety net atau **dimatikan**? | Mencegah koreksi ganda. Kalau keduanya aktif dan berbeda pendapat, label bisa tertukar lagi. | |

## D. Ground truth & evaluasi

| # | Pertanyaan | Kenapa penting | Jawaban |
|---|---|---|---|
| D1 ⭐ | Bolehkah 30–50 call Cashline diambil secara terstratifikasi dari NQ? Strata usulan: role tertukar / frasa agent muncul di 2 label / nasabah ≤31 kata / normal. | Tanpa ground truth, A/B parameter Scribe dan DRC tidak bisa diukur (DER dan role accuracy). | |
| D2 ⭐ | Siapa yang melabeli ground truth (tim QC?), dan berapa kapasitasnya per minggu? | Menentukan jadwal Fase evaluasi. | |
| D3 | Format label: cukup label speaker per segmen (start, end, AGENT/NASABAH), atau juga perlu koreksi teks? | DER hanya butuh label waktu dan speaker. Koreksi teks jauh lebih mahal. | |
| D4 | Item checklist mana yang paling kritis bergantung pada role (mis. disclosure bunga, perkenalan nama agent, persetujuan nasabah)? | Role accuracy akan diukur per item ini, bukan per vonis NQ/PASS. | |
| D5 | Bolehkah 217 PDF korpus kalibrasi App B (termasuk 31 yang tertukar) dipakai sebagai seed evaluasi role? | Seed gratis untuk role accuracy karena sudah divalidasi manual. | |

## E. ElevenLabs & kepatuhan

| # | Pertanyaan | Kenapa penting | Jawaban |
|---|---|---|---|
| E1 ⭐ | Apakah akun ElevenLabs berlevel **enterprise**? | `enable_logging=False` (zero-retention) hanya tersedia untuk enterprise. Sekarang retensi default yang berlaku (temuan P2). | |
| E2 | Budget untuk A/B: berapa call atau berapa dolar yang boleh dipakai untuk panggilan API ulang? | Threshold tuning dan uji DRC wajib memanggil API ulang. Biaya tambahan: keyterms +20%, `detect_speaker_roles` +10%. | |
| E3 | Bolehkah `detect_speaker_roles` (role agent/customer bawaan Scribe, +10%) ikut di-A/B? | Opsi ini tersedia di SDK 2.56.0 dan belum ada di rencana awal. | |
| E4 | Bolehkah keyterms ditambah per campaign (mis. dari prompt/konteks Cashline di DB)? Siapa yang menyetujui daftarnya? | Keyterms tidak boleh memuat PII nasabah (temuan P5). | |
| E5 | Fallback LLM on-prem untuk call dengan `role_confident=False`: model/endpoint mana yang dipakai (mis. container `ollama`)? | Menentukan desain fallback di Fase 3. | |

## F. Proses & rilis

| # | Pertanyaan | Kenapa penting | Jawaban |
|---|---|---|---|
| F1 | Siapa yang memberi keputusan deploy, dan apa kriteria go/no-go (mis. target role accuracy, DER)? | Kriteria sukses di plan harus sesuai harapan pengambil keputusan. | |
| F2 | Apakah commit ke branch `feat/diarization-scribe-audit` boleh di-push ke remote GitHub? | Saat ini semua perubahan masih lokal. | |
