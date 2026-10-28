"""
DIARIZATION_V2_MODE off/shadow/on terhadap golden jalur lama.

off & shadow WAJIB identik dengan golden (output PDF tidak berubah).
on punya ekspektasi v2 sendiri (Fase 2: role cue+dominansi, collapse, smoothing).
"""
import json
import re

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
    assert v2["source"] == "shadow" and v2["version"] == 2
    assert v2["n_segments"] == len(v2["segments"]) > 0
    dumped = json.dumps(v2)
    assert "Bank Mega" not in dumped and "Halo" not in dumped  # tidak ada teks transkrip


def _blocks(text):
    """Teks PDF -> [(speaker, isi)] per baris ber-timestamp."""
    out, spk = [], None
    for line in text.splitlines():
        m = re.match(r"^\[?(Agent|Customer) \[NETRAL\]\]?:$", line)
        if m:
            spk = m.group(1)
            continue
        m = re.match(r"^\[\d\d:\d\d\.\d\d -> \d\d:\d\d\.\d\d\] (.*)$", line)
        if m:
            out.append((spk, m.group(1)))
    return out


def _run_on(name, synthetic_wav, monkeypatch):
    monkeypatch.setattr(stt_config, "DIARIZATION_V2_MODE", "on")
    monkeypatch.setattr(stt_config, "SCRIBE_DIAR_MODE", "fixed2")
    text, _, meta = _run(name, synthetic_wav, monkeypatch)
    assert meta["diarization_v2"]["source"] == "scribe_api"
    assert meta["elevenlabs_raw_response"]["transcription_id"] == "synthetic"
    return _blocks(text), meta["diarization_v2"]


def test_on_nasabah_bicara_duluan_role_benar(synthetic_wav, monkeypatch):  # R1
    blocks, v2 = _run_on("customer_first", synthetic_wav, monkeypatch)
    assert blocks == [
        ("Customer", "Halo?"),
        ("Agent", "Selamat siang Bapak, perkenalkan saya Rina dari Bank Mega."),
        ("Customer", "Iya, ada apa ya?"),
        ("Agent", "Izin minta waktunya sebentar untuk informasi produk Cashline."),
        ("Customer", "Boleh."),
    ]
    assert (v2["agent_raw"], v2["role_confident"], v2["role_basis"]) == ("speaker_0", True, "cue+dominance")


def test_on_flip_satu_kata_dihaluskan(synthetic_wav, monkeypatch):  # F1
    blocks, v2 = _run_on("single_word_flip", synthetic_wav, monkeypatch)
    assert blocks[0] == ("Agent", "Selamat pagi Ibu, saya dari Bank Mega ingin menyampaikan penawaran.")
    assert v2["n_smoothed"] == 1


def test_on_cluster_ketiga_dilebur(synthetic_wav, monkeypatch):  # F3
    blocks, v2 = _run_on("three_speakers", synthetic_wav, monkeypatch)
    assert v2["n_spk_raw"] == 3 and v2["n_collapsed"] == 3
    assert blocks[0] == ("Agent", "Selamat sore, saya Dimas dari Bank Mega.")
    assert {spk for spk, _ in blocks} == {"Agent", "Customer"}


def test_on_satu_speaker_tidak_confident(synthetic_wav, monkeypatch):  # S1
    blocks, v2 = _run_on("one_speaker", synthetic_wav, monkeypatch)
    assert {spk for spk, _ in blocks} == {"Agent"}
    assert (v2["role_confident"], v2["role_basis"]) == (False, "single_speaker")


def test_shadow_tidak_menggagalkan_job_saat_raw_rusak(synthetic_wav, monkeypatch):
    from model.speech_stt.diarization_v2 import shadow_from_raw

    assert shadow_from_raw(None) is None
    broken = shadow_from_raw({"words": [{"type": "word", "text": "x", "start": "bukan-angka", "end": 1}]})
    assert broken == {"version": 2, "source": "shadow", "error": "ValueError"}
