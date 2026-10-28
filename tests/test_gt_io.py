"""tools/diar_eval: gt_io (CSV/JSON/RTTM, validasi, agreement) dan sample (strata)."""
import json

import pytest

from tests.conftest import load_scribe_dict
from tools.diar_eval import gt_io, sample


@pytest.mark.parametrize("value, sec", [("12.5", 12.5), ("12,5", 12.5), ("01:05.20", 65.2), ("1:05,2", 65.2),
                                        (" 3 ", 3.0)])
def test_parse_time(value, sec):
    assert gt_io.parse_time(value) == pytest.approx(sec)


def test_parse_time_tidak_valid():
    with pytest.raises(ValueError):
        gt_io.parse_time("dua detik")


def test_draft_label_netral_tanpa_role():
    rows = gt_io.draft_rows(load_scribe_dict("customer_first"))
    assert [r["speaker"] for r in rows] == ["SPK_A", "SPK_B", "SPK_A", "SPK_B", "SPK_A"]
    assert rows[0]["text"] == "Halo?" and rows[0]["start"] == "0.50"


def _write(path, text):
    path.write_text(text, encoding="utf-8-sig")


def test_import_csv_excel_titik_koma_dan_validasi(tmp_path):
    src, dst = tmp_path / "draft", tmp_path / "gt"
    src.mkdir()
    _write(src / "s1.segments.csv",
           "seg_id;start;end;speaker;text\n0;0,5;0,8;Customer;Halo?\n1;00:01.50;00:04.60;Agent;perkenalan\n;;;;\n")
    _write(src / "s1.items.csv", "item;start;end;speaker;catatan\nSC_CL_2;1,5;4,6;Agent;\n")
    _write(src / "s2.segments.csv", "seg_id,start,end,speaker,text\n0,0.5,0.8,SPK_A,x\n1,3,2,Agent,y\n")
    rep = gt_io.import_dir(src, dst)
    assert rep["s1"] == []
    assert any("SPK_A" in e for e in rep["s2"]) and any("end" in e for e in rep["s2"])
    gt = json.loads((dst / "s1.json").read_text())
    assert gt["segments"] == [{"start": 0.5, "end": 0.8, "speaker": "Customer"},
                              {"start": 1.5, "end": 4.6, "speaker": "Agent"}]
    assert gt["items"] == [{"start": 1.5, "end": 4.6, "speaker": "Agent", "item": "SC_CL_2"}]
    assert "perkenalan" not in (dst / "s1.json").read_text()  # GT tanpa teks
    assert not (dst / "s2.json").exists()


def test_validate_gt():
    assert gt_io.validate_gt({"stem": "s", "segments": [{"start": 0, "end": 1, "speaker": "Agent"}]}) == []
    errs = gt_io.validate_gt({"stem": "", "segments": [{"start": 2, "end": 1, "speaker": "X"}],
                              "items": [{"start": 0, "end": 1, "speaker": "Agent"}]})
    assert len(errs) == 4


def test_rttm_bolak_balik():
    gts = [{"stem": "s1", "segments": [{"start": 0.5, "end": 0.8, "speaker": "Customer"},
                                       {"start": 1.5, "end": 4.6, "speaker": "Agent"}]}]
    text = gt_io.to_rttm(gts)
    assert text.splitlines()[0] == "SPEAKER s1 1 0.500 0.300 <NA> <NA> Customer <NA> <NA>"
    assert gt_io.from_rttm(text)["s1"] == gts[0]["segments"]


def test_agreement(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    segs = [{"start": 0, "end": 10, "speaker": "Agent"}, {"start": 10, "end": 20, "speaker": "Customer"}]
    (a / "s1.json").write_text(json.dumps({"stem": "s1", "segments": segs}))
    (b / "s1.json").write_text(json.dumps({"stem": "s1", "segments": segs[:1] + [{**segs[1], "speaker": "Agent"}]}))
    out = gt_io.agreement(a, b)
    assert out["n_common"] == 1 and out["role_agreement_pooled"] == 0.5


# ── sample.py ──

M = {"cue_appb": "s0", "legacy_agent": "s0", "cue_raw_labels": 1, "minor_words": 100}


@pytest.mark.parametrize("over, expected", [
    ({"legacy_agent": "s1"}, "swap"),
    ({"cue_raw_labels": 2}, "cue_2label"),
    ({"minor_words": 31}, "minor_low"),
    ({"cue_appb": None}, "excluded"),
    ({}, "normal"),
])
def test_stratum(over, expected):
    assert sample.stratum({**M, **over}) == expected


def test_stratum_undecided_opsional():
    assert sample.stratum({**M, "cue_appb": None}, extra=("undecided",)) == "undecided"


def test_draw_deterministik_dan_double_label():
    rows = [{"stem": "c%02d" % i, "stratum": s, "double_label": False}
            for i, s in enumerate(["swap"] * 15 + ["normal"] * 3)]
    p1 = sample.draw([dict(r) for r in rows], 10, 7, ("swap", "normal"), 5)
    p2 = sample.draw([dict(r) for r in rows], 10, 7, ("swap", "normal"), 5)
    assert [r["stem"] for r in p1] == [r["stem"] for r in p2]
    assert sum(r["stratum"] == "swap" for r in p1) == 10 and sum(r["stratum"] == "normal" for r in p1) == 3
    assert sum(r["double_label"] for r in p1) == 5


def test_build_rows_filter_label_dan_durasi():
    entries = [{"stem": n, "raw": load_scribe_dict(n)} for n in ("customer_first", "single_word_flip")]
    rows = sample.build_rows(entries, {"customer_first": "NQ", "single_word_flip": "PASS"}, "NQ", None)
    assert [(r["stem"], r["stratum"]) for r in rows] == [("customer_first", "swap")]
    assert sample.build_rows(entries, {}, None, 10.0) == []  # durasi fixture 30 s
    assert "text" not in json.dumps(rows)
