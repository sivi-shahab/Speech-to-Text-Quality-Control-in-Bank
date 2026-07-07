#!/usr/bin/env python3
"""
routes/webhook_callback_routes.py

POST /speech/webhook/callback — dipanggil balik oleh sistem eksternal
setelah menerima notifikasi job selesai, untuk trigger cleanup file
audio asli + marker file. Dipindah verbatim dari server.py v4.0.
"""

import os
import json
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request

logger = logging.getLogger("server")

router = APIRouter()


def _is_success_status(v) -> bool:
    if isinstance(v, bool):
        return v is True
    if isinstance(v, str):
        return v.lower() in {"completed", "succeeded", "success", "ok", "done"}
    return False


@router.post("/speech/webhook/callback")
async def webhook_callback(request: Request):
    payload = {}
    content_type = request.headers.get("content-type", "")

    try:
        # Cek apakah yang datang adalah Form-Data (seperti error sebelumnya)
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form_data = await request.form()
            metadata_str = form_data.get("metadata")

            if metadata_str:
                payload = json.loads(metadata_str)  # Ubah string form menjadi JSON
            else:
                logger.error("[WEBHOOK] Form-data diterima, tapi field 'metadata' kosong.")
                return {"status": "error", "message": "missing metadata field"}

        # Jika bukan Form-Data, asumsikan itu JSON murni
        else:
            payload = await request.json()

    except json.JSONDecodeError as e:
        logger.error("[WEBHOOK] Gagal parsing string metadata menjadi JSON: %s", e)
        return {"status": "error", "message": "invalid metadata json string"}
    except Exception as e:
        logger.error("[WEBHOOK] Invalid JSON/Request: %s", e)
        return {"status": "error", "message": "invalid request format"}

    job_id        = payload.get("job_id")
    status        = payload.get("status")
    error         = payload.get("error")
    worker_pid    = payload.get("worker_pid")
    speaker_count = payload.get("speaker_count")
    original_path = (
        payload.get("original_path")
        or payload.get("file_path")
        or request.query_params.get("original_path")
    )

    # Catatan: ASSIGNED_GPU dipakai hanya untuk logging di versi asli.
    from services.server_config import ASSIGNED_GPU

    logger.info(
        "[WEBHOOK] job=%s status=%s pid=%s gpu=%d speakers=%s",
        job_id, status, worker_pid, ASSIGNED_GPU, speaker_count,
    )

    if _is_success_status(status):
        if original_path:
            src = Path(original_path)
            try:
                if src.exists():
                    await asyncio.to_thread(os.remove, src)
                    logger.info("[WEBHOOK] Deleted audio: %s", src)
            except Exception as e:
                logger.error("[WEBHOOK] Delete failed %s: %s", src, e)

            for marker in [
                Path("%s.queued" % src),
                src.with_suffix(src.suffix + ".queued"),
            ]:
                try:
                    if marker.exists():
                        await asyncio.to_thread(os.remove, marker)
                        logger.info("[WEBHOOK] Deleted marker: %s", marker)
                        break
                except Exception as e:
                    logger.error("[WEBHOOK] Marker delete failed %s: %s", marker, e)
        else:
            logger.warning(
                "[WEBHOOK] Job completed tapi original_path kosong. job_id=%s", job_id
            )
    else:
        readable_error = (
            error
            if (error and str(error).strip().lower() not in ("", "none"))
            else "Unknown error — periksa log (PID: %s)" % worker_pid
        )
        logger.error(
            "[WEBHOOK] Job Failed: job_id=%s | pid=%s | error=%s",
            job_id, worker_pid, readable_error,
        )

    return {"status": "received", "job_id": job_id}
