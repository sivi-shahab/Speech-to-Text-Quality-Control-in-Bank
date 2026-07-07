#!/usr/bin/env python3
"""
services/db.py

Connection pool Postgres + query dynamic prompt (Mega Cashline).
Dipindah apa adanya dari speech_routes.py — tidak ada perubahan logic.
"""

import os
import logging
from typing import Optional, Any, Dict
from urllib.parse import urlparse, unquote

import psycopg2
from psycopg2 import pool
from psycopg2.extras import Json

logger = logging.getLogger(__name__)


def _init_db_pool() -> Optional["psycopg2.pool.ThreadedConnectionPool"]:
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL tidak ditemukan di environment variables.")

        clean_url  = database_url.replace("postgresql+psycopg2://", "postgresql://")
        parsed_url = urlparse(clean_url)
        db_pass    = unquote(parsed_url.password) if parsed_url.password else None

        pool_ = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=20,
            host=parsed_url.hostname,
            port=parsed_url.port,
            dbname=parsed_url.path.lstrip("/"),
            user=parsed_url.username,
            password=db_pass,
        )
        logger.info("Database Connection Pool berhasil diinisialisasi dari DATABASE_URL.")
        return pool_
    except Exception as e:
        logger.error("Gagal inisialisasi Database Pool: %s", e)
        return None


db_pool = _init_db_pool()


# Prompt Mega Cashline — dipindah verbatim, tidak diubah satu karakter pun,
# karena ini adalah instruksi domain-spesifik untuk STT initial_prompt.
_MEGA_CASHLINE_PROMPT = (
    "Ini adalah rekaman telepon outbound dari agen Bank Mega terhubung kepada nasabah. "
    "Agen memperkenalkan diri sebagai staf Bank Mega, bukan bank lain. "
    "Nama bank yang disebutkan adalah Bank Mega, bukan Bamiga, Bank Mika, POMIGA, Bank Media, Pamija, Bank Meja atau yang lainnya. "
    "Percakapan telemarketing Bank Mega menawarkan Mega Cashline. "
    "Agen melakukan konfirmasi kesediaan waktu CH sesuai regulasi PDP. "
    "Penawaran limit terundang dengan cicilan tenor 12, 24, dan 36 bulan. "
    "Bunga 1,75%, 2.09%, 2,20%, dan 2,50% perbulan dari effective rate. "
    "Biaya administrasi dan provisi 2%. "
    "Penawaran asuransi Mega Ultima Shield dari PFI Megalife Insurance dengan premi 0,68% dari tagihan terhutang. "
    "Agen memverifikasi data nasabah. Nomor handphone dicatat dengan format standar seperti 0812-0000-0000. "
    "Nomor NIK KTP dicatat 16 digit angka berurutan seperti 3170000000000000. "
    "Nomor NPWP dicatat dengan format titik dan strip, contohnya 12.345.678.9-012.000. "
    "Alamat dicatat lengkap beserta RT, RW, Kelurahan, dan kode pos. "
    "Alamat email ditulis menggunakan simbol titik dan @, "
    "merubah ejaan verbal menjadi simbol: "
    "kata 'dot' atau 'titik' menjadi '.', kata 'at' atau 'et' menjadi '@', "
    "kata 'underscore' atau 'garis bawah' menjadi '_', "
    "kata 'dash' atau 'strip' menjadi '-'. "
    "Alamat email contohnya: nama.nasabah@gmail.com. "
    "Penjelasan mengenai Health Declaration, pengiriman RIPLAY, dan informasi claim. "
    "Pembayaran via Joint Or atau Virtual Account ke BCA, Mandiri, BSI, BRI, Bank Mega, Bank Mega Syariah, atau BNI. "
    "Tanggal cetak 16 atau 25. Nasabah mengucapkan setuju."
    "Bank Mega, kartu kredit, Mega Ultimate Shield, asuransi, cicilan, limit, OJK, nasabah"
)


def get_dynamic_prompt_from_db(ticket_id: str) -> Optional[str]:
    if db_pool is None:
        return None

    conn = None
    try:
        conn = db_pool.getconn()
        if conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT context, campaign
                    FROM dashboard.recording_tms_api
                    WHERE tiket_id = %s 
                      AND DATE(load_date) = CURRENT_DATE - INTERVAL '1 day'
                      AND processed_at IS NULL
                    LIMIT 1;
                """
                cursor.execute(query, (ticket_id,))
                result = cursor.fetchone()

                if result:
                    context_val  = str(result[0] or "").lower()
                    campaign_val = str(result[1] or "").lower()

                    if "cashline" in context_val or "cashline" in campaign_val:
                        logger.info("[PROMPT] Mega Cashline terdeteksi, tiket: %s", ticket_id)
                        return _MEGA_CASHLINE_PROMPT
                return None
    except Exception as e:
        logger.error("[DB ERROR] Gagal query tiket %s: %s", ticket_id, e)
        return None
    finally:
        if conn:
            db_pool.putconn(conn)


def save_stt_pipeline_results(
    job_id:                     str,
    audio_file:                 str,
    asr_backend:                str,
    asr_raw_response:           Optional[Dict[str, Any]] = None,
    gender_detection_result:    Optional[Dict[str, Any]] = None,
    emotion_detection_result:   Optional[Dict[str, Any]] = None,
) -> bool:
    """
    [NEW] Simpan hasil mentah pipeline ke dashboard.result_speech_to_text.

    Dipanggil untuk SETIAP job, semua ASR backend:
      - asr_raw_response HANYA terisi kalau asr_backend == "elevenlabs",
        NULL untuk backend lain (whisper, dst) — caller (pipeline.py)
        cukup teruskan apa adanya dari metadata (sudah None otomatis
        untuk non-elevenlabs, lihat engine.py).
      - gender_detection_result & emotion_detection_result SELALU
        diteruskan apa adanya (generik untuk semua backend).

    Pakai UPSERT (ON CONFLICT DO UPDATE) berdasarkan job_id supaya aman
    kalau job yang sama diproses ulang (retry/reprocess) — tidak gagal
    duplicate-key error, baris lama ditimpa hasil terbaru.

    Return True kalau berhasil, False kalau gagal (sudah di-log,
    caller tidak perlu raise/retry — kegagalan simpan TIDAK boleh
    menggagalkan seluruh job; PDF/webhook tetap harus jalan).
    """
    if db_pool is None:
        logger.warning(
            "[DB] db_pool tidak tersedia, skip simpan hasil pipeline untuk job %s", job_id)
        return False

    conn = None
    try:
        conn = db_pool.getconn()
        if not conn:
            return False
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dashboard.result_speech_to_text
                    (job_id, audio_file, asr_backend, asr_raw_response,
                     gender_detection_result, emotion_detection_result)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO UPDATE SET
                    audio_file               = EXCLUDED.audio_file,
                    asr_backend              = EXCLUDED.asr_backend,
                    asr_raw_response         = EXCLUDED.asr_raw_response,
                    gender_detection_result  = EXCLUDED.gender_detection_result,
                    emotion_detection_result = EXCLUDED.emotion_detection_result,
                    created_at               = now();
                """,
                (
                    job_id, audio_file, asr_backend,
                    Json(asr_raw_response) if asr_raw_response is not None else None,
                    Json(gender_detection_result) if gender_detection_result is not None else None,
                    Json(emotion_detection_result) if emotion_detection_result is not None else None,
                ),
            )
        conn.commit()
        logger.info("[DB] Hasil pipeline (%s) tersimpan untuk job %s", asr_backend, job_id)
        return True
    except Exception as e:
        logger.error("[DB ERROR] Gagal simpan hasil pipeline job %s: %s", job_id, e)
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            db_pool.putconn(conn)
