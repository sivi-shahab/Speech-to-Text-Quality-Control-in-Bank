"""
DIARIZATION_V2_MODE off/shadow/on terhadap golden jalur lama.

Fase 1: rangkaian v2 setara jalur lama, jadi KETIGA mode harus menghasilkan
teks PDF yang identik dengan golden. Mulai Fase 2 test mode "on" diganti
dengan ekspektasi v2 sendiri; off & shadow tetap wajib identik.
"""
import json

import pytest

from model.speech_stt import config as stt_config
from tests.conftest import FIXTURES
from tests.test_legacy_engine_golden import CASES, META_KEYS, _run

GOLDEN = FIXTURES / "golden"


def _assert_golden(name, text, meta):
    assert text + "\n" == (GOLDEN / ("%s.txt" % name)).read_text()
    expected = json.loads((GOLDEN / ("%s.meta.json" % name)).read_text())
    assert json.loads(json.dumps({k: meta.get(k) for k in META_KEYS})) == expected


@pytest.mark.parametrize("name", CASES)
def test_off_identik_dan_tanpa_key_v2(name, synthetic_wav, monkeypatch):
    monkeypatch.setattr(stt_config, "DIARIZATION_V2_MODE", "off")
    text, _, meta = _run(name, synthetic_wav, monkeypatch)
    _assert_golden(name, text, meta)
    assert "diarization_v2" not in meta


@pytest.mark.parametrize("name", CASES)
def test_shadow_output_identik_dan_v2_di_metadata(name, synthetic_wav, monkeypatch):
    monkeypatch.setattr(stt_config, "DIARIZATION_V2_MODE", "shadow")
    text, _, meta = _run(name, synthetic_wav, monkeypatch)
    _assert_golden(name, text, meta)
    v2 = meta["diarization_v2"]
    assert v2["source"] == "shadow" and v2["version"] == 1
    assert v2["n_segments"] == len(v2["segments"]) > 0
    assert "text" not in json.dumps(v2)  # tidak ada teks transkrip


@pytest.mark.parametrize("name", CASES)
def test_on_fase1_setara_jalur_lama(name, synthetic_wav, monkeypatch):
    monkeypatch.setattr(stt_config, "DIARIZATION_V2_MODE", "on")
    monkeypatch.setattr(stt_config, "SCRIBE_DIAR_MODE", "fixed2")
    text, _, meta = _run(name, synthetic_wav, monkeypatch)
    _assert_golden(name, text, meta)
    assert meta["diarization_v2"]["source"] == "scribe_api"
    assert meta["elevenlabs_raw_response"]["transcription_id"] == "synthetic"


def test_shadow_tidak_menggagalkan_job_saat_raw_rusak(synthetic_wav, monkeypatch):
    from model.speech_stt.diarization_v2 import shadow_from_raw

    assert shadow_from_raw(None) is None
    broken = shadow_from_raw({"words": [{"type": "word", "text": "x", "start": "bukan-angka", "end": 1}]})
    assert broken == {"version": 1, "source": "shadow", "error": "ValueError"}
