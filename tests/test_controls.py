import asyncio

from forkward import agents, experiments, gate, score, search, store
from forkward.data import halves
from tests.test_core import _fake_llm, needs_data


def _outcomes(key, right: set[str]) -> dict[str, search.Outcome]:
    out = {}
    for t, e in key.items():
        lines = e.gt if t in right else ("wrong",)
        d = gate.Decision(lines, "op:x", 0.9, True, None, None, 1, 1, False, [])
        out[t] = search.Outcome(t, [], [], None, d, 1)
    return out


@needs_data
def test_promotion_needs_an_exact_one_sided_test_on_the_selection_half():
    key = score.load_key("validation")
    sel, cal = halves("validation", 0)
    sel_tags = sorted(t for t in key if t in sel)
    cal_tags = sorted(t for t in key if t in cal)
    base = set(sel_tags[:8])
    s = search.Strategy("s-a", ["p"], "pb-0", {}, False)
    inc = _outcomes(key, base)
    # Five extra wins on the selection half: one-sided p = 1/32.
    ch = _outcomes(key, base | set(sel_tags[8:13]))
    d = search.promote("accuracy", s, inc, s, ch, 0.05, sel, cal)
    assert d["promoted"] and d["mcnemar"]["b_only"] == 5 and d["mcnemar"]["a_only"] == 0
    assert abs(d["mcnemar"]["p_b_better"] - 1 / 32) < 1e-9
    # The same gain at the alpha left after spending 0.05 over five rounds is not enough.
    late = search.promote("accuracy", s, inc, s, ch, 0.01, sel, cal)
    assert not late["promoted"] and late["alpha"] == 0.01
    # Wins that sit only in the calibration half never count toward promotion.
    ch_cal = _outcomes(key, base | set(cal_tags[:10]))
    d = search.promote("accuracy", s, inc, s, ch_cal, 0.05, sel, cal)
    assert not d["promoted"] and d["mcnemar"]["discordant"] == 0


def test_alpha_is_spent_over_the_planned_rounds(monkeypatch):
    monkeypatch.delenv("PROMOTION_ALPHA", raising=False)
    assert search.promotion_alpha(5) == 0.01
    assert search.promotion_alpha(0) == 0.05


def test_placebo_playbook_matches_size_and_passes_the_lesson_filter():
    pb = experiments.placebo_playbook(15)
    assert len(pb.lessons) == 15 and len({x.id for x in pb.lessons}) == 15
    assert pb.version == experiments.placebo_playbook(15).version
    assert all(agents.leakage_reason(x.text) is None for x in pb.lessons)


@needs_data
def test_keep_vendor_fallback_replaces_no_answer_and_abstentions():
    from forkward.data import load_hunks

    key = score.load_key("validation")
    hunks = [h for h in load_hunks("validation") if h.tag in key]
    outs = {}
    for h in hunks:
        d = gate.Decision(None, "none", 0.0, False, None, None, 0, 1, False, [])
        outs[h.tag] = search.Outcome(h.tag, [], [], None, d, 1)
    s = search.Strategy("s-x", ["p"], "pb-0", {}, False)
    f = experiments.fallback("validation", outs, s, gate.ABSTAIN, hunks)
    assert f["raw"] == 0.0
    assert f["fallback_no_answer"] == f["fallback_abstain"] == 25 / 45


@needs_data
def test_compute_matched_gives_both_arms_the_same_calls(monkeypatch, tmp_path):
    _fake_llm(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    res = asyncio.run(experiments.compute_matched(0, run_id="cm"))
    team, single = res["arms"]["team"], res["arms"]["single"]
    assert res["samples"] == 4 and single["strategy"]["rule"]["samples"] == 4
    assert team["calls"] == single["calls"] == 4 * (70 + 45)
    assert set(team["wall_clock_s"]) == {"train", "validation"}
    c = res["comparisons"]["validation: team vs single"]
    assert "p_holm" in c["mcnemar"] and "merge" in c["cluster_bootstrap"]
    attempts = store.read_jsonl(tmp_path / "cm", "attempts")
    assert attempts and not any(a.get("split") == "test" for a in attempts)
    assert "on abstain" in experiments.render(res)


@needs_data
def test_no_reflector_control_runs_three_arms_at_equal_per_hunk_budget(monkeypatch, tmp_path):
    _fake_llm(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    res = asyncio.run(experiments.no_reflector(0, run_id="nr"))
    arms = res["arms"]
    assert set(arms) == {"reflect", "placebo", "resample"}
    per_hunk = {k: a["calls_per_hunk"] for k, a in arms.items()}
    assert per_hunk["placebo"] == per_hunk["resample"] == 4.0
    assert arms["reflect"]["extra_calls"] == {"reflector": 1, "curator": 1}
    assert res["lessons"] >= 1
    assert "validation: reflect vs placebo" in res["comparisons"]
