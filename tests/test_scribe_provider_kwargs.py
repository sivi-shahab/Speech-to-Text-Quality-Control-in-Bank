"""ScribeDiarizationProvider: parameter API per mode, retry, dan tanpa fallback diam-diam."""
from types import SimpleNamespace

import pytest
from elevenlabs.core.api_error import ApiError

from model.speech_stt.asr_backends import elevenlabs_backend as eleven_mod
from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt.diarization_v2 import ScribeDiarizationProvider, words_from_raw
from tests.conftest import load_scribe_dict, load_scribe_response


class _FakeSpeechToText:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def convert(self, **kwargs):
        assert hasattr(kwargs.pop("file"), "read")
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF")
    return str(path)


def _provider(outcomes=None, keyterms=None, **kw):
    be = ElevenLabsBackend("scribe_v2", "dummy-key", keyterms=keyterms)
    fake = _FakeSpeechToText(outcomes or [load_scribe_response("customer_first")])
    be._client = SimpleNamespace(speech_to_text=fake)
    return ScribeDiarizationProvider(be, **kw), fake, be


BASE = {"model_id": "scribe_v2", "language_code": "ind", "diarize": True, "timestamps_granularity": "word",
        "tag_audio_events": False, "request_options": {"timeout_in_seconds": 300}}


def test_mode_fixed2(audio):
    prov, fake, _ = _provider(keyterms=["Cashline"])
    prov.diarize(audio, "id")
    assert fake.calls == [{**BASE, "num_speakers": 2, "keyterms": ["Cashline"]}]


def test_mode_threshold_tidak_mengirim_num_speakers(audio):
    prov, fake, _ = _provider(mode="threshold", threshold=0.2)
    prov.diarize(audio, "id")
    assert fake.calls == [{**BASE, "diarization_threshold": 0.2}]


def test_mode_roles(audio):
    prov, fake, _ = _provider(mode="roles")
    prov.diarize(audio, "id")
    assert fake.calls == [{**BASE, "num_speakers": 2, "detect_speaker_roles": True}]


@pytest.mark.parametrize("value", [True, False])
def test_enable_logging_dikirim_bila_dikonfigurasi(audio, value):
    prov, fake, _ = _provider(enable_logging=value)
    prov.diarize(audio, "id")
    assert fake.calls[0]["enable_logging"] is value


def test_timeout_diteruskan_dan_bahasa_iso3_dipertahankan(audio):
    prov, fake, _ = _provider(timeout_sec=120.0)
    prov.diarize(audio, "ind")
    assert fake.calls[0]["request_options"] == {"timeout_in_seconds": 120}
    assert fake.calls[0]["language_code"] == "ind"


@pytest.mark.parametrize("kw, msg", [
    ({"mode": "auto"}, "tidak dikenal"),
    ({"mode": "threshold"}, "butuh SCRIBE_DIAR_THRESHOLD"),
    ({"mode": "threshold", "threshold": 1.5}, "di antara 0 dan 1"),
])
def test_konfigurasi_tidak_valid_ditolak(kw, msg):
    with pytest.raises(ValueError, match=msg):
        _provider(**kw)


def test_tanpa_fallback_typeerror(audio):
    prov, fake, _ = _provider(outcomes=[TypeError("unexpected keyword 'diarization_threshold'")])
    with pytest.raises(TypeError):
        prov.diarize(audio, "id")
    assert len(fake.calls) == 1


def test_retry_429_lalu_sukses(audio, monkeypatch):
    monkeypatch.setattr(eleven_mod.time, "sleep", lambda s: None)
    prov, fake, _ = _provider(outcomes=[ApiError(status_code=429), load_scribe_response("customer_first")])
    prov.diarize(audio, "id")
    assert len(fake.calls) == 2


def test_error_non_429_langsung_dilempar(audio):
    prov, fake, _ = _provider(outcomes=[ApiError(status_code=401)])
    with pytest.raises(ApiError):
        prov.diarize(audio, "id")
    assert len(fake.calls) == 1


def test_raw_response_disimpan_di_backend_dan_meta_tanpa_teks(audio):
    prov, _, be = _provider(keyterms=["Cashline"])
    result = prov.diarize(audio, "id")
    assert be.last_raw_response["transcription_id"] == "synthetic"
    assert result.raw_response is be.last_raw_response
    assert result.meta["params"]["n_keyterms"] == 1
    assert "keyterms" not in result.meta["params"]


def test_words_from_raw_buang_spacing_dan_audio_event():
    words = words_from_raw(load_scribe_dict("three_speakers"))
    assert all(w.text.strip() and not w.text.startswith("(") for w in words)
    assert words[0].text == "Selamat" and words[0].speaker == "speaker_0"
    assert words_from_raw(None) == []
