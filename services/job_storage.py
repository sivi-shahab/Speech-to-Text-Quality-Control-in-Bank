#!/usr/bin/env python3
"""
services/job_storage.py

ThreadSafeJobStorage dipindah verbatim dari speech_routes.py.
Satu instance singleton `job_storage` dipakai di seluruh app,
sama seperti sebelumnya.
"""

import time
import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ThreadSafeJobStorage:
    def __init__(self, ttl_seconds: int = 7200):
        self._storage: Dict[str, Dict] = {}
        self._lock    = threading.Lock()
        self._ttl     = ttl_seconds

    def get(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            return self._storage.get(job_id)

    def set(self, job_id: str, job: Dict):
        job = dict(job)
        job.setdefault("_created_at", time.time())
        with self._lock:
            self._storage[job_id] = job
            self._evict_expired_unsafe()

    def delete(self, job_id: str):
        with self._lock:
            self._storage.pop(job_id, None)

    def values(self):
        with self._lock:
            return list(self._storage.values())

    def count_by_status(self, *statuses) -> int:
        with self._lock:
            return sum(
                1 for v in self._storage.values()
                if v.get("status") in statuses
            )

    def _evict_expired_unsafe(self):
        now     = time.time()
        expired = [
            k for k, v in self._storage.items()
            if now - v.get("_created_at", now) > self._ttl
        ]
        for k in expired:
            del self._storage[k]
        if expired:
            logger.debug("Evicted %d expired jobs.", len(expired))

    def __contains__(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._storage

    def __len__(self) -> int:
        with self._lock:
            return len(self._storage)


# Singleton — sama seperti `job_storage` di speech_routes.py versi lama.
job_storage = ThreadSafeJobStorage(ttl_seconds=7200)
