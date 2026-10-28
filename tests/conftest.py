"""
Fixture bersama. Env label speaker dipaksa ke default SEBELUM modul model
di-import (config.py membaca env saat import), supaya test tidak bergantung
pada .env mesin.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

os.environ["AGENT_SPEAKER_LABEL"] = "Agent"
os.environ["CUSTOMER_SPEAKER_LABEL"] = "Customer"
os.environ["DIARIZATION_V2_MODE"] = "off"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_scribe_dict(name: str) -> dict:
    return json.loads((FIXTURES / "scribe" / ("%s.json" % name)).read_text())


def load_scribe_response(name: str):
    """Fixture sebagai objek response SDK asli (bentuk yang diterima backend)."""
    from elevenlabs.types import SpeechToTextChunkResponseModel

    return SpeechToTextChunkResponseModel.model_validate(load_scribe_dict(name))


@pytest.fixture
def synthetic_wav(tmp_path):
    """WAV 16 kHz mono 30 s berisi noise ber-seed (lolos cek RMS, deterministik)."""
    path = tmp_path / "synthetic.wav"
    rng = np.random.default_rng(1234)
    sf.write(path, (rng.standard_normal(16000 * 30) * 0.01).astype(np.float32), 16000)
    return str(path)
