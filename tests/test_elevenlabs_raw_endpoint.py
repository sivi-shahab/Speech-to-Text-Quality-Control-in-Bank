"""
Endpoint POST /api/v1/speech/stt/elevenlabs (body = bytes audio mentah).
API ElevenLabs di-stub: _call_api mengembalikan fixture Scribe.
"""
import os

import pytest
from elevenlabs.core.api_error import ApiError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import elevenlabs_stt_routes
from tests.conftest import load_scribe_response

URL = "/api/v1/speech/stt/elevenlabs"


class _FakeBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def _call_api(self, audio_path, language, diarize):
        with open(audio_path, "rb") as f:
            body = f.read()
        self.calls.append(dict(
            path=audio_path, body=body, language=language, diarize=diarize,
            exists_during_call=os.path.exists(audio_path),
        ))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def client_with(monkeypatch):
    def _make(result):
        fake = _FakeBackend(result)
        monkeypatch.setattr(elevenlabs_stt_routes, "get_elevenlabs_backend", lambda: fake)
        app = FastAPI()
        app.include_router(elevenlabs_stt_routes.router, prefix="/api/v1")
        return TestClient(app), fake
    return _make


def test_body_mentah_diteruskan_apa_adanya_dan_temp_dihapus(client_with):
    client, fake = client_with(load_scribe_response("customer_first"))
    audio = b"ID3\x04fake-mp3-bytes"

    r = client.post(URL, content=audio, headers={"Content-Type": "audio/mpeg"})

    assert r.status_code == 200
    call = fake.calls[0]
    assert call["body"] == audio
    assert call["exists_during_call"]
    assert call["path"].endswith(".mp3")
    assert call["language"] == "id" and call["diarize"] is False
    assert not os.path.exists(call["path"])
    data = r.json()
    assert data["text"].startswith("Halo? Selamat siang Bapak")
    assert data["language"] == "ind"
    assert data["audio_duration_secs"] == 30.0
    assert data["segments"] is None


def test_content_type_tak_dikenal_tetap_diterima(client_with):
    client, fake = client_with(load_scribe_response("one_speaker"))

    r = client.post(URL, content=b"\x00\x01opaque", headers={"Content-Type": "application/x-whatever"})

    assert r.status_code == 200
    assert fake.calls[0]["path"].endswith(".bin")


def test_diarize_label_mengikuti_engine_bicara_duluan_jadi_agent(client_with):
    client, fake = client_with(load_scribe_response("customer_first"))

    r = client.post(URL + "?diarize=true&language=en", content=b"RIFFxxxxWAVE")

    assert r.status_code == 200
    assert fake.calls[0]["diarize"] is True and fake.calls[0]["language"] == "en"
    segs = r.json()["segments"]
    assert [s["speaker_id"] for s in segs] == ["speaker_1", "speaker_0", "speaker_1", "speaker_0", "speaker_1"]
    assert [s["speaker"] for s in segs] == ["Agent", "Customer", "Agent", "Customer", "Agent"]
    assert segs[0]["text"] == "Halo?"


def test_body_kosong_400(client_with):
    client, fake = client_with(load_scribe_response("one_speaker"))

    r = client.post(URL, content=b"")

    assert r.status_code == 400
    assert fake.calls == []


def test_error_api_elevenlabs_dipetakan(client_with):
    client, fake = client_with(ApiError(status_code=400, body={"detail": "invalid audio"}))

    r = client.post(URL, content=b"garbage")

    assert r.status_code == 400
    assert "invalid audio" in r.json()["detail"]
    assert not os.path.exists(fake.calls[0]["path"])


def test_429_habis_retry_jadi_503(client_with):
    client, _ = client_with(RuntimeError("[ElevenLabs] Gagal setelah retry: 429"))

    r = client.post(URL, content=b"audio")

    assert r.status_code == 503


def test_api_key_kosong_503(monkeypatch):
    monkeypatch.setattr(elevenlabs_stt_routes, "_backend", None)
    monkeypatch.setattr(elevenlabs_stt_routes.stt_config, "ELEVENLABS_API_KEY", None)
    app = FastAPI()
    app.include_router(elevenlabs_stt_routes.router, prefix="/api/v1")

    r = TestClient(app).post(URL, content=b"audio")

    assert r.status_code == 503
    assert "ELEVENLABS_API_KEY" in r.json()["detail"]


# ── analysis=true: diarization ElevenLabs -> engine.analyze_speakers() ──

class _FakeEngine:
    """Hanya analyze_speakers() yang boleh dipanggil — bukan transcribe_*."""
    asr_backend_name = "whisper"  # endpoint harus jalan apa pun backend server

    def __init__(self):
        self.calls = []

    def analyze_speakers(self, audio_path, aligned, diar_segs):
        self.calls.append(dict(path=audio_path, aligned=aligned, diar_segs=diar_segs))
        return {
            "speaker_genders":  {"Agent": "female", "Customer": "male"},
            "segment_emotions": ["POSITIF"] * len(aligned),
            "speaker_stats":    {"Agent": {"POSITIF": 3, "NEGATIF": 0, "NETRAL": 0, "TOTAL": 3}},
            "fraud_alerts":     [],
            "talk_time":        {"Agent": "60%"},
        }


@pytest.fixture
def analysis_client(monkeypatch, tmp_path):
    converted = tmp_path / "converted.wav"

    def _fake_convert(src, tag):
        converted.write_bytes(open(src, "rb").read())
        return str(converted)

    def _make(result, convert=_fake_convert):
        fake_api = _FakeBackend(result)
        engine = _FakeEngine()
        monkeypatch.setattr(elevenlabs_stt_routes, "get_elevenlabs_backend", lambda: fake_api)
        monkeypatch.setattr(elevenlabs_stt_routes, "convert_audio_for_stt", convert)
        app = FastAPI()
        app.state.stt_instance = engine
        app.include_router(elevenlabs_stt_routes.router, prefix="/api/v1")
        return TestClient(app), fake_api, engine, converted
    return _make


def test_analysis_diarization_elevenlabs_lalu_gender_emotion_dari_engine(analysis_client):
    client, api, engine, converted = analysis_client(load_scribe_response("customer_first"))

    r = client.post(URL + "?analysis=true", content=b"RIFFxxxxWAVE")

    assert r.status_code == 200
    assert api.calls[0]["diarize"] is True                # diarization dari ElevenLabs
    assert api.calls[0]["path"] == str(converted)          # pakai WAV hasil konversi
    call = engine.calls[0]
    assert call["path"] == str(converted)                  # gender/emotion di WAV yang sama
    assert [s["speaker_original"] for s in call["diar_segs"]] == [
        "speaker_1", "speaker_0", "speaker_1", "speaker_0", "speaker_1"]
    assert not converted.exists()
    data = r.json()
    assert data["text"].startswith("Halo? Selamat siang Bapak")
    assert [s["speaker"] for s in data["segments"]] == [seg.speaker for seg in call["aligned"]]
    assert data["segments"][0]["speaker"] == "Agent"
    assert all(s["emotion"] == "POSITIF" for s in data["segments"])
    a = data["analysis"]
    assert a["speaker_genders"] == {"Agent": "female", "Customer": "male"}
    assert a["speaker_emotions"]["Agent"]["POSITIF"] == 3
    assert a["talk_time"] == {"Agent": "60%"}


def test_analysis_konversi_gagal_fallback_ke_file_asli(analysis_client):
    def _boom(src, tag):
        raise RuntimeError("ffmpeg error")

    client, api, engine, _ = analysis_client(load_scribe_response("one_speaker"), convert=_boom)

    r = client.post(URL + "?analysis=true", content=b"audio")

    assert r.status_code == 200
    assert "eleven_raw_" in api.calls[0]["path"]
    assert engine.calls[0]["path"] == api.calls[0]["path"]


def test_analysis_error_api_elevenlabs_dipetakan_dan_engine_tidak_dipanggil(analysis_client):
    client, _, engine, converted = analysis_client(ApiError(status_code=500, body="boom"))

    r = client.post(URL + "?analysis=true", content=b"audio")

    assert r.status_code == 502
    assert engine.calls == []
    assert not converted.exists()


# ── engine.analyze_speakers() bisa dipanggil terpisah, backend ASR apa pun ──

def test_analyze_speakers_jalan_di_engine_whisper_tanpa_transcribe(synthetic_wav, monkeypatch):
    from model.speech_stt import engine as engine_mod
    from model.speech_stt.engine import SpeechSTT, scribe_segments_to_aligned

    seen = {}

    def _fake_gender(audio_path, diar_segs, pitch_history):
        seen["speakers"] = sorted({s["speaker"] for s in diar_segs})
        return {"Agent": "female", "Customer": "male"}

    monkeypatch.setattr(engine_mod, "run_gender_detection", _fake_gender)
    monkeypatch.setattr(engine_mod, "DSP_FRAUD_ENABLED", False)
    raw = elevenlabs_stt_routes.ElevenLabsBackend._extract_segments_with_speaker(
        load_scribe_response("customer_first"))
    diar_segs, aligned, before_merge = scribe_segments_to_aligned(raw)

    stt = SpeechSTT("unused", max_file_size=10, asr_backend="whisper")
    out = stt.analyze_speakers(synthetic_wav, aligned, diar_segs)

    assert before_merge == 5
    assert seen["speakers"] == ["Agent", "Customer"]
    assert out["speaker_genders"] == {"Agent": "female", "Customer": "male"}
    assert len(out["segment_emotions"]) == len(aligned)
    assert sum(st["TOTAL"] for st in out["speaker_stats"].values()) == len(aligned)
    assert out["talk_time"]["agent"]["speaker"] == "Agent"
    assert out["talk_time"]["total_sec"] == 30.0          # durasi synthetic_wav
