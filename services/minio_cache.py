#!/usr/bin/env python3
"""
services/minio_cache.py

Backup/mirror JSON hasil cashline-ntb-asscend ke MinIO -- MURNI untuk
backup/durability tambahan, BUKAN bagian dari alur baca. Alur baca TETAP
Redis (L1) -> Postgres (L2) saja (lebih cepat) -- MinIO TIDAK PERNAH
dicek saat request masuk, cuma DITULIS saat data baru didapat dari DWH.

Pakai instance MinIO YANG SAMA dengan App B (port 4003, API), exposed ke
host lewat docker-compose "ports: 4003:4003". Bucket TERPISAH
("cashline-ntb-asscend") dari bucket App B (transcripts/results/audio)
supaya tidak bercampur dengan data App B.

⚠️ Sama seperti redis_cache.py: SEPENUHNYA best-effort. Kalau MinIO tidak
tersedia/gagal, TIDAK BOLEH menggagalkan endpoint utama -- Postgres tetap
sumber kebenaran satu-satunya untuk pembacaan.
"""
import io
import json
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)

_MINIO_ENDPOINT = os.getenv("MINIO_CACHE_ENDPOINT", "localhost:4003")
_MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
_MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
_MINIO_BUCKET = os.getenv("MINIO_CACHE_BUCKET", "cashline-ntb-asscend")
_MINIO_SECURE = os.getenv("MINIO_CACHE_SECURE", "false").lower() == "true"

_client = None
_client_init_failed = False
_bucket_checked = False


def _get_client():
    """Lazy singleton -- coba connect + pastikan bucket ada SEKALI saja.
    Kalau gagal, jangan coba lagi tiap request (sama seperti pola
    redis_cache.py)."""
    global _client, _client_init_failed, _bucket_checked
    if _client is not None:
        return _client
    if _client_init_failed:
        return None

    if not _MINIO_ACCESS_KEY or not _MINIO_SECRET_KEY:
        logger.warning(
            "[minio-cache] MINIO_ACCESS_KEY/MINIO_SECRET_KEY belum di-set -- "
            "skip backup MinIO sepenuhnya (Postgres tetap jalan normal)."
        )
        _client_init_failed = True
        return None

    try:
        from minio import Minio
        client = Minio(
            _MINIO_ENDPOINT,
            access_key=_MINIO_ACCESS_KEY,
            secret_key=_MINIO_SECRET_KEY,
            secure=_MINIO_SECURE,
        )
        if not _bucket_checked:
            if not client.bucket_exists(_MINIO_BUCKET):
                client.make_bucket(_MINIO_BUCKET)
                logger.info("[minio-cache] Bucket '%s' belum ada, berhasil dibuat", _MINIO_BUCKET)
            _bucket_checked = True
        _client = client
        logger.info(
            "[minio-cache] Terhubung ke MinIO %s, bucket='%s'",
            _MINIO_ENDPOINT, _MINIO_BUCKET,
        )
        return _client
    except Exception as e:
        logger.warning(
            "[minio-cache] Gagal connect/setup MinIO (%s, bucket=%s): %s -- "
            "lanjut TANPA backup MinIO.",
            _MINIO_ENDPOINT, _MINIO_BUCKET, e,
        )
        _client_init_failed = True
        return None


def save(result_id: str, data: Dict[str, Any]) -> bool:
    """Simpan (backup/mirror) ke MinIO sebagai object '{result_id}.json'.
    Best-effort -- gagal diam-diam (di-log warning), TIDAK PERNAH raise.

    Return True/False HANYA untuk keperluan logging caller -- TIDAK
    dipakai untuk keputusan alur apa pun (alur baca tidak pernah cek
    MinIO, jadi return value ini murni informational).
    """
    client = _get_client()
    if client is None:
        return False
    try:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        client.put_object(
            _MINIO_BUCKET,
            f"{result_id}.json",
            io.BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )
        return True
    except Exception as e:
        logger.warning("[minio-cache] Gagal simpan backup result_id=%s: %s", result_id, e)
        return False
