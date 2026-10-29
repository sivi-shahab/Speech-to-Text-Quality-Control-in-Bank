# Kenapa layanan ini?

Quality control telemarketing menuntut lebih dari sekadar teks. Auditor perlu tahu **siapa** mengucapkan **apa**, apakah agent membacakan skrip wajib, dan apakah ada tanda suara berganti orang di tengah panggilan. Layanan ini dibangun untuk kebutuhan itu.

## Masalah yang diselesaikan

| Masalah di rekaman telepon bank | Jawaban layanan ini |
|---|---|
| Rekaman mono 1-channel, suara agent dan nasabah bercampur | Diarization yang di-tuning untuk telepon: pyannote dengan hyperparameter telephony, NeMo MSDD telephonic, atau diarization bawaan ElevenLabs Scribe |
| Nasabah sering hanya menjawab singkat ("iya", "boleh") sehingga terserap ke cluster agent | `PYANNOTE_MIN_CLUSTER_SIZE` yang lebih kecil, plus validasi `speaker_imbalance_detected` saat porsi agent > 85% |
| Role Agent/Customer tertukar karena aturan "yang bicara duluan = agent" | [Diarization v2](/features/diarization-v2) menentukan role dari cue skrip dan dominansi bicara |
| Istilah produk (mis. nama kartu kredit) salah ditranskrip | Dynamic prompt per tiket dari DB (Whisper) atau keyterm prompting (Scribe) |
| Antrean dashboard timeout saat GPU sibuk | Endpoint data dipisah ke [lightweight server](/features/lightweight-server) tanpa model |
| Auditor butuh dokumen, bukan JSON | Hasil dibungkus PDF transkrip dan dikirim ke backend utama |

## Prinsip desain

1. **Satu proses per GPU.** Model Whisper, pyannote, gender, dan emosi dimuat sekali dan dibagi ke semua thread. Kapasitas ditambah dengan container baru per GPU, bukan worker baru.
2. **Backend bisa ditukar lewat env var.** ASR dan diarization adalah dua sumbu Strategy yang independen. Mengganti mesin tidak perlu mengubah kode.
3. **Jalur lama tidak berubah saat fitur baru mati.** Fitur seperti diarization v2 berada di paket terpisah dan dilindungi feature flag yang default-nya `off`. Golden test menjaga perilaku jalur lama.
4. **Tanpa teks transkrip di log.** Yang dicatat hanya metrik numerik, label, dan timestamp.
5. **Fail-safe.** Tuning hyperparameter yang gagal di-apply jatuh kembali ke default. Kegagalan cache tidak menggagalkan response.

## Kepatuhan data

::: warning ElevenLabs adalah layanan cloud pihak ketiga
Dengan `ASR_BACKEND=elevenlabs` atau endpoint `/speech/stt/elevenlabs`, audio dikirim ke server ElevenLabs. Region resmi yang dipublikasikan adalah EU dan India, bukan Indonesia. Pastikan ada persetujuan tertulis dari tim legal/compliance sebelum memproses data nasabah produksi. Retensi diatur lewat `SCRIBE_ENABLE_LOGGING` (zero-retention hanya untuk akun enterprise).
:::

Data evaluasi (cache transkrip, ground truth) berisi PII. Tool evaluasi menolak menyimpan cache di dalam repo dan mewajibkan purge maksimal 30 hari. Lihat [Evaluasi diarization](/features/diar-eval).
