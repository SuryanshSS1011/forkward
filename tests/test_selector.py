import pytest

from forkward import selector
from forkward.data import Hunk
from tests.test_core import needs_data


def _row(i: int, flag: bool, correct: set[str]) -> selector.Row:
    outs = {"keep_vendor": ("v",), "take_upstream": ("u",)}
    return selector.Row(f"h{i}", "x", {"flag": flag, "other": i % 2 == 0}, outs, frozenset(correct))


def test_rule_list_learns_a_feature_to_operator_rule():
    rows = [_row(i, True, {"take_upstream"}) for i in range(6)]
    rows += [_row(i + 6, False, {"keep_vendor"}) for i in range(10)]
    sel = selector.fit(rows, min_support=3, max_rules=4)
    preds = selector.predictions(sel, rows)
    assert all(p["correct"] for p in preds.values())
    assert any(r.cond == ("flag",) and r.op == "take_upstream" for r in sel.rules)
    assert sel.default.op == "keep_vendor"
    # Confidence is Laplace-smoothed train precision, so it stays below 1.
    assert all(0 < r.confidence < 1 for r in sel.rules)


def test_min_support_blocks_rules_on_tiny_groups():
    rows = [_row(0, True, {"take_upstream"})] + [
        _row(i + 1, False, {"keep_vendor"}) for i in range(5)
    ]
    sel = selector.fit(rows, min_support=3, max_rules=4)
    assert not any(r.op == "take_upstream" for r in sel.rules)


def test_features_come_from_visible_sides_and_diffs():
    h = Hunk("t", "train", "drivers/x.c", "", ("a;", "b;"), (), "", "")
    f = selector.features(h)
    assert f["upstream_empty"] and not f["vendor_empty"] and f["ext_c"] and not f["base_known"]


def test_selector_refuses_the_test_split():
    with pytest.raises(ValueError):
        selector.rows_for("test")


@needs_data
def test_selector_report_on_train_and_validation(monkeypatch):
    from forkward import data

    real = data.load_hunks

    def guarded(split, **kw):
        assert split != "test", "the selector must never load test hunks"
        return real(split, **kw)

    monkeypatch.setattr(selector, "load_hunks", guarded)
    res = selector.run(seed=0, target=0.2)
    assert res["train"]["n"] == 70 and res["validation"]["n"] == 45
    assert set(res["validation"]["per_class"]) == {"keep_vendor", "take_upstream", "merge"}
    assert res["halves"]["selection"] + res["halves"]["calibration"] == 45
    assert "p_two_sided" in res["validation_vs_keep_vendor"]["mcnemar"]
    assert res["abstention"]["min_error_free_accepted_needed"] == 14
    assert "worst_bound" in res["leave_one_vendor_out"]
    assert "rules" in selector.render(res)


@needs_data
def test_selector_compares_against_a_logged_team(monkeypatch, tmp_path):
    import asyncio

    from forkward import analysis, search
    from tests.test_core import _fake_llm

    _fake_llm(monkeypatch, tmp_path)
    monkeypatch.setattr(analysis, "RUNS", tmp_path)
    asyncio.run(search.search(0, run_id="team"))
    res = selector.run(seed=0, team_run="team")
    assert res["selector_vs_team"]["mcnemar"]["n"] == 45
