"""Group-conditional and leave-one-vendor-out calibration on synthetic data."""

from forkward import gate


def _pool(prefix: str, n: int, wrong: int, score: float = 0.9):
    scores = {f"{prefix}{i}": score for i in range(n)}
    correct = {f"{prefix}{i}": i >= wrong for i in range(n)}
    return scores, correct


def _merge(*pools):
    scores, correct = {}, {}
    for s, c in pools:
        scores.update(s)
        correct.update(c)
    return scores, correct


def test_calibrate_scores_matches_decision_calibration():
    scores, correct = _pool("h", 80, 0)
    dec = {
        t: gate.Decision(("x",), "op:keep_vendor", s, True, None, None, 1, 1, False, [])
        for t, s in scores.items()
    }
    a = gate.calibrate(dec, correct, need_classifier=False, target=0.05)
    b = gate.calibrate_scores(scores, correct, target=0.05)
    assert a["target_met"] and b["target_met"]
    assert (a["threshold"], a["accepted"], a["bound"]) == (
        b["threshold"],
        b["accepted"],
        b["bound"],
    )


def test_group_conditional_calibration_abstains_on_the_risky_group_only():
    scores, correct = _merge(_pool("e", 200, 0), _pool("r", 40, 12))
    pooled = gate.calibrate_scores(scores, correct, target=0.2)
    # Pooled calibration certifies the mix and so auto-applies the risky group at 30% error.
    assert pooled["target_met"]
    assert gate.usable_threshold(pooled) <= scores["r0"]
    groups = {t: t[0] for t in scores}
    cal = gate.calibrate_groups(scores, correct, groups, target=0.2)
    assert cal["target_met_groups"] == ["e"]
    assert gate.accept_grouped(scores["e5"], "e", cal)
    assert not gate.accept_grouped(scores["r20"], "r", cal)
    assert not gate.accept_grouped(0.99, "unseen", cal)
    assert cal["worst_bound"] < 0.05
    assert cal["level"] == 1 - 0.05 / 2


def test_leave_one_vendor_out_reports_the_worst_held_out_vendor():
    scores, correct = _merge(_pool("a", 100, 0), _pool("b", 100, 0), _pool("c", 50, 20))
    vendor = {t: t[0] for t in scores}
    res = gate.leave_one_group_out(scores, correct, vendor, target=0.1)
    assert res["folds"]["c"]["accepted"] == 50 and res["folds"]["c"]["violates_target"]
    assert res["worst_vendor"] == "c" and res["violations"] == 1
    # Calibrating on b and c misses the target, so the gate abstains on vendor a.
    assert res["folds"]["a"]["accepted"] == 0
    # Conditioning on a group that only vendor c has leaves c with no certified threshold.
    groups = {t: ("risky" if t[0] == "c" else "plain") for t in scores}
    cond = gate.leave_one_group_out(scores, correct, vendor, target=0.1, groups=groups)
    assert cond["folds"]["c"]["accepted"] == 0 and cond["violations"] == 0


def test_fixed_sequence_certifies_where_bonferroni_cannot_and_stops_at_the_first_miss():
    # 120 hunks at confidence 0.95 with no errors, then 60 at 0.60 with 30% errors.
    scores = {f"a{i}": 0.95 for i in range(120)} | {f"b{i}": 0.60 for i in range(60)}
    correct = {f"a{i}": True for i in range(120)} | {f"b{i}": i % 10 >= 3 for i in range(60)}
    fs = gate.calibrate_scores(scores, correct, target=0.03)
    assert fs["target_met"] and fs["method"] == "fixed_sequence"
    assert fs["threshold"] == 0.61 and fs["accepted"] == 120 and fs["level"] == 0.95
    # Bonferroni over the data-derived grid tests at 1 - 0.05 / 2 and misses 3% on 120 hunks.
    bf = gate.calibrate_scores(scores, correct, target=0.03, method="bonferroni")
    assert not bf["target_met"]
    # Testing stopped at 0.60, the first threshold whose bound missed the target.
    assert fs["grid"][-1]["threshold"] == 0.60 and fs["grid"][-1]["bound"] > 0.03


def test_ltt_method_is_read_when_calibrating_not_at_import(monkeypatch):
    from forkward import gate

    monkeypatch.delenv("FORKWARD_LTT_METHOD", raising=False)
    assert gate.ltt_method() == "fixed_sequence"
    monkeypatch.setenv("FORKWARD_LTT_METHOD", "bonferroni")
    assert gate.ltt_method() == "bonferroni"
