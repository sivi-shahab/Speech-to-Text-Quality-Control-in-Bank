-- Index untuk get_current_nls_request_cc() di services/db.py:
--   SELECT * FROM dashboard.current_nls_request_cc
--   WHERE type_request = 'S' AND TRIM(numerator) = %s;
--
-- Expression index di TRIM(numerator) (index biasa di numerator tidak
-- terpakai karena kolomnya dibungkus TRIM), partial hanya untuk
-- type_request = 'S' supaya lebih kecil.
--
-- CONCURRENTLY: tidak mengunci tabel dari tulis selama build. Tidak boleh
-- dijalankan di dalam transaction block (BEGIN ... COMMIT); jalankan
-- sebagai statement tunggal dengan autocommit (psql default).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_current_nls_request_cc_trim_numerator
    ON dashboard.current_nls_request_cc (TRIM(numerator))
    WHERE type_request = 'S';
