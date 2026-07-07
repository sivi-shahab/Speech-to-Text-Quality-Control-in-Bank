#!/usr/bin/env python3
"""
services/executor.py

ThreadPoolExecutor dedicated untuk job transcribe. Dipisah dari
speech_routes.py supaya server.py (endpoint /queue/status) bisa
import tanpa perlu import seluruh router.
"""

import logging
import concurrent.futures

from services.config import MAX_TRANSCRIBE_WORKERS

logger = logging.getLogger(__name__)

transcribe_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers        = MAX_TRANSCRIBE_WORKERS,
    thread_name_prefix = "stt-transcribe",
)

logger.info(
    "Transcribe executor initialized: max_workers=%d", MAX_TRANSCRIBE_WORKERS
)
