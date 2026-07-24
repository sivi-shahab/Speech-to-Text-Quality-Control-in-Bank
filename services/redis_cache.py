#!/usr/bin/env python3
"""
services/redis_cache.py

Cache Redis (L1, cepat) untuk dashboard."cashline-ntb-asscend" -- lapisan
DI ATAS cache Postgres (L2, permanen di services/db.py) yang sudah ada.

Pakai Redis instance YANG SAMA dengan App B/Celery (port 6378, exposed ke
host lewat docker-compose "ports: 6378:6378"), TAPI DB NUMBER BERBEDA
(DB 1, bukan DB 0 yang dipakai Celery broker/backend) -- supaya key
TIDAK BERCAMPUR dengan task queue Celery.

TTL 1 hari (86400 detik) -- data DWH (cashline/customer) jarang berubah
dalam sehari, jadi aman di-cache selama itu.

⚠️ DESAIN PENTING: Redis di sini SEPENUHNYA OPSIONAL/best-effort. Kalau
Redis tidak tersedia/gagal connect (down, network issue, dll), SEMUA
fungsi di sini gagal secara GRACEFUL (get() -> None, set() -> no-op) --
Postgres (services/db.py) TETAP jadi sumber kebenaran, Redis cuma
optimisasi kecepatan tambahan, BUKAN dependency wajib.
"""
import json
import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_REDIS_HOST = os.getenv("REDIS_CACHE_HOST", "localhost")
_REDIS_PORT = int(os.getenv("REDIS_CACHE_PORT", "6378"))
_REDIS_DB = int(os.getenv("REDIS_CACHE_DB", "1"))  # DB 1 -- BUKAN 0 (Celery pakai DB 0)
_TTL_SEC = int(os.getenv("REDIS_CACHE_TTL_SEC", str(3600)))  # 1 jam

_KEY_PREFIX = "cashline-ntb-asscend:"

_client = None
_client_init_failed = False


def _get_client():
    """Lazy singleton -- coba connect SEKALI. Kalau gagal, JANGAN coba
    lagi tiap request (supaya Redis down tidak nambah latency di setiap
    request lewat timeout berulang -- cukup 1x gagal, lalu selalu
    fallback ke Postgres tanpa nunggu retry Redis lagi)."""
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None
    try:
        import redis
        client = redis.Redis(
            host=_REDIS_HOST, port=_REDIS_PORT, db=_REDIS_DB,
            socket_connect_timeout=2, socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        _client = client
        logger.info(
            "[redis-cache] Terhubung ke Redis %s:%d db=%d (TTL=%ds)",
            _REDIS_HOST, _REDIS_PORT, _REDIS_DB, _TTL_SEC,
        )
        return _client
    except Exception as e:
        logger.warning(
            "[redis-cache] Gagal connect Redis (%s:%d db=%d): %s -- "
            "lanjut TANPA Redis, semua request fallback ke Postgres saja.",
            _REDIS_HOST, _REDIS_PORT, _REDIS_DB, e,
        )
        _client_init_failed = True
        return None


def get(result_id: str) -> Optional[Dict[str, Any]]:
    """Baca dari Redis. Return None kalau cache miss ATAU Redis tidak
    tersedia -- caller HARUS fallback ke Postgres di kedua kasus ini,
    tidak bisa membedakan mana yang terjadi (dan memang tidak perlu)."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(_KEY_PREFIX + result_id)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("[redis-cache] Gagal baca result_id=%s: %s", result_id, e)
        return None


def set(result_id: str, data: Dict[str, Any]) -> None:
    """Simpan ke Redis dengan TTL 1 hari. Best-effort -- gagal diam-diam
    (di-log, TIDAK raise) supaya caller tidak pernah gagal gara-gara
    Redis, cuma gara-gara Postgres (yang memang sumber kebenaran)."""
    client = _get_client()
    if client is None:
        return
    try:
        client.setex(_KEY_PREFIX + result_id, _TTL_SEC, json.dumps(data))
    except Exception as e:
        logger.warning("[redis-cache] Gagal simpan result_id=%s: %s", result_id, e)