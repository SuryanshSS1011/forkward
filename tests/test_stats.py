import asyncio

import pytest

from forkward import analysis, search, stats
from tests.test_core import _fake_llm, needs_data


def _arms(a_only: int, b_only: int, both: int = 20, neither: int = 5):
    a, b, i = {}, {}, 0
    for va, vb, k in ((1, 0, a_only), (0, 1, b_only), (1, 1, both), (0, 0, neither)):
        for _ in range(k):
            a[f"h{i}"], b[f"h{i}"] = float(va), float(vb)
            i += 1
    return a, b


def test_exact_mcnemar_matches_the_review_numbers():
    # Frozen vs keep-vendor on test: 10 and 7 discordant hunks give p = 0.63.
    a, b = _arms(7, 10)
    m = stats.mcnemar(a, b)
    assert (m["a_only"], m["b_only"]) == (7, 10)
    assert m["p_two_sided"] == pytest.approx(0.6291, abs=1e-4)
    assert stats.mcnemar(*_arms(0, 4))["p_two_sided"] == pytest.approx(0.125)
    assert stats.mcnemar(*_arms(0, 4))["p_b_better"] == pytest.approx(0.0625)
    assert stats.mcnemar(*_arms(2, 5))["p_two_sided"] == pytest.approx(0.4531, abs=1e-4)
    assert stats.mcnemar(*_arms(0, 0))["p_two_sided"] == 1.0


def test_holm_adjusts_step_down():
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})


def test_wilson_interval():
    lo, hi = stats.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert stats.wilson(0, 10)[0] == pytest.approx(0.0) and stats.wilson(0, 10)[1] == pytest.approx(
        0.2775, abs=1e-3
    )
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_cluster_bootstrap_is_wider_when_gains_come_from_one_cluster():
    a, b = _arms(0, 8, both=24, neither=8)
    tags = sorted(a, key=lambda t: int(t[1:]))
    # All eight gains sit in one cluster, the rest spread over many small clusters.
    clusters = {t: ("hot" if i < 8 else f"c{i // 2}") for i, t in enumerate(tags)}
    hunk = stats.compare(a, b, resamples=2000)
    cl = stats.cluster_bootstrap(a, b, clusters, resamples=2000)
    assert cl["delta"] == pytest.approx(hunk["delta"])
    assert cl["clusters"] == 1 + 16
    assert (cl["hi"] - cl["lo"]) > (hunk["hi"] - hunk["lo"])


def test_compare_and_render_print_discordant_counts_and_exact_p():
    a, b = _arms(2, 6)
    c = stats.compare(a, b, {"file": {t: t[:2] for t in a}}, resamples=500)
    stats.adjust({"x": c, "missing": None})
    line = stats.render("x", c)
    assert "discordant 2 vs 6" in line and "exact p=" in line and "holm p=" in line
    assert "file CI" in line and {"delta", "lo", "hi"} <= set(c)


@needs_data
def test_ladder_reports_exact_tests_from_a_logged_run(monkeypatch, tmp_path):
    _fake_llm(monkeypatch, tmp_path)
    monkeypatch.setattr(analysis, "RUNS", tmp_path)
    asyncio.run(search.search(1, run_id="ladder"))
    res = analysis.ladder("ladder", "validation")
    step = res["ladder"][1]["vs_previous"]
    assert {"a_only", "b_only", "p_two_sided", "p_holm"} <= set(step["mcnemar"])
    assert set(step["cluster_bootstrap"]) == {"merge", "file"}
    assert res["attribution"]["frozen_vs_keep_vendor"]["mcnemar"]["n"] == 45
