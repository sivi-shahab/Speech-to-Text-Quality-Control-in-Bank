"""
text_normalization.py

Normalisasi angka pada teks TAMPILAN transkrip (PDF/dashboard) memakai
GPT-5.4-mini -- mengubah angka terucap ("dua puluh lima persen") menjadi
angka tertulis ("25%").

PENTING -- SCOPE SENGAJA DIBATASI:
Fungsi ini HANYA dipakai untuk teks yang DITAMPILKAN ke user (PDF/dashboard).
JANGAN dipakai untuk data terstruktur (words[]/timestamp per-kata) yang
menjadi input evaluasi LLM (scorecard, verifikasi tanggal-lahir/nominal,
evidence quote+timestamp) -- menggabungkan "dua puluh lima" (3 kata) jadi
"25" (1 kata) akan merusak perataan word-level timestamp yang dipakai luas
di seluruh sistem evidence/citation. Transkrip mentah ber-timestamp TETAP
disimpan apa adanya; hanya SALINAN teks untuk tampilan yang dinormalisasi.

BELUM DIUJI live ke API OpenAI (network di environment ini tidak bisa akses
api.openai.com) -- syntax & logic sudah divalidasi dengan mock, tapi WAJIB
dites dengan API key asli sebelum dipakai di production.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
NORMALIZATION_MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")

# Azure AI Foundry (bukan OpenAI langsung) -- terdeteksi dari domain
# *.cognitiveservices.azure.com atau *.openai.azure.com pada LLM_BASE_URL.
# API v1 Azure (GA sejak Agustus 2025) cukup pakai client OpenAI() BIASA
# (bukan AzureOpenAI()), asal base_url diarahkan ke {resource}/openai/v1/ --
# TIDAK perlu api_version lagi. `model` di Azure = NAMA DEPLOYMENT (bukan
# selalu identik dengan nama model resmi, tapi sering disamakan supaya
# gampang) -- pastikan LLM_MODEL cocok dengan deployment name di Foundry.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")

# Reasoning effort rendah -- normalisasi angka adalah tugas extraction/rewrite
# yang relatif langsung, tidak butuh reasoning berat. "low" dipilih untuk
# tetap tahan terhadap disfluency (butuh sedikit "pemahaman" konteks) tanpa
# biaya/latensi reasoning tinggi yang tidak perlu.
REASONING_EFFORT = os.getenv("TEXT_NORM_REASONING_EFFORT", "low")

_SYSTEM_PROMPT = """\
Anda adalah asisten normalisasi teks transkrip panggilan telemarketing bank \
(Bahasa Indonesia).

TUGAS: Ubah angka yang DIUCAPKAN dalam bentuk KATA menjadi ANGKA TERTULIS. \
JANGAN ubah apa pun yang lain.

ATURAN WAJIB:
1. Angka terucap -> digit. Contoh:
   "dua puluh lima persen" -> "25%"
   "lima ratus ribu rupiah" -> "Rp500.000"
   "tiga belas januari sembilan belas sembilan puluh" -> "13 Januari 1990"
   "Dua Belas nol lima sembilan belas delapan tujuh" -> "12051987"
   "empat belas juta" -> "Rp14.000.000" jika jelas konteks nominal)
   "nol delapan 1"
2. Kalau speaker RAGU-RAGU atau MENGOREKSI DIRI dan mengulang angka yang SAMA
   (mis. "empat belas... empat belas juta"), tetap SATU angka final saja
   ("Rp14.000.000") -- JANGAN digandakan, JANGAN pertahankan pengulangannya.
3. JANGAN parafrase kalimat, JANGAN hapus kata lain, JANGAN ubah tanda baca
   atau struktur selain angka yang dikonversi.
4. KALAU RAGU apakah sebuah kata adalah angka yang perlu dikonversi atau
   bukan (mis. bisa jadi nama orang, istilah lain) -- JANGAN diubah, biarkan
   apa adanya. Lebih aman salah-tidak-ubah daripada salah-ubah untuk data \
finansial/pribadi.
5. Pertahankan label speaker, timestamp, dan urutan baris PERSIS seperti \
input -- HANYA konten angka dalam kalimat yang diubah.
6. OUTPUT HANYA teks hasil normalisasi. TANPA penjelasan, TANPA komentar, \
TANPA markdown/code fence tambahan.

"""


class NormalizationError(Exception):
    """Gagal normalisasi -- caller HARUS fallback ke teks asli, jangan biarkan
    kegagalan normalisasi menghalangi transkrip tetap tersedia."""


def normalize_transcript_text(
    raw_text: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> str:
    """Normalisasi angka pada teks TAMPILAN transkrip via GPT-5.4-mini.

    HANYA untuk teks yang ditampilkan ke user (PDF/dashboard) -- lihat
    docstring modul untuk alasan kenapa data ber-timestamp tidak boleh lewat
    fungsi ini.

    Raises:
        NormalizationError: kalau panggilan API gagal (network, auth, dll).
            Caller WAJIB fallback ke `raw_text` asli saat exception ini
            muncul -- normalisasi adalah "nice to have", bukan hard
            dependency; transkrip asli harus tetap tersedia walau
            normalisasi gagal.
    """
    if not raw_text or not raw_text.strip():
        return raw_text

    key = api_key or OPENAI_API_KEY
    if not key:
        raise NormalizationError(
            "OPENAI_API_KEY tidak di-set -- normalisasi di-skip, pakai teks asli."
        )

    try:
        from openai import OpenAI  # import lokal: jangan hard-fail modul kalau
        # package openai belum terinstall di environment yang tidak butuh fitur ini
    except ImportError as e:
        raise NormalizationError(f"Package 'openai' belum terinstall: {e}") from e

    client_kwargs = {"api_key": key, "timeout": timeout}
    if LLM_BASE_URL:
        # Azure AI Foundry v1 API: base_url = {resource-root}/openai/v1/
        # Contoh: LLM_BASE_URL="https://foundry-dmanalytics-hub.cognitiveservices.azure.com"
        #      -> base_url="https://foundry-dmanalytics-hub.cognitiveservices.azure.com/openai/v1/"
        client_kwargs["base_url"] = f"{LLM_BASE_URL}/openai/v1/"
    client = OpenAI(**client_kwargs)
    use_model = model or NORMALIZATION_MODEL

    try:
        response = client.responses.create(
            model=use_model,
            reasoning={"effort": REASONING_EFFORT},
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )
        normalized = (response.output_text or "").strip()
    except Exception as e:
        # Tangkap SEMUA exception dari SDK (network, auth, rate limit, dll)
        # dan bungkus jadi NormalizationError -- caller HANYA perlu handle
        # 1 tipe exception untuk fallback, tidak perlu tahu detail internal SDK.
        raise NormalizationError(f"Panggilan API GPT-5.4-mini gagal: {e}") from e

    if not normalized:
        raise NormalizationError("Respons API kosong -- pakai teks asli.")

    return normalized


def normalize_transcript_text_safe(raw_text: str, **kwargs) -> str:
    """Wrapper aman: normalisasi via normalize_transcript_text(), tapi kalau
    gagal (network/auth/dll), LOG warning dan fallback ke `raw_text` asli --
    supaya kegagalan normalisasi TIDAK PERNAH menghalangi transkrip tetap
    tersedia untuk user. Dipakai di jalur produksi (bukan
    normalize_transcript_text() langsung, yang dibiarkan raise untuk
    keperluan testing/debug eksplisit).
    """
    try:
        return normalize_transcript_text(raw_text, **kwargs)
    except NormalizationError as e:
        logger.warning("Normalisasi transkrip gagal, fallback ke teks asli: %s", e)
        return raw_text
