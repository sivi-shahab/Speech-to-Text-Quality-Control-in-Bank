"""tools/diar_eval: metrik GT, metrik proxy, cache, dan grid (tanpa DB/API)."""
import math
import os
import stat

import pytest

from model.speech_stt.diarization_v2.pipeline import DEFAULT_PARAMS
from tests.conftest import ROOT, load_scribe_dict
from tools.diar_eval import cache, grid, metrics, proxy_metrics

REF = [{"start": 0.0, "end": 10.0, "speaker": "Agent"}, {"start": 10.0, "end": 20.0, "speaker": "Customer"}]
SWAPPED = [{"start": 0.0, "end": 10.0, "speaker": "Customer"}, {"start": 10.0, "end": 20.0, "speaker": "Agent"}]
ALL_AGENT = [{"start": 0.0, "end": 20.0, "speaker": "Agent"}]


# ── metrics.py ──

def test_der_sempurna_dan_role_tertukar_tidak_menaikkan_der():
    assert metrics.der(REF, REF, collar=0.0)["der"] == pytest.approx(0.0)
    assert metrics.der(REF, SWAPPED, collar=0.0)["der"] == pytest.approx(0.0)


def test_der_satu_speaker_untuk_dua():
    d = metrics.der(REF, ALL_AGENT, collar=0.0)
    assert d["der"] == pytest.approx(0.5) and d["confusion"] == pytest.approx(10.0)


def test_role_accuracy_menangkap_role_tertukar():
    assert metrics.role_accuracy(REF, REF) == pytest.approx(1.0)
    assert metrics.role_accuracy(REF, SWAPPED) == pytest.approx(0.0)
    assert metrics.role_accuracy(REF, ALL_AGENT) == pytest.approx(0.5)
    assert math.isnan(metrics.role_accuracy([], REF))


def test_jer_sempurna():
    assert metrics.jer(REF, REF, collar=0.0) == pytest.approx(0.0)


def test_item_role_accuracy():
    items = [
        {"item": "SC_CL_2", "start": 1.0, "end": 4.0, "speaker": "Agent"},
        {"item": "SC_CL_2", "start": 11.0, "end": 12.0, "speaker": "Agent"},   # hipotesis: Customer
        {"item": "SC_CL_7", "start": 30.0, "end": 31.0, "speaker": "Agent"},   # tanpa ucapan
    ]
    out = metrics.item_role_accuracy(items, REF)
    assert out["SC_CL_2"] == {"n": 2, "correct": 1, "acc": 0.5}
    assert out["SC_CL_7"]["acc"] == 0.0


# ── proxy_metrics.py ──

def test_proxy_customer_first_legacy_tertukar_v2_sesuai_cue():
    r = proxy_metrics.evaluate_call(load_scribe_dict("customer_first"))
    assert r["legacy_agent"] == "speaker_1"
    assert r["v2_agent"] == "speaker_0" == r["cue_appb"] == r["dominant"]
    assert r["legacy_cue_under_customer"] is True and r["v2_cue_under_customer"] is False


def test_proxy_tanpa_kata():
    assert proxy_metrics.evaluate_call({"words": []}) is None


def test_proxy_aggregate_dan_tanpa_teks():
    rows = [proxy_metrics.evaluate_call(load_scribe_dict(n))
            for n in ("customer_first", "single_word_flip", "three_speakers", "one_speaker")]
    agg = proxy_metrics.aggregate(rows)
    assert agg["n_calls"] == 4
    assert agg["legacy_vs_cue_appb"]["n_decided"] == 4
    assert agg["legacy_vs_cue_appb"]["disagree_pct"] == 25.0
    assert agg["v2_vs_cue_appb_SIRKULAR"]["disagree_pct"] == 0.0
    assert "Bank Mega" not in repr(agg) and "Bank Mega" not in repr(rows)


# ── cache.py ──

def test_cache_menolak_path_di_dalam_repo():
    with pytest.raises(SystemExit, match="di dalam repo"):
        cache.cache_dir(str(ROOT / "tools" / "diar_eval" / "cache"))
    with pytest.raises(SystemExit, match="DIAR_EVAL_CACHE_DIR"):
        cache.cache_dir("")


def test_cache_tulis_baca_izin_dan_purge(tmp_path):
    root = cache.cache_dir(str(tmp_path / "c"))
    raw = load_scribe_dict("customer_first")
    cache.write_entry(root, "stem1", {"stem": "stem1", "raw": cache.slim_raw(raw)})
    cache.write_manifest(root, campaign="Cashline")
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root / "stem1.json").st_mode) == 0o600
    entries = list(cache.iter_entries(root))
    assert [e["stem"] for e in entries] == ["stem1"]
    assert set(entries[0]["raw"]["words"][0]) == set(cache.WORD_KEYS)
    cache.purge(root)
    assert not root.exists()


# ── grid.py ──

def test_grid_score_v2_mengalahkan_legacy_pada_customer_first():
    raw = load_scribe_dict("customer_first")
    gt = {"segments": [
        {"start": 0.5, "end": 0.8, "speaker": "Customer"},
        {"start": 1.5, "end": 4.6, "speaker": "Agent"},
        {"start": 5.5, "end": 6.85, "speaker": "Customer"},
        {"start": 7.5, "end": 10.25, "speaker": "Agent"},
        {"start": 12.0, "end": 12.3, "speaker": "Customer"},
    ]}
    v2 = grid.score([(gt, raw)], lambda r: grid.v2_segments(r, DEFAULT_PARAMS))
    legacy = grid.score([(gt, raw)], lambda r: [{"start": s["start"], "end": s["end"], "speaker": s["speaker"]}
                                                 for s in proxy_metrics.legacy_segments(r)])
    assert v2["role_accuracy"] == pytest.approx(1.0)
    assert legacy["role_accuracy"] == pytest.approx(0.0)
    assert v2["der"] == pytest.approx(legacy["der"])  # DER buta terhadap role tertukar
