#!/usr/bin/env python3
"""
routes/recording_routes.py  (Aplikasi A)

Endpoint untuk mencari data dashboard.recording_tms_api dengan filter:
  - load_date = tanggal hari ini (default; bisa dioverride via query ?load_date=)
  - processed_at IS NOT NULL
  - context = 'cashline' (default; bisa dioverride via ?context=)
  - ORDER BY tiket_id

Mengikuti pola campaign_routes.py: FastAPI router + fungsi raw-SQL di services/db.py.
Router ini di-mount di root (sejajar /campaign/...), lihat catatan registrasi di
bawah file.
"""
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from services.db import (
    list_recording_tms_cashline, insert_transcript_processed, list_transcript_processed,
)

router = APIRouter()

_WIB = ZoneInfo("Asia/Jakarta")


@router.get("/recording/tms-cashline")
def get_recording_tms_cashline(
    load_date: Optional[date] = Query(
        None, description="Tanggal load_date (YYYY-MM-DD). Default: hari ini (WIB)."
    ),
    context: str = Query("cashline", description="Filter kolom context."),
):
    """List baris recording_tms_api untuk ``load_date`` (default hari ini WIB) yang
    sudah diproses (``processed_at IS NOT NULL``) dan ``context = 'cashline'``,
    diurutkan berdasarkan ``tiket_id``.

    Response::
        {
            "load_date": "2026-07-09",
            "context": "cashline",
            "count": <n>,
            "items": [ {..semua kolom..}, ... ],
            "processed_count": <m>,
            "processed": [ {..dashboard.transcript_processed kemarin s/d hari ini..}, ... ]
        }   
    """
    

    target = load_date or (datetime.now(_WIB).date() - timedelta(days=1))
    rows = list_recording_tms_cashline(target, context)
    # Catat ke dashboard.transcript_processed -- tiket yang sudah tercatat
    # di-skip (ON CONFLICT DO NOTHING); kegagalan tidak menggagalkan response.
    insert_transcript_processed(rows)
    processed = list_transcript_processed()
    return {
        "load_date": str(target),
        "context": context,
        "count": len(rows),
        "items": rows,
        "processed_count": len(processed),
        "processed": processed,
    }


# --- REGISTRASI (di main.py / tempat include_router Aplikasi A) -----------------
# from routes import recording_routes
# app.include_router(recording_routes.router)          # -> path: /recording/tms-cashline
# (samakan dengan cara campaign_routes.router di-include; campaign ada di root,
#  bukan di prefix /api/v1, jadi jangan beri prefix="/api/v1".)