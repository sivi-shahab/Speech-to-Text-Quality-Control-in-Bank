#!/usr/bin/env python3
"""
routes/campaign_routes.py

Endpoint untuk ambil data campaign (dashboard.campaign_cashline_ntb) +
data customer terkait (dashboard.current_cc_scmcustp) — dipanggil
BERURUTAN karena query kedua butuh field "no-ktpkitas" dari hasil query
pertama sebagai kunci pencarian.

Terpisah dari speech_routes.py karena ini bukan soal STT — concern
berbeda (data campaign/customer), konsisten dengan pola modular yang
sudah dipakai untuk diagnostics_routes.py/webhook_callback_routes.py.
"""

import logging

from fastapi import APIRouter, HTTPException

from services.db import get_campaign_cashline_ntb, get_current_cc_scmcustp

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/campaign/cashline-ntb/{result_id}")
async def get_cashline_ntb_with_customer(result_id: str):
    """
    Ambil data campaign berdasarkan result_id, lalu pakai field
    "no-ktpkitas" dari hasil itu untuk ambil data customer terkait.

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

    return {"cashline": campaign_data, "customer": customer_data}