-- Tabel gabungan NTB eksternal + LOS (dipakai services/db.py::save_ntb_los).
-- Pola sama dengan dashboard."cashline-ntb-asscend".
CREATE TABLE IF NOT EXISTS dashboard."ntb-los" (
    result_id        text PRIMARY KEY,
    ntb_data         jsonb,                    -- semua kolom campaign_new_to_bank_eksternal
    customer_consent boolean,                  -- newlos_request.customer_consent
    persetujuan_data varchar,                  -- newlos_data_mob.persetujuan_data
    cached_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ntb_los_cached_at
    ON dashboard."ntb-los" (cached_at);
