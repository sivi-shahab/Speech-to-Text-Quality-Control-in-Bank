#!/usr/bin/env python3
# =================================================================
# lightweight_server.py  —  App A "ringan" (terpisah dari STT)
#
# LATAR BELAKANG: server.py (App A utama) WAJIB jalan dengan
# `gunicorn -w 1` (satu proses) karena model STT di-load sekali ke
# GPU dan dipakai bersama semua thread -- kalau workers > 1, tiap
# proses akan load model sendiri-sendiri dan bikin GPU OOM.
#
# MASALAHNYA: /campaign/cashline-ntb/{result_id} dan
# /recording/tms-cashline TIDAK ADA HUBUNGANNYA dengan model
# STT/GPU sama sekali -- keduanya cuma query PostgreSQL biasa lewat
# services/db.py. Tapi karena nebeng di app yang sama (1 worker),
# request ke endpoint ringan ini HARUS ANTRE di belakang thread yang
# sedang sibuk memproses transkripsi audio (bisa puluhan detik-menit
# per job) -- inilah penyebab timeout "Read timed out (10.0)"/
# "Read timed out (30.0)" yang berulang kali muncul saat App B
# (dashboard/list_results) memanggil endpoint ini.
#
# SOLUSI: pindahkan KEDUA endpoint ini ke proses FastAPI TERPISAH,
# yang TIDAK import apa pun yang menyentuh torch/CUDA/model STT --
# jadi bisa jalan dengan BANYAK worker (tidak ada risiko GPU OOM,
# karena tidak ada model yang di-load sama sekali di sini), dan
# TIDAK PERNAH lagi antre di belakang proses transkripsi.
#
# routes/campaign_routes.py dan routes/recording_routes.py cuma
# depend ke services/db.py (murni psycopg2, tanpa torch) -- jadi
# aman dipindah ke sini tanpa menyentuh app.py/server.py utama sama
# sekali (App A utama TIDAK PERLU diubah/di-redeploy untuk ini).
# =================================================================



import os
import sys
import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import campaign_routes, recording_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lightweight_server")

PORT = int(os.getenv("LIGHTWEIGHT_PORT", "8002"))

logger.info("Lightweight Server (campaign/recording endpoints only) | PORT=%d", PORT)

app = FastAPI(
    title       = "Speech STT API — Lightweight (campaign/recording)",
    version     = "1.0.0",
    description = (
        "Endpoint ringan (query DB murni, TANPA model STT/GPU) yang dipisah "
        "dari server.py utama supaya tidak antre di belakang proses "
        "transkripsi audio yang berat. Jalan dengan banyak worker karena "
        "tidak ada risiko GPU OOM di sini."
    ),
)

# =================================================================
# CORS
# ⚠️ TODO: samakan dengan CORS_ORIGINS yang dipakai server.py utama
# (lihat services/server_config.py) -- di sini ditulis manual/sederhana
# supaya file ini TIDAK PERLU import server_config sama sekali (yang
# menyentuh CUDA_VISIBLE_DEVICES dkk, tidak relevan untuk app ringan
# ini). Sesuaikan CORS_ORIGINS_LIGHTWEIGHT di .env sesuai kebutuhan.
# =================================================================
_cors_env = os.getenv("CORS_ORIGINS_LIGHTWEIGHT", "*")
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
CORS_HAS_WILDCARD = "*" in CORS_ORIGINS

if CORS_HAS_WILDCARD and os.getenv("DEPLOYMENT_MODE", "dev") == "prod":
    logger.critical(
        "CORS_ORIGINS_LIGHTWEIGHT='*' tidak diizinkan di production. "
        "Set ke domain spesifik di .env"
    )
    sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = not CORS_HAS_WILDCARD,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# =================================================================
# Route Registration — HANYA 2 router ini, TIDAK ADA speech_routes
# (yang butuh model STT) di app ini sama sekali.
# =================================================================
app.include_router(campaign_routes.router, tags=["campaign"])
app.include_router(recording_routes.router, tags=["recording"])


@app.get("/health")
def health():
    """Health check sederhana -- tidak ada model STT untuk dicek di sini."""
    return {"status": "ok", "service": "lightweight_server"}


# =================================================================
# Entry Point — DEV only
# Production: gunicorn lightweight_server:app -k uvicorn.workers.UvicornWorker -w 4
#             (BOLEH > 1 worker -- beda dari server.py utama yang WAJIB -w 1)
# =================================================================
if __name__ == "__main__":
    import uvicorn

    print("\n%s" % ("=" * 65))
    print("  LIGHTWEIGHT SERVER — campaign & recording endpoints | PORT %d" % PORT)
    print("=" * 65)
    print("  Endpoint  : GET /campaign/cashline-ntb/{result_id}")
    print("              GET /recording/tms-cashline")
    print("  GPU/Model : TIDAK ADA -- boleh banyak worker")
    print()
    print("  DEPLOYMENT COMMANDS:")
    print("    Dev  : python lightweight_server.py")
    print("    Prod : gunicorn lightweight_server:app \\")
    print("             -k uvicorn.workers.UvicornWorker \\")
    print("             -w 4 \\")
    print("             --bind 0.0.0.0:%d" % PORT)
    print("=" * 65)

    uvicorn.run(
        "lightweight_server:app",
        host      = "0.0.0.0",
        port      = PORT,
        workers   = 1,  # dev only; production pakai gunicorn -w 4 (lihat atas)
        log_level = "info",
    )
