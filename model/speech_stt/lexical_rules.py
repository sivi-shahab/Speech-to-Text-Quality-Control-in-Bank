#!/usr/bin/env python3
"""
model/speech_stt/lexical_rules.py

Semua konstanta lexical yang dipakai heuristik alignment/koreksi speaker
(FORBIDDEN_CUSTOMER_WORDS, AGENT_OPENER_PHRASES, QA_INLINE_PATTERNS, dst).
Dipindah verbatim dari speech_stt.py — tidak ada satu kata pun yang diubah.

Ini murni DATA (bukan logic), makanya aman dipisah tanpa risiko regresi.
Logic yang MEMAKAI konstanta ini tetap di modul alignment_legacy.py supaya
tidak memutus saling-rujuk antar helper function yang saling terkait erat.
"""

from typing import List, Tuple

FORBIDDEN_CUSTOMER_WORDS: List[str] = [
    "mega cashline", "mega ultima shield", "mega ultimasyut", "mega ultima shoot",
    "mega life insurance", "pfa megalife", "msmile", "nsmile",
    "cicilan flat", "tenor", "efektif rate", "biaya provisi",
    "total tagihan terhutang", "sisa pokok pinjaman", "uang pertanggungan",
    "penyakit kritis", "operasi bypass", "infark miokard", "infak miokat", "premi",
    "yang bertugas", "izin simulasikan", "izin ulanginya", "izin sebutkan",
    "mohon izin", "bantu ajukan", "kami bantu", "jangan dulu ditutup",
    "terundang limit baru", "pemanfaatannya khusus", "pernyataan kesehatan",
    "terverifikasi", "perlindungan data pribadi", "direkam untuk pengajuan",
    "merupakan pertimbangan utama",
]

AGENT_OPENER_PHRASES: List[str] = [
    # Salam pembuka
    "selamat pagi", "selamat siang", "selamat sore", "selamat malam",
    # Perkenalan (tanpa nama agen — nama agen juga dinamis)
    "halo dengan", "halo saya", "mohon izin",
    "dengan bank mega", "dari bank mega",
    "izin minta waktunya", "izin saya",
    "kembali dari bank", "ini dengan",
    # Frasa khas agen telemarketing Bank Mega
    "alih informasikan", "berizin dan diawasi",
    "kartu kredit tambahan", "masih ingat pertama kali",
    "dibantu untuk alamat",
    "perkenalkan dengan saya",
    "izin minta waktunya sebentar",
    "menyampaikan informasi",
]

# FIX #3: Kata yang pasti diucapkan AGEN saat membaca dokumen
# Meski pendek, segmen ini TIDAK di-flip oleh duration_guard
AGENT_DOCUMENT_WORDS: set = {
    "bahwa", "pertama", "kedua", "ketiga", "keempat",
    "selanjutnya", "berikutnya", "kemudian", "adapun",
    "pengobatan", "perawatan", "mengandung", "dirawat",
    "limit", "kredit", "analisa", "pengajuan", "pengiriman", "ditentukan",
    "kecamatan", "kelurahan", "provinsi", "kabupaten",
    "tanggal", "januari", "februari", "maret", "april", "mei", "juni",
    "juli", "agustus", "september", "oktober", "november", "desember",
}

# FIX #4: QA patterns untuk memisahkan jawaban customer dari blok panjang agen
# QA_INLINE_PATTERNS: pola STRUKTURAL kalimat agen → jawaban customer.
# Semua nama nasabah TIDAK di-hardcode — nama distrip dinamis via
# _strip_proper_nouns() sebelum matching dilakukan.
# Format: (frasa_akhir_pertanyaan_agen, jawaban_customer)
QA_INLINE_PATTERNS: List[Tuple[str, str]] = [
    # ── Pembukaan: izin minta waktu ─────────────────────────────────
    # Pattern generik — nama nasabah sudah di-strip jadi [NAMA]
    # Covers: "...sebentar Ibu Ernawati Boleh" / "...sebentar Bapak Budi Iya"
    ("minta waktunya sebentar ibu",                "boleh"),
    ("minta waktunya sebentar ibu",                "iya"),
    ("minta waktunya sebentar bapak",              "boleh"),
    ("minta waktunya sebentar bapak",              "iya"),
    ("minta waktunya sebentar",                    "boleh"),
    ("minta waktunya sebentar",                    "iya"),

    # ── Konfirmasi izin baca/ubah dokumen (pola "...nya Boleh" di akhir) ──
    # Agen minta izin membacakan/mengubah sesuatu, customer jawab "Boleh"
    # tepat di akhir kalimat panjang. Anchor harus SPESIFIK ke frasa akhir
    # instruksi, BUKAN frasa di tengah kalimat (mis. "boleh ya bu ya" yang
    # muncul di tengah adalah partikel sopan agen, bukan jawaban customer).
    ("untuk ubah kecil-kecilannya",                "boleh"),

    # ── Konfirmasi identitas ────────────────────────────────────────
    # "keluarganya" cukup sebagai anchor — [NAMA] di antara tidak masalah
    # Covers: "...keluarganya Ibu [NAMA] mohon maaf Iya"
    #         "...keluarganya, mohon maaf Iya"
    ("keluarganya ibu",                            "iya"),
    ("keluarganya bapak",                          "iya"),
    ("keluarganya, mohon maaf",                    "iya"),
    ("keluarganya mohon maaf",                     "iya"),

    # ── Konfirmasi kepemilikan kartu ────────────────────────────────
    ("ada atau tidak?",                            "tidak"),
    ("ada atau tidak?",                            "ada"),
    ("betul ya?",                                  "iya"),
    ("betul ya?",                                  "ya"),
    ("betul ya?",                                  "betul"),

    # ── Konfirmasi alamat pengiriman ────────────────────────────────
    ("rumah atau kantor,",                         "rumah"),
    ("rumah atau kantor,",                         "kantor"),
    ("rumah atau kantor?",                         "rumah"),
    ("rumah atau kantor?",                         "kantor"),

    # ── Konfirmasi final (setuju/oke) ───────────────────────────────
    ("oke atau setuju ibu?",                       "oke setuju"),
    ("oke atau setuju ibu?",                       "setuju"),
    ("oke atau setuju ibu?",                       "boleh"),

    # ── Konfirmasi kata iya ─────────────────────────────────────────
    ("dibantu kembali dengan kata iya",            "setuju"),
    ("dibantu kembali dengan kata iya",            "iya setuju"),
    ("dibantu kembali dengan kata iya",            "iya"),

    # ── Jawaban data diri ───────────────────────────────────────────
    ("tanggal lahir boleh dibantu",                "boleh"),
    ("boleh ya bu ya",                             "boleh"),
    ("boleh ya bu",                                "boleh"),
]

CUSTOMER_ONLY_RESPONSES: List[str] = [
    "ya", "iya", "ya iya", "iya ya", "betul", "benar", "bener",
    "ya betul", "ya benar", "iya betul", "iya benar",
    "ya bener", "iya bener", "ya dong", "iya dong",
    "tentu", "ya tentu", "tentu saja",
    "setuju", "ya setuju", "oke setuju",
    "oke", "ok", "oke deh", "ok deh",
    "boleh", "boleh boleh", "ya boleh",
    "bisa", "bisa bisa", "ya bisa", "silakan", "silahkan",
    "tidak", "nggak", "enggak", "ndak", "gak", "tak",
    "tidak ada", "nggak ada", "enggak ada", "gak ada",
    "tidak mau", "nggak mau", "enggak mau", "gak mau",
    "tidak bisa", "nggak bisa", "gak bisa",
    "tidak perlu", "nggak perlu", "gak perlu",
    "tidak usah", "nggak usah", "gak usah",
    "tidak jadi", "nggak jadi", "gak jadi",
    "belum", "belum ada", "belum punya",
    "jangan", "jangan dulu", "nanti dulu", "tunggu dulu",
    "halo", "halo halo", "ya halo", "iya halo", "oh halo",
    "halo sore", "halo pagi", "halo siang", "halo malam",
    "sore", "pagi", "siang", "malam",
    "ya sore", "iya sore", "oh sore",
    "12", "18", "24", "30", "36", "48", "60",
    "12 bulan", "18 bulan", "24 bulan", "36 bulan", "48 bulan", "60 bulan",
    "dua belas", "delapan belas", "dua puluh empat",
    "tiga puluh enam", "empat puluh delapan", "enam puluh",
    "satu tahun", "dua tahun", "tiga tahun",
    "48 ya", "36 ya", "24 ya",
    "ke rumah", "ke kantor", "di rumah", "di kantor",
    "rumah", "kantor", "ya ke rumah", "ya ke kantor",
    "iya ke rumah", "iya ke kantor",
    "ada", "ada satu", "ada dua", "ada tiga",
    "tidak ada", "nggak ada", "belum ada",
    "sudah", "sudah ada", "sudah punya",
    "belum", "belum ada", "belum punya",
    "punya", "punya satu", "tidak punya", "gak punya",
    "ya bu", "iya bu", "ya pak", "iya pak",
    "ya mbak", "iya mbak", "ya mas", "iya mas",
    "ya mba", "iya mba",
    "oh iya", "oh ya", "oh oke", "oh gitu", "oh begitu",
    "gitu ya", "begitu ya", "oh nggak", "oh tidak", "oh belum",
    "hmm", "hm", "mmm", "eh", "ah", "oh",
    "mmm iya", "hmm iya", "hmm ya",
    "sebentar", "sebentar ya", "tunggu sebentar",
    "coba", "coba dulu", "dicoba dulu",
    "pikir dulu", "dipikir dulu",
    "nanti", "nanti ya", "nanti dulu",
    "lihat dulu", "dilihat dulu",
    "mungkin", "mungkin iya", "mungkin bisa",
    "kayaknya", "sepertinya", "rasanya",
    "hah", "ha", "apa", "apa ya", "gimana", "bagaimana",
    "maaf", "maaf ya", "maaf bu", "maaf pak",
    "kurang jelas", "tidak jelas", "nggak jelas",
    "bisa diulang", "tolong diulang",
    "tapi", "tapi kan", "kenapa", "mengapa",
    "masa", "masa sih", "masa iya",
    "kok", "kok bisa", "kok gitu",
    "loh", "lho", "lha", "wah", "wah iya", "wah gitu",
    "aduh", "aduh iya",
    "berat", "terlalu berat", "terlalu mahal",
    "mahal", "terlalu mahal", "keberatan", "ada keberatan",
    "tidak ada", "ada satu", "ada dua",
    "atas nama saya", "atas nama suami", "atas nama istri", "atas nama anak",
    "sudah", "belum", "sudah menikah", "belum menikah",
    "sudah berkeluarga", "masih lajang",
    "swasta", "pns", "wiraswasta", "ibu rumah tangga",
    "gaji", "ya gaji",
    "oke terima kasih", "ya terima kasih", "iya terima kasih",
    "makasih", "makasih ya", "makasih bu", "makasih pak",
    "terima kasih", "terima kasih ya",
    "sama sama", "ya sama sama",
    "oke dah", "ya udah", "iya udah",
    "sampai jumpa", "dadah", "bye",
    # customer balik tanya
    "iya kenapa", "ya kenapa", "oh kenapa",
    "iya gimana", "ya gimana", "oh gimana",
    "iya berapa", "ya berapa",
    "iya herna kenapa", "ya herna kenapa",
    "kenapa?", "gimana?", "berapa?",
]

_PASSIVE_CUSTOMER_RESPONSES: set = {
    "ya", "iya", "baik", "oke", "ok", "boleh", "bisa",
    "tidak", "nggak", "enggak", "ndak", "gak",
    "hmm", "hm", "oh", "eh", "ah",
    "berapa", "kapan", "gimana", "bagaimana",
    "halo", "sore", "pagi", "siang", "malam",
    "ya halo", "iya halo", "halo sore", "halo pagi",
    "benar", "betul", "iya benar", "ya benar",
    "setuju", "oke bu", "iya bu", "ya bu",
}

# Kata tanya customer — customer balik bertanya ke agen.
# Segmen NASABAH yang mengandung kata-kata ini TIDAK di-flip
# oleh duration_guard, meski durasi < MIN_SEGMENT_DUR_SEC.
CUSTOMER_QUESTION_WORDS: set = {
    "kenapa", "mengapa", "gimana", "bagaimana", "apakah",
    "berapa", "kapan", "dimana", "di mana", "siapa",
    "apa", "yang mana", "mana",
    # pola spesifik call center
    "kenapa?", "gimana?", "berapa?",
    "iya kenapa", "ya kenapa", "oh kenapa",
    "iya gimana", "ya gimana",
    "iya berapa", "ya berapa",
    "boleh tahu", "boleh tau",
}

# Kata Indonesia pendek (1-3 huruf) yang VALID berdiri sendiri.
# Dipakai sebagai whitelist agar tidak salah dianggap sebagai
# fragmen kata yang terpotong pyannote (mis. "ya", "di", "ke").
VALID_SHORT_WORDS: set = {
    "ya", "di", "ke", "ku", "mu", "ia", "aku", "itu", "ini",
    "dan", "tak", "tau", "iya", "kan", "pun", "loh", "deh",
    "dah", "nah", "wah", "aja", "apa", "ada", "abis", "ama",
    "gak", "gan", "kok", "lho", "biar", "kita", "kami", "saya",
    "anda", "dia", "kau", "mas", "mba", "pak", "bu", "bapak",
    "ibu", "tu", "lu", "gw", "gue", "kamu", "tau", "mau",
    "bisa", "ada", "oke", "ok", "iy", "ye",
    # v6.1: tambahan untuk cegah false-positive Pass 0 mid-word merge
    # (mis. "Oh enggak"/"Oh gitu" jangan digabung paksa jadi "Ohenggak")
    "oh", "eh", "ah", "hm", "ho", "lah", "sih", "dong", "kah",
    "nya", "pun", "ada", "dua", "tri", "rt", "rw", "km", "no",
}

# Kata yang BUKAN nama orang meski huruf kapital
NOT_PROPER_NOUNS: set = {
    # sapaan
    "ibu", "bapak", "pak", "bu", "mas", "mbak",
    # institusi
    "bank", "mega", "visa",
    # kata hubung & umum
    "untuk", "dengan", "dari", "pada", "yang", "ini",
    "itu", "dan", "atau", "juga", "sudah", "belum",
    "saya", "kami", "kita", "anda",
    # jawaban customer (huruf kapital di tengah kalimat, bukan nama)
    "boleh", "iya", "tidak", "oke", "ok", "setuju",
    "betul", "benar", "bisa", "nanti", "sudah", "belum",
    "ada", "mau", "halo", "sore", "pagi", "siang", "malam",
    "maaf", "terima", "sama", "baik", "nah", "jadi",
    "kalau", "tapi", "karena",
}

# Kata yang menandakan AGEN melanjutkan bicara setelah jeda — penanda
# kuat bahwa teks sebelumnya (setelah "?") adalah jawaban CUSTOMER,
# bukan lanjutan kalimat agen sendiri.
AGENT_RESUME_MARKERS: Tuple[str, ...] = (
    "baik,", "baik ", "oke,", "oke ", "nah,", "nah ",
    "jadi,", "jadi ", "untuk", "yang terhormat", "dan ",
    "kalau", "ibu,", "ibu ", "bapak,", "bapak ",
)

# Kata sambung tanya — jika kandidat jawaban DIAWALI kata ini DAN
# DIAKHIRI "?", itu kemungkinan besar AGEN melanjutkan pertanyaan
# lain, bukan customer menjawab. Mencegah false positive seperti
# "...berjalan berapa? Atau usahanya berjalan berapa?" (agen tanya
# dua kali berturut, bukan customer menjawab di antaranya).
AGENT_QUESTION_STARTERS: set = {
    "atau", "apakah", "bagaimana", "kapan", "siapa", "dimana",
    "mengapa", "kenapa", "untuk", "kalau", "jika",
}

# Kata single-word yang sangat khas jawaban customer — dipakai
# _safe_single_word_correction (v7.0 pure mode alignment).
SINGLE_WORD_CUSTOMER: set = {
    "ya", "iya", "tidak", "nggak", "enggak", "gak", "boleh",
    "oke", "ok", "betul", "benar", "bener", "setuju", "bisa",
    "sudah", "belum", "ada", "sip", "siap",
}
