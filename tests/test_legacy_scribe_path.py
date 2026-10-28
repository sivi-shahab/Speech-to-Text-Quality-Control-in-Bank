"""
Characterization test jalur Scribe LAMA (sebelum diarization v2).

Test ini MENGUNCI perilaku sekarang — termasuk perilaku yang salah menurut
docs/diarization-audit.md (R1, F1, F3, S1, A4). Tujuannya: memastikan jalur
lama tidak berubah saat flag DIARIZATION_V2_MODE=off. Jangan "memperbaiki"
assertion di sini; perbaikan masuk ke jalur v2 dengan test-nya sendiri.
"""
from types import SimpleNamespace

from model.speech_stt.alignment.pure_alignment import merge_same_speaker_simple
from model.speech_stt.asr_backends.elevenlabs_backend import ElevenLabsBackend
from model.speech_stt.segment import SpeakerSegment, map_speakers_to_binary
from tests.conftest import load_scribe_response


def _segments(name):
    return ElevenLabsBackend._extract_segments_with_speaker(load_scribe_response(name))


# ── Ekstraksi segmen dari words[] (elevenlabs_backend.py:286-322) ──

def test_response_sdk_tidak_punya_segments_jadi_selalu_fallback_words():
    assert getattr(load_scribe_response("customer_first"), "segments", None) is None
    segs = _segments("customer_first")
    assert [s["speaker"] for s in segs] == ["speaker_1", "speaker_0", "speaker_1", "speaker_0", "speaker_1"]
    assert segs[0]["text"] == "Halo?"
    assert segs[0]["speaker_name"] is None


def test_spacing_dan_audio_event_diabaikan():
    segs = _segments("three_speakers")
    assert all("(tertawa)" not in s["text"] for s in segs)
    assert all(s["text"] == s["text"].strip() for s in segs)


def test_flip_satu_kata_memecah_kalimat_jadi_tiga_segmen():  # temuan F1
    segs = _segments("single_word_flip")
    assert [(s["speaker"], s["text"]) for s in segs[:3]] == [
        ("speaker_0", "Selamat pagi Ibu, saya"),
        ("speaker_1", "dari"),
        ("speaker_0", "Bank Mega ingin menyampaikan penawaran."),
    ]


# ── Role mapping (segment.py:26-58) ──

def test_bicara_pertama_jadi_agent_walau_itu_nasabah():  # temuan R1
    mapped = map_speakers_to_binary(_segments("customer_first"))
    assert mapped[0]["speaker_original"] == "speaker_1"
    assert mapped[0]["speaker"] == "Agent"
    intro = next(m for m in mapped if "perkenalkan" in m["text"])
    assert intro["speaker"] == "Customer"


def test_speaker_ketiga_otomatis_jadi_customer():  # temuan F3
    mapped = map_speakers_to_binary(_segments("three_speakers"))
    spk2 = [m for m in mapped if m["speaker_original"] == "speaker_2"]
    assert spk2 and all(m["speaker"] == "Customer" for m in spk2)


def test_satu_speaker_semua_jadi_agent():  # temuan S1
    mapped = map_speakers_to_binary(_segments("one_speaker"))
    assert {m["speaker"] for m in mapped} == {"Agent"}


def test_map_speakers_kosong():
    assert map_speakers_to_binary([]) == []


# ── Merge (pure_alignment.py:171-205) ──

def test_merge_hanya_speaker_sama_dengan_gap_maks():
    segs = [
        SpeakerSegment(0.0, 1.0, "a", "Agent"),
        SpeakerSegment(1.5, 2.0, "b", "Agent"),     # gap 0.5 -> merge
        SpeakerSegment(2.7, 3.0, "c", "Agent"),     # gap 0.7 -> tidak
        SpeakerSegment(3.1, 3.5, "d", "Customer"),
    ]
    out = merge_same_speaker_simple(segs, max_gap_sec=0.6)
    assert [(s.start, s.end, s.text, s.speaker) for s in out] == [
        (0.0, 2.0, "a b", "Agent"), (2.7, 3.0, "c", "Agent"), (3.1, 3.5, "d", "Customer"),
    ]


# ── Parameter API yang dikirim (elevenlabs_backend.py:131-182) ──

class _FakeSpeechToText:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append({k: v for k, v in kwargs.items() if k != "file"})
        return self.response


def _backend_with_fake(response, **kw):
    be = ElevenLabsBackend("scribe_v2", "dummy-key", **kw)
    fake = _FakeSpeechToText(response)
    be._client = SimpleNamespace(speech_to_text=fake)
    return be, fake


def test_kwargs_api_sekarang(tmp_path):  # temuan S2, A4, P2
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    be, fake = _backend_with_fake(load_scribe_response("customer_first"),
                                  num_speakers=2, keyterms=["Cashline"], timeout_sec=300)
    be.transcribe_with_speakers(str(audio), "id")
    assert fake.calls == [{
        "model_id": "scribe_v2", "language_code": "id", "diarize": True,
        "tag_audio_events": False, "keyterms": ["Cashline"], "num_speakers": 2,
    }]
    assert be.last_raw_response["language_code"] == "ind"


def test_keyterms_dipotong_1000_dan_num_speakers_tidak_dikirim_tanpa_diarize(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    be, fake = _backend_with_fake(load_scribe_response("one_speaker"),
                                  keyterms=["k%d" % i for i in range(1200)])
    be.transcribe_full(str(audio), "id")
    assert len(fake.calls[0]["keyterms"]) == 1000
    assert "num_speakers" not in fake.calls[0]
    assert fake.calls[0]["diarize"] is False
