"""assign_roles: cue skrip agent + dominansi kata -> Agent & role_confident."""
from model.speech_stt.diarization_v2.roles import assign_roles, load_cues
from model.speech_stt.diarization_v2.types import Utterance, Word


def _utts(spec):
    """spec: list of (speaker, text). Jumlah Word = jumlah token teks."""
    out, t = [], 0.0
    for i, (spk, text) in enumerate(spec):
        ws = [Word(tok, t + k, t + k + 0.5, spk) for k, tok in enumerate(text.split())]
        out.append(Utterance(i, ws[0].start, ws[-1].end, spk, text, ws))
        t = ws[-1].end + 1.0
    return out


FILLER = "jadi untuk produk ini ada beberapa ketentuan yang berlaku bagi pemegang kartu"


def test_nasabah_bicara_duluan_intro_menentukan_agent():
    d = assign_roles(_utts([
        ("s1", "Halo?"),
        ("s0", "Selamat siang Bapak, perkenalkan saya Rina dari Bank Mega."),
        ("s1", "Iya."),
        ("s0", FILLER),
    ]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", True, "cue+dominance")
    assert d.evidence["hits"]["s0"]["intro"] == 1  # satu hit per utterance
    assert d.evidence["score"]["s0"] == 2


def test_panggilan_susulan_frasa_skrip():
    d = assign_roles(_utts([
        ("s1", "Halo."),
        ("s0", "Pak Budi, dengan Rina kembali yang kemarin menghubungi Bapak."),
        ("s0", FILLER),
        ("s1", "Oh iya."),
    ]))
    assert (d.agent, d.role_confident) == ("s0", True)


def test_tanpa_cue_pakai_dominan_dan_tidak_confident():
    d = assign_roles(_utts([("s1", "Halo."), ("s0", FILLER), ("s1", "Iya.")]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", False, "dominance")


def test_cue_seri_tidak_confident():
    d = assign_roles(_utts([
        ("s0", "kami informasikan " + FILLER),
        ("s1", "terima kasih atas waktunya"),
    ]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", False, "dominance")


def test_intro_kuat_mengalahkan_dominansi():
    # Nasabah bicara panjang; agent sedikit kata tapi memperkenalkan diri.
    d = assign_roles(_utts([
        ("s0", "Perkenalkan saya Rina dari Bank Mega."),
        ("s1", FILLER + " " + FILLER),
    ]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", True, "cue")


def test_cue_lemah_berlawanan_dominansi_tidak_confident():
    d = assign_roles(_utts([
        ("s0", "apakah bapak setuju"),
        ("s1", FILLER + " " + FILLER),
    ]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", False, "cue_vs_dominance")


def test_cue_nasabah_bernilai_negatif():
    d = assign_roles(_utts([("s1", "Halo ini siapa?"), ("s0", FILLER), ("s1", "oh")]))
    assert d.evidence["score"]["s1"] == -1
    assert (d.agent, d.role_confident, d.basis) == ("s0", True, "cue+dominance")


def test_intro_di_luar_jendela_tidak_dihitung():
    spec = [("s1", "iya")] + [("s0" if i % 2 else "s1", "baik") for i in range(9)] + \
           [("s1", "perkenalkan saya dari Bank Mega")]
    d = assign_roles(_utts(spec))
    assert d.evidence["hits"]["s1"].get("intro", 0) == 0


def test_satu_speaker_dan_kosong():
    d = assign_roles(_utts([("s0", "halo"), ("s0", "perkenalkan saya Rina")]))
    assert (d.agent, d.role_confident, d.basis) == ("s0", False, "single_speaker")
    assert assign_roles([]).basis == "empty"


def test_evidence_tanpa_teks():
    d = assign_roles(_utts([("s1", "Halo?"), ("s0", "perkenalkan saya Rina dari Bank Mega")]))
    assert "Rina" not in repr(d.evidence) and "Halo" not in repr(d.evidence)


def test_cues_yaml_valid():
    cues = load_cues()
    assert cues["version"] == 1 and cues["intro_window"] == 8
    assert len(cues["intro"]) == 3 and len(cues["script"]) == 10
