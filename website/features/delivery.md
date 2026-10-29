# Webhook & forwarding

Setelah PDF transkrip jadi, `deliver_result()` mengirimkannya. Implementasinya bergantung pada endpoint yang memulai job.

## Webhook (dashboard lama)

`services/webhook_service.py` menjalankan satu **daemon thread** yang mengambil notifikasi dari antrean (kapasitas 2000) dan mengirimnya dengan `post_with_retry()`. Job tidak pernah menunggu webhook selesai.

```json
{
  "job_id": "…",
  "status": "completed",
  "created_at": "2026-10-29T03:00:00+00:00",
  "completed_at": "2026-10-29T03:00:00+00:00",
  "result": { },
  "error": null,
  "original_path": "…",
  "marker_path": "…",
  "speaker_count": 2
}
```

- Header: `User-Agent: Speech-STT-Worker/<pid>`, `Content-Type: application/json`
- Timeout 5 detik, 2 percobaan. Kegagalan akhir hanya di-log.
- Notifikasi dikirim saat status berubah (`processing`, `completed`, `failed`).

### Retry HTTP

`post_with_retry()` me-retry error jaringan dan respons **5xx** dengan backoff eksponensial (`2^attempt` detik). Respons **4xx** tidak di-retry karena mengulang request yang sama tidak akan mengubah hasilnya.

### Penerima callback bawaan

`POST /speech/webhook/callback` adalah penerima contoh yang menerima JSON murni, atau multipart/form dengan field `metadata` berisi string JSON. Endpoint ini berguna untuk uji lokal.

## Forward ke backend utama (dashboard baru)

`services/backend_forwarder.py` memakai pola **Strategy**. `forward_to_all_branches()` menjalankan kedua cabang secara independen, sehingga gagal di satu cabang tidak menggagalkan cabang lain.

| Strategi | Tujuan | Format | Catatan |
|---|---|---|---|
| `RegisterResultStrategy` | `MAIN_BACKEND_RESULT_URL` | JSON | Mendaftarkan hasil memakai `job_id` STT |
| `UploadTranscriptStrategy` | `MAIN_BACKEND_BASE_URL/upload_transcript` | multipart (PDF + `campaign`) | Header `X-API-Key: MAIN_BACKEND_API_KEY`. Backend membuat `result_id` baru dan memicu evaluasi LLM (Celery). 3 percobaan, timeout 30 detik. File PDF dibuka ulang di setiap percobaan. |

`campaign` diambil dari form `save_dashboard`, dengan fallback `DEFAULT_UPLOAD_TRANSCRIPT_CAMPAIGN`.

## Menambah tujuan baru

Buat class baru yang mengimplementasikan `ResultForwarder.forward(ctx) -> ForwardResult`, lalu panggil dari `forward_to_all_branches()`. Pipeline tidak perlu diubah.
