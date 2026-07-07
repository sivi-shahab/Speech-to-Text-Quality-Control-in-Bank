#!/usr/bin/env python3
"""
services/server_config.py

[PENTING — URUTAN IMPORT KRITIKAL]
Modul ini WAJIB jadi import PERTAMA di server.py, SEBELUM import apa pun
yang menyentuh `torch` — termasuk `routes.speech_routes` yang sekarang
transitif meng-import `model.speech_stt` (yang meng-import torch di level
module). CUDA_VISIBLE_DEVICES harus di-set sebelum torch pertama kali
di-import, kalau tidak GPU isolation tidak akan berfungsi.

Modul ini sendiri TIDAK mengimport torch/fastapi/apa pun yang berat,
jadi aman diimpor kapan saja tanpa memicu inisialisasi CUDA prematur.

Dipindah verbatim dari bagian atas server.py v4.0 — tidak ada nilai
yang diubah.
"""

import os
from dotenv import load_dotenv

load_dotenv()

os.environ["TMPDIR"] = os.getenv("TMPDIR", "/data/tmp")
import tempfile
tempfile.tempdir = os.environ["TMPDIR"]

# =================================================================
# CUDA isolation — set SEBELUM import torch
# Setiap server instance (port 8000/8001) dijalankan sebagai proses
# terpisah dengan ASSIGNED_GPU yang berbeda di env/supervisor config.
# =================================================================
ASSIGNED_GPU = int(os.getenv("ASSIGNED_GPU", "0"))
os.environ["CUDA_VISIBLE_DEVICES"] = str(ASSIGNED_GPU)

# =================================================================
# Cache paths
# =================================================================
os.environ["HF_HOME"]               = "/data/.cache/huggingface"
os.environ["HUGGINGFACE_HUB_CACHE"] = "/data/.cache/huggingface/hub"
os.environ["TRANSFORMERS_CACHE"]    = "/data/.cache/huggingface/transformers"
os.environ["TORCH_HOME"]            = "/data/.cache/torch"

# =================================================================
# Konstanta — disederhanakan
# GPU logical index selalu 0 karena CUDA_VISIBLE_DEVICES isolation
# =================================================================
LOGICAL_GPU_INDEX          = 0
ESTIMATED_MODEL_MEMORY_GB  = float(os.getenv("ESTIMATED_MODEL_MEMORY_GB", "4.0"))
DEPLOYMENT_MODE            = os.getenv("DEPLOYMENT_MODE", "dev").lower()

# =================================================================
# CORS
# =================================================================
CORS_ORIGINS      = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]
CORS_HAS_WILDCARD = "*" in CORS_ORIGINS
