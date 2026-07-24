#!/usr/bin/env python3
"""
diagnose_cache_pipeline.py

Diagnostik menyeluruh untuk 1 result_id -- trace SETIAP langkah dari query
DWH langsung sampai data tersimpan di cache Postgres, supaya ketahuan
PERSIS di titik mana (kalau ada) yang gagal.

Jalankan LANGSUNG di server App A (bukan lewat HTTP), supaya bisa lihat
detail internal yang tidak muncul di response API biasa.

Cara pakai:
    cd /data/speech_to_text_v4/refactored
    python3 diagnose_cache_pipeline.py 020452YO26
"""
import sys
import os

sys.path.insert(0, os.getcwd())


def _load_dotenv(path=".env"):
    """[NEW] Load file .env sederhana (tanpa dependency python-dotenv) --
    supaya script ini bisa dijalankan manual (bukan lewat systemd) dan
    tetap dapat semua environment variable (DATABASE_URL, dll) yang
    biasanya di-load otomatis saat service jalan normal."""
    if not os.path.exists(path):
        print(f"⚠️  File {path} tidak ditemukan di direktori kerja -- "
              f"pastikan menjalankan script ini dari folder yang benar "
              f"(tempat .env berada), atau export DATABASE_URL manual.")
        return
    loaded = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:  # jangan timpa yang sudah di-set manual
                os.environ[key] = value
                loaded += 1
    print(f"✅ {loaded} environment variable dimuat dari {path}\n")


_load_dotenv()

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 diagnose_cache_pipeline.py <result_id>")
        sys.exit(1)

    result_id = sys.argv[1]
    print(f"{'='*70}")
    print(f"DIAGNOSTIK PIPELINE CACHE untuk result_id={result_id}")
    print(f"{'='*70}\n")

    # --- Langkah 0: cek db_pool tersedia ---
    print("--- Langkah 0: cek db_pool App A ---")
    try:
        from services import db as db_module
        if db_module.db_pool is None:
            print("❌ db_pool adalah None -- koneksi ke DATABASE_URL App A gagal saat startup!")
            print("   Cek: apakah DATABASE_URL benar? Apakah server DWH bisa diakses?")
            sys.exit(1)
        print("✅ db_pool tersedia (bukan None)")
    except Exception as e:
        print(f"❌ Gagal import services.db: {e}")
        sys.exit(1)

    # --- Langkah 1: test koneksi mentah ---
    print("\n--- Langkah 1: test _get_working_conn() ---")
    conn = db_module._get_working_conn()
    if conn is None:
        print("❌ _get_working_conn() balik None -- koneksi ke DWH GAGAL sama sekali.")
        print("   Ini akar masalahnya -- semua langkah selanjutnya PASTI gagal juga.")
        sys.exit(1)
    print("✅ Koneksi berhasil didapat & divalidasi (SELECT 1 sukses)")
    db_module.db_pool.putconn(conn)

    # --- Langkah 2: query get_campaign_cashline_ntb() langsung ---
    print(f"\n--- Langkah 2: get_campaign_cashline_ntb('{result_id}') ---")
    campaign_data = db_module.get_campaign_cashline_ntb(result_id)
    if campaign_data is None:
        print(f"❌ TIDAK ADA data di dashboard.campaign_cashline_ntb untuk result_id='{result_id}'")
        print("   Kemungkinan: (a) result_id ini memang tidak ada di DWH, atau")
        print("   (b) ada error koneksi yang di-log tapi tidak raise (cek log gpu-app-lightweight)")
        sys.exit(1)
    print(f"✅ Data ADA -- {len(campaign_data)} kolom ditemukan")
    print(f"   cust_name: {campaign_data.get('cust_name')!r}")
    print(f"   no-ktpkitas: {campaign_data.get('no-ktpkitas')!r}")

    no_ktpkitas = campaign_data.get("no-ktpkitas")
    if not no_ktpkitas:
        print("⚠️  no-ktpkitas KOSONG -- customer_data TIDAK akan bisa diambil (ini WAJAR")
        print("   kalau memang datanya begitu di sumber, bukan bug)")
        customer_data = None
    else:
        # --- Langkah 3: query get_current_cc_scmcustp() ---
        print(f"\n--- Langkah 3: get_current_cc_scmcustp('{no_ktpkitas}') ---")
        customer_data = db_module.get_current_cc_scmcustp(no_ktpkitas)
        if customer_data is None:
            print(f"⚠️  TIDAK ADA data customer untuk no-ktpkitas='{no_ktpkitas}'")
            print("   (kemungkinan memang tidak match, bukan error koneksi -- cek manual di DB)")
        else:
            print(f"✅ Data customer ADA -- {len(customer_data)} kolom ditemukan")
            print(f"   CUST_CR_CARD1: {customer_data.get('CUST_CR_CARD1')!r}")

    # --- Langkah 4: test _filter_fields() (termasuk fix Decimal) ---
    print(f"\n--- Langkah 4: _filter_fields() (cek Decimal/tipe data lain) ---")
    try:
        filtered_cashline = db_module._filter_fields(campaign_data, db_module._ASSCEND_CASHLINE_FIELDS)
        filtered_customer = db_module._filter_fields(customer_data, db_module._ASSCEND_CUSTOMER_FIELDS)
        print(f"✅ Filter berhasil -- {len(filtered_cashline or {})} field cashline, "
              f"{len(filtered_customer or {})} field customer")

        # Cek eksplisit: ada Decimal yang LOLOS tidak ter-convert?
        import decimal
        bad_types = []
        for k, v in (filtered_cashline or {}).items():
            if isinstance(v, decimal.Decimal):
                bad_types.append(f"cashline.{k}")
        for k, v in (filtered_customer or {}).items():
            if isinstance(v, decimal.Decimal):
                bad_types.append(f"customer.{k}")
        if bad_types:
            print(f"❌ MASIH ADA Decimal yang lolos (fix belum aktif?): {bad_types}")
        else:
            print("✅ Tidak ada Decimal yang lolos -- fix sudah aktif dengan benar")
    except Exception as e:
        print(f"❌ _filter_fields() ERROR: {e}")
        sys.exit(1)

    # --- Langkah 5: test json.dumps() (simulasi persis apa yang Json() lakukan) ---
    print(f"\n--- Langkah 5: simulasi json.dumps() (persis seperti Json() wrapper) ---")
    import json
    try:
        json.dumps(filtered_cashline)
        json.dumps(filtered_customer)
        print("✅ json.dumps() berhasil untuk kedua dict -- TIDAK akan error saat INSERT")
    except TypeError as e:
        print(f"❌ json.dumps() GAGAL: {e}")
        print("   Ini AKAN menyebabkan error 'Object of type X is not JSON serializable' saat simpan!")
        sys.exit(1)

    # --- Langkah 6: benar-benar simpan ke Postgres ---
    print(f"\n--- Langkah 6: save_cashline_ntb_asscend_cache() (TULIS SUNGGUHAN) ---")
    result = db_module.save_cashline_ntb_asscend_cache(result_id, campaign_data, customer_data)
    if result is None:
        print("❌ save_cashline_ntb_asscend_cache() balik None -- GAGAL simpan!")
        print("   Cek log gpu-app-lightweight.service untuk pesan error detail:")
        print("   sudo journalctl -u gpu-app-lightweight.service -n 50 --no-pager | grep 'DB ERROR'")
        sys.exit(1)
    print(f"✅ BERHASIL disimpan! Isi yang tersimpan:")
    print(f"   cashline: {result.get('cashline')}")
    print(f"   customer: {result.get('customer')}")

    # --- Langkah 7: baca balik dari Postgres untuk konfirmasi ---
    print(f"\n--- Langkah 7: baca balik get_cashline_ntb_asscend_cache() ---")
    cached = db_module.get_cashline_ntb_asscend_cache(result_id)
    if cached is None:
        print("❌ ANEH -- baru saja berhasil simpan (Langkah 6), tapi baca balik gagal!")
        sys.exit(1)
    print(f"✅ Berhasil dibaca balik dari Postgres:")
    print(f"   cached_at: {cached.get('cached_at')}")

    print(f"\n{'='*70}")
    print("🎉 SEMUA LANGKAH BERHASIL -- pipeline cache bekerja dengan benar")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()