"""
Golden test end-to-end SpeechSTT.transcribe_with_diarization() untuk backend
ElevenLabs (engine.py:303-329 + formatter). Output teks (bahan PDF ke App B)
dan metadata dibandingkan dengan file golden di tests/fixtures/golden/.

Gender detection dan DSP fraud dimatikan supaya golden hanya mengunci
diarization + format. Baris yang berubah tiap run (Generated, Proc. Time)
dinormalisasi.

Regenerasi golden (HANYA saat perubahan jalur lama memang disengaja):
    UPDATE_GOLDEN=1 pytest tests/test_legacy_engine_golden.py
"""
import json
import os
import re
from types import SimpleNamespace

import pytest

from model.speech_stt import engine as engine_mod
from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt.engine import SpeechSTT
from tests.conftest import FIXTURES, load_scribe_response

GOLDEN = FIXTURES / "golden"
CASES = ["customer_first", "single_word_flip", "three_speakers", "one_speaker"]
META_KEYS = ["mode", "detected_language", "speaker_count", "speaker_durations", "talk_time", "reattributed_count"]


def _normalize(text: str) -> str:
    text = re.sub(r"^Generated : .*$", "Generated : <ts>", text, flags=re.M)
    return re.sub(r"^Proc\. Time: .*$", "Proc. Time: <t>", text, flags=re.M)


def _run(name, audio_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "run_gender_detection", lambda *a, **k: {})
    monkeypatch.setattr(engine_mod, "DSP_FRAUD_ENABLED", False)
    stt = SpeechSTT("unused", max_file_size=10, asr_backend="elevenlabs")
    asr = ElevenLabsBackend("scribe_v2", "dummy-key", num_speakers=2)
    response = load_scribe_response(name)
    asr._client = SimpleNamespace(speech_to_text=SimpleNamespace(convert=lambda **kw: response))
    stt._asr = asr
    text, confidence, meta = stt.transcribe_with_diarization(audio_path, "id", original_filename="%s.wav" % name)
    return _normalize(text), confidence, meta


@pytest.mark.parametrize("name", CASES)
def test_golden_jalur_scribe_lama(name, synthetic_wav, monkeypatch):
    text, confidence, meta = _run(name, synthetic_wav, monkeypatch)
    meta_subset = {k: meta.get(k) for k in META_KEYS}
    txt_path, meta_path = GOLDEN / ("%s.txt" % name), GOLDEN / ("%s.meta.json" % name)

    if os.environ.get("UPDATE_GOLDEN") == "1":
        txt_path.write_text(text + "\n")
        meta_path.write_text(json.dumps(meta_subset, indent=1, sort_keys=True) + "\n")

    assert text + "\n" == txt_path.read_text()
    assert json.loads(json.dumps(meta_subset)) == json.loads(meta_path.read_text())
    assert confidence == 1.0
    assert meta["elevenlabs_raw_response"]["transcription_id"] == "synthetic"
