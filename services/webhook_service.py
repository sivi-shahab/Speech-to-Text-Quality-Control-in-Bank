#!/usr/bin/env python3
"""
services/webhook_service.py

Webhook queue + daemon thread + send_webhook_notification().
Dipindah verbatim dari speech_routes.py, logic tidak berubah.
Sebelumnya daemon-thread-start dijalankan sebagai side-effect saat
speech_routes.py di-import — sekarang eksplisit lewat start_webhook_daemon()
supaya lebih mudah di-test dan tidak nyala 2x kalau module lain
accidentally re-import.
"""

import os
import time
import logging
import threading
import queue as _queue
from datetime import datetime, timezone
from typing import Optional

from services.http_retry import post_with_retry

logger = logging.getLogger(__name__)

_webhook_queue: _queue.Queue = _queue.Queue(maxsize=2000)
_daemon_started = False
_daemon_lock    = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _webhook_daemon():
    while True:
        try:
            item = _webhook_queue.get(timeout=5)
        except _queue.Empty:
            continue
        try:
            post_with_retry(
                url         = item["url"],
                payload     = item["payload"],
                headers     = {
                    "User-Agent":   "Speech-STT-Worker/%d" % os.getpid(),
                    "Content-Type": "application/json",
                },
                max_retries = 2,
                timeout     = 5,
            )
        except Exception as e:
            logger.error(
                "[WEBHOOK] Gagal semua retry ke %s: %s", item["url"], e
            )


def start_webhook_daemon():
    """Idempotent — aman dipanggil berkali-kali (mis. di setiap import)."""
    global _daemon_started
    with _daemon_lock:
        if _daemon_started:
            return
        thread = threading.Thread(
            target=_webhook_daemon, name="WebhookDaemon", daemon=True,
        )
        thread.start()
        _daemon_started = True


def send_webhook_notification(
    job_id:        str,
    status:        str,
    webhook_url:   str,
    result:        dict          = None,
    error:         str           = None,
    original_path: Optional[str] = None,
    marker_path:   Optional[str] = None,
    speaker_count: Optional[int] = None,
):
    if not webhook_url:
        return
    payload = {
        "job_id":        job_id,
        "status":        status,
        "created_at":    _now().isoformat(),
        "completed_at":  _now().isoformat(),
        "result":        result,
        "error":         error,
        "original_path": original_path,
        "marker_path":   marker_path,
        "speaker_count": speaker_count,
        "worker_pid":    os.getpid(),
    }
    try:
        _webhook_queue.put_nowait({"url": webhook_url, "payload": payload})
    except _queue.Full:
        logger.warning("[WEBHOOK] Queue penuh, drop job %s", job_id)


# Daemon dinyalakan saat module ini pertama kali diimport — sama seperti
# behavior asli di speech_routes.py (module-level side effect).
start_webhook_daemon()
