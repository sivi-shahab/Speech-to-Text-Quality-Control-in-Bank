#!/usr/bin/env python3
"""
routes/campaign_routes.py

Endpoint untuk ambil data campaign (dashboard.campaign_cashline_ntb) +
data customer terkait (dashboard.current_cc_scmcustp) — dipanggil
BERURUTAN karena query kedua butuh field "no-ktpkitas" dari hasil query
pertama sebagai kunci pencarian.

[NEW] 2 lapis cache untuk data terfilter (48 field):
  L1 Redis     -- cepat, TTL 1 hari, best-effort (lihat services/redis_cache.py)
  L2 Postgres  -- permanen, dashboard."cashline-ntb-asscend" (lihat services/db.py)

Alur baca (/campaign/cashline-ntb-asscend/{result_id}):
  1. Coba Redis dulu -> hit: langsung balikkan (PALING cepat)
  2. Redis miss -> coba Postgres -> hit: populate Redis, balikkan
  3. Keduanya miss -> 404 (caller disarankan panggil endpoint ASLI)

Alur tulis (/campaign/cashline-ntb/{result_id}, endpoint ASLI):
  Setelah ambil data dari DWH -> simpan ke Postgres (L2) DAN Redis (L1)
  sekaligus, supaya permintaan BERIKUTNYA (lewat endpoint cache) langsung
  cepat dari hit pertama.

Terpisah dari speech_routes.py karena ini bukan soal STT — concern
berbeda (data campaign/customer), konsisten dengan pola modular yang
sudah dipakai untuk diagnostics_routes.py/webhook_callback_routes.py.
"""
import logging
from fastapi import APIRouter, HTTPException
from services.db import (
    get_campaign_cashline_ntb,
    get_current_cc_scmcustp,
    save_cashline_ntb_asscend_cache,
    get_cashline_ntb_asscend_cache,
)
from services import redis_cache

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/campaign/cashline-ntb/{result_id}")
async def get_cashline_ntb_with_customer(result_id: str):
    """
    Ambil data campaign berdasarkan result_id, lalu pakai field
    "no-ktpkitas" dari hasil itu untuk ambil data customer terkait.

    [NEW] Setelah berhasil ambil data, otomatis simpan versi terfilter
    (48 field) ke Postgres (L2) DAN Redis (L1) sekaligus -- kegagalan
    simpan cache (salah satu/keduanya) TIDAK menggagalkan response ini
    (cuma di-log).

    Response:
        {
            "cashline": {...semua kolom campaign_cashline_ntb...},
            "customer": {...semua kolom current_cc_scmcustp...} atau null
                        kalau no-ktpkitas kosong / tidak ketemu di tabel customer
        }
    """
    campaign_data = get_campaign_cashline_ntb(result_id)
    if campaign_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Data campaign tidak ditemukan untuk result_id={result_id}",
        )
    no_ktpkitas = campaign_data.get("no-ktpkitas")
    customer_data = None
    if no_ktpkitas:
        customer_data = get_current_cc_scmcustp(no_ktpkitas)
        if customer_data is None:
            logger.warning(
                "[campaign-cashline-ntb] result_id=%s punya no-ktpkitas=%s "
                "tapi tidak ketemu di current_cc_scmcustp",
                result_id, no_ktpkitas,
            )
    else:
        logger.warning(
            "[campaign-cashline-ntb] result_id=%s tidak punya no-ktpkitas, "
            "skip lookup customer", result_id,
        )

    # [NEW] Simpan ke Postgres (L2) -- best-effort, TIDAK boleh gagalkan response utama.
    filtered = None
    try:
        filtered = save_cashline_ntb_asscend_cache(result_id, campaign_data, customer_data)
        if filtered is None:
            logger.warning("[campaign-cashline-ntb] gagal simpan cache Postgres untuk %s", result_id)
    except Exception as e:
        logger.error("[campaign-cashline-ntb] exception saat simpan cache Postgres untuk %s: %s", result_id, e)

    # [UBAH] Populate Redis (L1) saja -- MinIO backup DIHAPUS dari alur ini
    # (cukup 2 lapis: Postgres L2 + Redis L1). Pakai hasil TERFILTER dari
    # save_cashline_ntb_asscend_cache() di atas, TIDAK query ulang Postgres.
    if filtered is not None:
        try:
            redis_cache.set(result_id, filtered)
        except Exception as e:
            logger.warning("[campaign-cashline-ntb] gagal populate Redis untuk %s: %s", result_id, e)

    return {"cashline": campaign_data, "customer": customer_data}


@router.get("/campaign/cashline-ntb-asscend/{result_id}")
async def get_cashline_ntb_asscend_cached(result_id: str):
    """
    [NEW] Baca data dari CACHE (48 field terpilih), TANPA panggil DWH API
    sama sekali. 2 lapis:
      1. Redis (L1) -- kalau hit, ini yang PALING cepat (dalam memori).
      2. Postgres (L2) -- kalau Redis miss, coba tabel
         dashboard."cashline-ntb-asscend". Kalau hit, sekaligus populate
         Redis supaya permintaan berikutnya untuk result_id yang sama
         langsung kena L1.

    404 kalau KEDUA lapis cache kosong -- caller disarankan fallback ke
    /campaign/cashline-ntb/{result_id} (endpoint asli) kalau ini terjadi.

    Response:
        {
            "cashline": {...30 field terpilih...} atau null,
            "customer": {...18 field terpilih...} atau null,
            "cached_at": "2026-07-20T12:34:56+00:00" (null kalau dari Redis)
        }
    """
    # --- L1: Redis ---
    redis_hit = redis_cache.get(result_id)
    if redis_hit is not None:
        return redis_hit

    # --- L2: Postgres ---
    pg_hit = get_cashline_ntb_asscend_cache(result_id)
    if pg_hit is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Belum ada cache untuk result_id={result_id} -- "
                f"panggil /campaign/cashline-ntb/{result_id} dulu (endpoint asli) "
                "supaya cache-nya terisi."
            ),
        )

    # Postgres hit -> populate Redis supaya request berikutnya kena L1.
    try:
        redis_cache.set(result_id, pg_hit)
    except Exception as e:
        logger.warning("[campaign-cashline-ntb-asscend] gagal populate Redis dari Postgres hit untuk %s: %s", result_id, e)

    return pg_hit


# --- REGISTRASI (di main.py / tempat include_router Aplikasi A) -----------------
# from routes import campaign_routes
# app.include_router(campaign_routes.router)   # -> /campaign/cashline-ntb/{result_id}
#                                                  /campaign/cashline-ntb-asscend/{result_id}