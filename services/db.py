#!/usr/bin/env python3
"""
services/db.py

Connection pool Postgres + query dynamic prompt (Mega Cashline).
Dipindah apa adanya dari speech_routes.py — tidak ada perubahan logic.

[FIX] Ditambahkan: TCP keepalive pada pool + validasi koneksi ("SELECT 1")
sebelum dipakai untuk query sungguhan. psycopg2.pool.ThreadedConnectionPool
TIDAK otomatis test koneksi sebelum dikeluarkan (beda dari SQLAlchemy
pool_pre_ping=True) -- koneksi basi (mis. kena SSL EOF akibat firewall/
load-balancer motong koneksi idle) bisa lolos dari pool dan baru ketahuan
gagal saat dipakai, menyebabkan "SSL SYSCALL error: EOF detected".
"""

import os
import logging
from typing import Optional, Any, Dict
from urllib.parse import urlparse, unquote

import psycopg2
from psycopg2 import pool
from psycopg2.extras import Json, execute_values

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
            # [FIX] TCP keepalive -- deteksi koneksi mati lebih cepat, dan
            # cegah firewall/load-balancer di tengah jalan motong koneksi
            # yang dianggap "idle" terlalu lama.
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        logger.info("Database Connection Pool berhasil diinisialisasi dari DATABASE_URL.")
        return pool_
    except Exception as e:
        logger.error("Gagal inisialisasi Database Pool: %s", e)
        return None


db_pool = _init_db_pool()


def _get_working_conn(max_retries: int = 2):
    """Ambil koneksi dari db_pool, VALIDASI dulu dengan query trivial
    (SELECT 1) sebelum dikembalikan ke caller untuk dipakai query
    sungguhan -- ini yang psycopg2.pool.ThreadedConnectionPool TIDAK
    lakukan secara default (lihat catatan di modul).

    Kalau koneksi ternyata basi/putus, DIBUANG dari pool (bukan
    dikembalikan begitu saja -- pakai putconn(conn, close=True) supaya
    psycopg2 benar-benar tutup & tidak coba re-pakai koneksi rusak itu),
    lalu ambil koneksi baru dan ulangi sampai `max_retries` kali.

    Return None kalau db_pool tidak tersedia ATAU semua percobaan gagal
    (caller HARUS handle None sama seperti sebelumnya -- fallback ke
    hasil kosong, jangan sampai ini menggagalkan seluruh job/request).
    """
    if db_pool is None:
        return None
    last_err = None
    for attempt in range(max_retries + 1):
        conn = db_pool.getconn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
            return conn  # koneksi sehat, siap dipakai untuk query sungguhan
        except Exception as e:
            last_err = e
            logger.warning(
                "[DB] Koneksi basi terdeteksi (percobaan %d/%d): %s -- buang & ambil baru.",
                attempt + 1, max_retries + 1, e,
            )
            try:
                db_pool.putconn(conn, close=True)
            except Exception:
                pass
    logger.error(
        "[DB] Gagal dapat koneksi sehat setelah %d percobaan: %s",
        max_retries + 1, last_err,
    )
    return None


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


# def get_dynamic_prompt_from_db(ticket_id: str) -> Optional[str]:
#     if db_pool is None:
#         return None

#     conn = None
#     try:
#         conn = db_pool.getconn()
#         if conn:
#             with conn.cursor() as cursor:
#                 query = """
#                     SELECT context, campaign
#                     FROM dashboard.recording_tms_api
#                     WHERE tiket_id = %s 
#                     AND DATE(load_date) = CURRENT_DATE - INTERVAL '1 day'
#                     AND processed_at IS NULL
#                     LIMIT 1;
#                 """
#                 cursor.execute(query, (ticket_id,))
#                 result = cursor.fetchone()

#                 if result:
#                     context_val  = str(result[0] or "").lower()
#                     campaign_val = str(result[1] or "").lower()

#                     if "cashline" in context_val or "cashline" in campaign_val:
#                         logger.info("[PROMPT] Mega Cashline terdeteksi, tiket: %s", ticket_id)
#                         return _MEGA_CASHLINE_PROMPT
#                 return None
#     except Exception as e:
#         logger.error("[DB ERROR] Gagal query tiket %s: %s", ticket_id, e)
#         return None
#     finally:
#         if conn:
#             db_pool.putconn(conn)
            
# Testing            
def get_dynamic_prompt_from_db(ticket_id: str) -> Optional[str]:
    """[TESTING] processed_at IS NOT NULL + INTERVAL '4 day' -- HARUS
    dikembalikan ke IS NULL + INTERVAL '1 day' sebelum production (lihat
    catatan di modul)."""
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
                    AND processed_at IS NULL;
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


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    """Map row hasil query ke dict {nama_kolom: value} otomatis dari
    cursor.description — supaya tidak perlu tulis nama kolom manual
    satu-satu di Python (tabel ini punya 70+ kolom)."""
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def get_campaign_cashline_ntb(result_id: str) -> Optional[Dict[str, Any]]:
    """
    [NEW] Ambil 1 baris data campaign dari dashboard.campaign_cashline_ntb
    berdasarkan result_id. Nama kolom yang mengandung tanda "-" (mis.
    "no-ktpkitas", "alamat-pengiriman") WAJIB diapit petik ganda di
    PostgreSQL — beda dari MySQL yang pakai backtick.

    [FIX] Pakai _get_working_conn() (bukan db_pool.getconn() langsung) --
    koneksi divalidasi dulu (SELECT 1) sebelum dipakai, dengan retry
    otomatis kalau koneksi ternyata basi. Fungsi ini dipanggil oleh endpoint
    /campaign/cashline-ntb/{result_id} yang mengalami "Read timed out"
    (10s) dari App B -- koneksi basi yang HANG lama sebelum gagal adalah
    kandidat kuat penyebabnya.
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk get_campaign_cashline_ntb")
        return None

    conn = _get_working_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cursor:
            query = """
                SELECT
                    result_id, prospect_id, customer_id,
                    CASE
                        WHEN POSITION('_' IN cust_name) > 0
                        THEN LEFT(cust_name, POSITION('_' IN cust_name) - 1)
                        ELSE cust_name
                    END AS cust_name,
                    agent_id, submit_time, qc_id, approve_time, turli, segmen,
                    "alamat-pengiriman", "send-mail-to", "send-message-to",
                    "no-npwp-new", "no-telp-rumah-new", "no-telp-kantor-new",
                    "nik-new", "e-statement-new", "alamat-email-new",
                    "kirim-pesan-melalui", "kantor-rt-new", "kantor-rw-new",
                    "kantor-provinsi-new", "kantor-kabupatenkota-new",
                    "kantor-kecamatan-new", "kantor-kelurahan-new",
                    "kantor-kode-pos-new", "nama-perusahaan-new",
                    "alamat-rumah-1-new", "rumah-rt-new", "rumah-rw-new",
                    "rumah-provinsi-new", "rumah-kabupatenkota-new",
                    "rumah-kecamatan-new", "rumah-kelurahan-new",
                    "rumah-kode-pos-new", "alamat-kantor-1-new",
                    "alamat-rumah-2-new", "alamat-kantor-2-new",
                    "checlist-jika-ada-perubahan", "source-code",
                    "jenis-kartu-yang-dikehendaki", "max-transfer",
                    "nominal-transfer", tenor, "cicilan-per-bulan",
                    "plan-code", "nama-bank", "nomor-rekening",
                    "nama-di-rekening", "admin-fee", "biaya-transfer",
                    "alamat-rumah-chk", "alamat-kantor-chk",
                    "alamat-pengiriman-kartu-chk", "no-npwp-chk",
                    "no-telp-rumah-chk", "no-telp-kantor-chk", "nik-chk",
                    "e-statement-chk", "alamat-email-chk", "send-message",
                    pekerjaan, "bidang-usaha", jabatan, "no-ktpkitas",
                    "pendaftaran-credit-shield", privy,
                    "tanpa-perubahan-data-ntb-chk", grab, enroll, finance,
                    status, los, "cld-amount"
                FROM dashboard.campaign_cashline_ntb
                WHERE result_id = %s
                LIMIT 1;
            """
            cursor.execute(query, (result_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_dict(cursor, row)
    except Exception as e:
        logger.error("[DB ERROR] Gagal query campaign_cashline_ntb result_id=%s: %s", result_id, e)
        return None
    finally:
        if conn:
            db_pool.putconn(conn)


def get_current_cc_scmcustp(cust_id_nbr: str) -> Optional[Dict[str, Any]]:
    """
    [NEW] Ambil 1 baris data customer dari dashboard.current_cc_scmcustp
    berdasarkan CUST_ID_NBR. Nilai cust_id_nbr didapat dari field
    "no-ktpkitas" hasil get_campaign_cashline_ntb() — dipanggil BERURUTAN
    (bukan paralel) dari route, karena query ini butuh hasil query
    pertama dulu.

    [FIX] Pakai _get_working_conn() (bukan db_pool.getconn() langsung) --
    sama seperti get_campaign_cashline_ntb(), lihat catatan di sana.
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk get_current_cc_scmcustp")
        return None
    if not cust_id_nbr:
        return None

    conn = _get_working_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cursor:
            query = """
                SELECT
                    "CUST_NBR", "CUST_OPER_CODE", "CUST_TYPE", "CUST_LOCAL_NAME",
                    "CUST_ADDR1", "CUST_ADDR2", "CUST_ADD_CITY", "CUST_ADD_PROVINCE",
                    "CUST_ADD_ZIPCODE", "CUST_PHONE", "CUST_RES_TYPE",
                    "CUST_RES_PERIOD", "CUST_SEX", "CUST_MARITAL_ST",
                    "CUST_QUALIFICATION", "CUST_ID_NBR", "CUST_DTE_BIRTH",
                    "CUST_MOM_NAME", "CUST_WORK_ID", "CUST_NATIONALITY",
                    "CUST_SUPP_RLNSHIP", "CUST_EMP_NAME", "CUST_EMP_ADDR1",
                    "CUST_EMP_ADDR2", "CUST_EMP_ADDR3", "CUST_EMP_ADDR4",
                    "CUST_EMP_CITY", "CUST_EMP_ZIP", "CUST_OCC_CODE",
                    "CUST_OCC_PER", "CUST_EMP_PHONE", "CUST_ANN_SALARY",
                    "CUST_OTH_INCOME", "CUST_GRLNSHIP", "CUST_GLOCAL_NAME",
                    "CUST_GENG_NAME", "CUST_GADDR1", "CUST_GADDR2",
                    "CUST_GADDR3", "CUST_GADDR4", "CUST_GADD_CITY",
                    "CUST_GADD_PROVINCE", "CUST_GADD_ZIPCODE", "CUST_GPHONE",
                    "CUST_GSEX", "CUST_GMARITAL_ST", "CUST_GQUALIFICATION",
                    "CUST_GID_NBR", "CUST_PLC_BIRTH", "CUST_GDTE_BIRTH",
                    "CUST_GWORK_ID", "CUST_GEMP_NAME", "CUST_GEMP_ADDR1",
                    "CUST_GEMP_ADDR2", "CUST_GEMP_ZIP", "CUST_GOCC_CODE",
                    "CUST_GOCC_PER", "CUST_GEMP_PHONE", "CUST_GANN_SALARY",
                    "CUST_MADDR1", "CUST_MADDR2", "CUST_MADD_CITY",
                    "CUST_MADD_PROVINCE", "CUST_MADD_ZIPCODE", "CUST_CNAME",
                    "CUST_CORP_EMBOSS_NAME", "CUST_CPHONE", "CUST_CREG_DATE",
                    "CUST_CREG_NBR", "CUST_CBUSINESS", "CUST_CBANK",
                    "CUST_CBANK_ACCT", "CUST_CCONTACT", "CUST_COLLECTOR",
                    "CUST_MEMO", "CUST_CRLIMIT", "CUST_AVAIL_CREDIT",
                    "CUST_CASH_LIMIT", "CUST_AVAIL_CASH", "CUST_TEMP_CRLIMIT",
                    "CUST_PERM_CRLIMIT", "CUST_DATE_TEMP_CRLIMIT_EFF",
                    "CUST_DATE_TEMP_CRLIMIT_EXP", "CUST_CURR_CODE",
                    "CUST_ADDR3", "CUST_ADDR4", "CUST_MADDR3", "CUST_MADDR4",
                    "CUST_EMAIL_ADDR", "CUST_MOBILE_PHONE", "CUST_ADDR_CODE",
                    "CUST_FILE_BUFFER", "CUST_INSTL_LIMIT", "CUST_AVAIL_INSTL",
                    "CUST_STDALN_INSTL", "CUST_AVAIL_STDALN_INS", "CUST_NODE_ID",
                    "CUST_FAMILY_SIZE", "CUST_SPOUSE_NAME", "CUST_TAX_ID",
                    "CUST_DIN", "CUST_TITLE", "CUST_CR_CARD1",
                    "CUST_CR_CARD_LMT1", "CUST_CR_CARD2", "CUST_CR_CARD_LMT2",
                    "CUST_CIF", "CUST_HOME_OWNSHP", "CUST_DATE_MAINT",
                    "CUST_USER_MAINT", "CUST_TIME_MAINT", "CUST_FILE_DATE"
                FROM dashboard.current_cc_scmcustp
                WHERE TRIM("CUST_ID_NBR") = %s
                LIMIT 1;
            """
            cursor.execute(query, (cust_id_nbr,))
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_dict(cursor, row)
    except Exception as e:
        logger.error("[DB ERROR] Gagal query current_cc_scmcustp cust_id_nbr=%s: %s", cust_id_nbr, e)
        return None
    finally:
        if conn:
            db_pool.putconn(conn)


# _RECORDING_TMS_SQL = """
#     SELECT tiket_id, file_path, julian_mis_date, mis_date, id, prospect_id,
#            customer_id, cust_name, campaign, agent_id, status, created_by,
#            created_time, created_date, recording_customer_id,
#            recording_created_time, a_number, context, source_api, load_date,
#            status_data, error_message, inserted_at, processed_at
#     FROM dashboard.recording_tms_api
#     WHERE load_date::date = %s
#       AND processed_at IS NOT NULL
#       AND context = %s AND campaign in ('CLENB', 'CLENTB') AND id = '161024b5HN'
#     ORDER BY tiket_id;
# """

# !!! TODO HAPUS SEBELUM PRODUCTION !!!
# Query ini HARDCODE cuma ambil id='010844SxBu' -- TIDAK ADA %s sama sekali,
# artinya TIDAK PEDULI load_date/context yang dikirim scheduler, selalu
# # balikkan baris ini saja (kalau ada).
# _RECORDING_TMS_SQL = """
#     SELECT tiket_id, file_path, julian_mis_date, mis_date, id, prospect_id,
#            customer_id, cust_name, campaign, agent_id, status, created_by,
#            created_time, created_date, recording_customer_id,
#            recording_created_time, a_number, context, source_api, load_date,
#            status_data, error_message, inserted_at, processed_at
#     FROM dashboard.recording_tms_api
#     WHERE id = '010844SxBu'
#     ORDER BY tiket_id;
# """




_RECORDING_TMS_SQL = """
    SELECT tiket_id, file_path, julian_mis_date, mis_date, id, prospect_id,
           customer_id, cust_name, campaign, agent_id, status, created_by,
           created_time, created_date, recording_customer_id,
           recording_created_time, a_number, context, source_api, load_date,
           status_data, error_message, inserted_at, processed_at
    FROM dashboard.recording_tms_api
    WHERE load_date::date BETWEEN CURRENT_DATE - 1 AND CURRENT_DATE
    AND context in ('CLENB', 'CLENTB')
    ORDER BY tiket_id;
"""



def list_recording_tms_cashline(load_date, context: str = "cashline") -> list[Dict[str, Any]]:
    """Baris dashboard.recording_tms_api untuk ``load_date`` (objek date) yang sudah
    diproses (``processed_at IS NOT NULL``) dan ber-``context`` tertentu, urut
    ``tiket_id``. Balik list of dict (kosong bila tak ada / pool tak tersedia).
    ``load_date::date`` dipakai agar aman baik kolomnya date maupun timestamp.

    [FIX] Pakai _get_working_conn() (bukan db_pool.getconn() langsung) --
    koneksi divalidasi dulu (SELECT 1) sebelum dipakai, dengan retry
    otomatis kalau koneksi ternyata basi. Ini fungsi yang paling sering
    kena "SSL SYSCALL error: EOF detected" di log produksi.
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk list_recording_tms_cashline")
        return []
    conn = _get_working_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cursor:
            # cursor.execute(_RECORDING_TMS_SQL, (load_date, context))
            cursor.execute(_RECORDING_TMS_SQL)
            rows = cursor.fetchall()
            return [_row_to_dict(cursor, row) for row in rows]
    except Exception as e:
        logger.error(
            "[DB ERROR] Gagal query recording_tms_api load_date=%s context=%s: %s",
            load_date, context, e,
        )
        return []
    finally:
        if conn:
            db_pool.putconn(conn)


_TRANSCRIPT_PROCESSED_COLUMNS = ["tiket_id", "file_path", "id", "campaign", "agent_id", "a_number"]


def insert_transcript_processed(rows: list[Dict[str, Any]]) -> int:
    """[NEW] Catat baris hasil list_recording_tms_cashline() ke
    dashboard.transcript_processed -- cuma kolom di
    _TRANSCRIPT_PROCESSED_COLUMNS, plus inserted_at = now() sebagai
    penanda kapan baris tersebut dicatat.

    tiket_id UNIQUE + ON CONFLICT DO NOTHING -- tiket yang sudah pernah
    tercatat di-skip, inserted_at tetap waktu pertama kali dicatat.

    Insert dalam 1 batch (execute_values) supaya tidak 1 round-trip per
    baris. Return jumlah baris yang BENAR-BENAR ter-insert (tidak
    termasuk yang di-skip karena duplikat; 0 kalau rows kosong /
    pool tak tersedia / gagal -- sudah di-log, caller tidak perlu raise).
    """
    if not rows:
        return 0
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk insert_transcript_processed")
        return 0

    values = [tuple(r.get(c) for c in _TRANSCRIPT_PROCESSED_COLUMNS) for r in rows]

    conn = _get_working_conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cursor:
            execute_values(
                cursor,
                """
                INSERT INTO dashboard.transcript_processed
                    (tiket_id, file_path, id, campaign, agent_id, a_number, inserted_at)
                VALUES %s
                ON CONFLICT (tiket_id) DO NOTHING;
                """,
                values,
                template="(%s, %s, %s, %s, %s, %s, now())",
                # 1 page saja -- rowcount execute_values cuma hitung page terakhir.
                page_size=len(values),
            )
            inserted = cursor.rowcount
        conn.commit()
        logger.info("[DB] %d baris tercatat di transcript_processed", inserted)
        return inserted
    except Exception as e:
        logger.error("[DB ERROR] Gagal insert transcript_processed (%d baris): %s", len(rows), e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        if conn:
            db_pool.putconn(conn)


def list_transcript_processed() -> list[Dict[str, Any]]:
    """[NEW] Baris dashboard.transcript_processed yang sudah tercatat
    (``inserted_at IS NOT NULL``) kemarin s/d hari ini, urut ``tiket_id``. Balik list of dict
    (kosong bila tak ada / pool tak tersedia / gagal -- sudah di-log).
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk list_transcript_processed")
        return []
    conn = _get_working_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT tiket_id, file_path, id, campaign, agent_id, a_number, inserted_at
                FROM dashboard.transcript_processed
                WHERE inserted_at IS NOT NULL
                  AND inserted_at::date BETWEEN CURRENT_DATE - 1 AND CURRENT_DATE
                ORDER BY tiket_id;
                """
            )
            rows = cursor.fetchall()
            return [_row_to_dict(cursor, row) for row in rows]
    except Exception as e:
        logger.error("[DB ERROR] Gagal query transcript_processed: %s", e)
        return []
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

    Pakai UPSERT (ON CONFLICT DO UPDATE) berdasarkan job_id supaya aman
    kalau job yang sama diproses ulang (retry/reprocess).

    Return True kalau berhasil, False kalau gagal (sudah di-log,
    caller tidak perlu raise/retry).
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
            db_pool.putconn(conn)# ---------------------------------------------------------------------------
# [NEW] Cache cashline+customer (Ascend) -- cuma 50 field yang App B pakai
# (32 cashline + 18 customer), disimpan sebagai JSONB di
# dashboard."cashline-ntb-asscend". Diisi setiap kali
# /campaign/cashline-ntb/{result_id} dipanggil (upsert), dibaca lewat
# endpoint terpisah /campaign/cashline-ntb-asscend/{result_id} untuk
# akses cepat tanpa panggil DWH API lagi.
# ---------------------------------------------------------------------------

# 32 field cashline yang benar-benar dipakai App B (reference_data.py,
# crud.get_tms_cashline_change_flags, routers/agent_error.py). Field DI LUAR
# daftar ini SENGAJA tidak disimpan -- kalau nanti App B butuh field baru,
# tambahkan di sini.
_ASSCEND_CASHLINE_FIELDS = [
    "cust_name",
    # Agent Error Summary (App B routers/agent_error.py + api/sales_lookup.py):
    # agent_id -> kolom Agent ID/Agent Name, submit_time -> kolom Tanggal DAN
    # basis hitung new joiner / durasi bergabung. Tanpa keduanya di cache,
    # endpoint cache-first bikin kolom-kolom itu kosong walau datanya ada di DWH.
    "agent_id", "submit_time",
    "nominal-transfer", "nama-bank", "tenor", "cicilan-per-bulan",
    "admin-fee", "nomor-rekening", "nama-di-rekening",
    "alamat-kantor-1-new", "alamat-kantor-2-new", "kantor-rt-new", "kantor-rw-new",
    "kantor-kelurahan-new", "kantor-kecamatan-new", "kantor-kabupatenkota-new",
    "kantor-provinsi-new", "kantor-kode-pos-new",
    "alamat-rumah-1-new", "alamat-rumah-2-new", "rumah-rt-new", "rumah-rw-new",
    "rumah-kelurahan-new", "rumah-kecamatan-new", "rumah-kabupatenkota-new",
    "rumah-provinsi-new", "rumah-kode-pos-new",
    "nik-new", "no-npwp-new",
    "jenis-kartu-yang-dikehendaki", "pendaftaran-credit-shield",
]

# 18 field customer (Ascend) yang benar-benar dipakai App B.
_ASSCEND_CUSTOMER_FIELDS = [
    "CUST_ID_NBR",
    "CUST_DTE_BIRTH", "CUST_PLC_BIRTH", "CUST_MOM_NAME",
    "CUST_MOBILE_PHONE", "CUST_EMAIL_ADDR",
    "CUST_EMP_ADDR1", "CUST_EMP_ADDR2", "CUST_EMP_ADDR3",
    "CUST_EMP_ADDR4", "CUST_EMP_CITY", "CUST_EMP_ZIP",
    "CUST_ADDR1", "CUST_ADDR2", "CUST_ADD_CITY",
    "CUST_ADD_PROVINCE", "CUST_ADD_ZIPCODE",
    "CUST_CR_CARD1",
]


def _filter_fields(data: Optional[Dict[str, Any]], fields: list) -> Optional[Dict[str, Any]]:
    """Ambil cuma key yang ada di `fields` dari `data`. None -> None
    (bukan dict kosong -- supaya caller bisa bedakan 'tidak ada data sama
    sekali' vs 'ada data tapi semua field kosong').

    [FIX] Nilai bertipe str di-strip() -- data DWH sering punya padding
    spasi (kolom CHAR di sumber aslinya, mis. "CUST_ID_NBR" jadi
    "3603030112720005         " dengan spasi di belakang).

    [FIX-2] Nilai bertipe Decimal (kolom NUMERIC/DECIMAL Postgres, mis.
    nominal-transfer, cicilan-per-bulan, admin-fee) di-convert ke str --
    json.dumps() (dipakai internal Json() wrapper psycopg2 saat simpan ke
    cache) TIDAK BISA serialize Decimal secara langsung, error "Object of
    type Decimal is not JSON serializable". Dikonversi ke str (bukan
    float) supaya presisi angka TIDAK berubah/hilang akibat pembulatan
    floating-point.

    Nilai NON-string/Decimal (int, bool, None) dibiarkan apa adanya.
    Perubahan ini otomatis berlaku ke SEMUA tempat penyimpanan (Postgres,
    Redis, MinIO) karena ketiganya pakai hasil fungsi ini -- cuma 1 titik
    ubah.
    """
    if data is None:
        return None
    import decimal
    out = {}
    for k in fields:
        v = data.get(k)
        if isinstance(v, str):
            out[k] = v.strip()
        elif isinstance(v, decimal.Decimal):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def save_cashline_ntb_asscend_cache(
    result_id: str,
    cashline_data: Optional[Dict[str, Any]],
    customer_data: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """[NEW] Simpan (upsert) versi TERFILTER dari cashline+customer ke
    dashboard."cashline-ntb-asscend" -- cuma 50 field yang App B pakai
    (lihat _ASSCEND_CASHLINE_FIELDS / _ASSCEND_CUSTOMER_FIELDS).

    Dipanggil dari endpoint /campaign/cashline-ntb/{result_id} SETELAH
    berhasil ambil data dari DWH -- kegagalan simpan cache TIDAK BOLEH
    menggagalkan response endpoint utama (caller harus tetap balikkan
    data walau fungsi ini return None).

    [FIX] Return dict TERFILTER (bentuk {"cashline":..., "customer":...})
    kalau berhasil, None kalau gagal -- SEBELUMNYA cuma return bool.
    Perubahan ini supaya caller (campaign_routes.py) bisa langsung
    populate Redis dari hasil fungsi ini, TANPA query ulang ke Postgres.
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia, skip simpan cache asscend untuk %s", result_id)
        return None

    filtered_cashline = _filter_fields(cashline_data, _ASSCEND_CASHLINE_FIELDS)
    filtered_customer = _filter_fields(customer_data, _ASSCEND_CUSTOMER_FIELDS)

    conn = _get_working_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dashboard."cashline-ntb-asscend"
                    (result_id, cashline_data, customer_data, cached_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (result_id) DO UPDATE SET
                    cashline_data = EXCLUDED.cashline_data,
                    customer_data = EXCLUDED.customer_data,
                    cached_at     = EXCLUDED.cached_at;
                """,
                (
                    result_id,
                    Json(filtered_cashline) if filtered_cashline is not None else None,
                    Json(filtered_customer) if filtered_customer is not None else None,
                ),
            )
        conn.commit()
        return {"cashline": filtered_cashline, "customer": filtered_customer}
    except Exception as e:
        logger.error("[DB ERROR] Gagal simpan cache asscend result_id=%s: %s", result_id, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if conn:
            db_pool.putconn(conn)


def get_cashline_ntb_asscend_cache(result_id: str) -> Optional[Dict[str, Any]]:
    """[NEW] Baca cache dari dashboard."cashline-ntb-asscend" untuk
    `result_id`. Return None kalau belum pernah di-cache (caller HARUS
    handle ini -- artinya endpoint /campaign/cashline-ntb/{result_id}
    yang sesungguhnya belum pernah dipanggil untuk result_id ini).

    Return bentuknya SAMA seperti endpoint asli:
    {"cashline": {...50 field...} | None, "customer": {...} | None,
     "cached_at": "2026-...isoformat..."}.
    """
    if db_pool is None:
        logger.warning("[DB] db_pool tidak tersedia untuk get_cashline_ntb_asscend_cache")
        return None

    conn = _get_working_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT cashline_data, customer_data, cached_at
                FROM dashboard."cashline-ntb-asscend"
                WHERE result_id = %s
                LIMIT 1;
                """,
                (result_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cashline_data, customer_data, cached_at = row
            return {
                "cashline": cashline_data,
                "customer": customer_data,
                "cached_at": cached_at.isoformat() if cached_at else None,
            }
    except Exception as e:
        logger.error("[DB ERROR] Gagal baca cache asscend result_id=%s: %s", result_id, e)
        return None
    finally:
        if conn:
            db_pool.putconn(conn)
