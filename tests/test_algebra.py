"""Regression tests for the delta operators, one per bug found in the research review (1.9)."""

from forkward import algebra, gate, legacy_algebra
from forkward.data import Hunk


def _diff(*body: str) -> str:
    return "@@ -1,1 +1,1 @@\n" + "\n".join(body)


def _hunk(vendor, upstream, upstream_diff="", vendor_diff="", prompt=""):
    return Hunk(
        "t", "train", "f.c", prompt, tuple(vendor), tuple(upstream), upstream_diff, vendor_diff
    )


def test_duplicate_guard_only_checks_the_anchored_location():
    target = ["int f(void)", "{", "\treturn 0;", "}", "int g(void)", "{", "\tdo_x();", "}"]
    diff = _diff(" int g(void)", " {", " \tdo_x();", "+\treturn 0;", " }")
    changes = algebra.parse_changes(diff)
    # The old guard saw `return 0;` in f and skipped the change.
    assert legacy_algebra.apply_changes(target, legacy_algebra.parse_changes(diff)) is None
    assert algebra.apply_changes(target, changes) == (
        "int f(void)", "{", "\treturn 0;", "}", "int g(void)", "{", "\tdo_x();", "\treturn 0;", "}",
    )  # fmt: skip
    # Already present at its own anchor counts as applied, so nothing is left to do.
    assert algebra.apply_changes(algebra.apply_changes(target, changes), changes) is None


def test_replacement_uses_its_context_not_the_first_match():
    target = ["\tret = a();", "\tif (ret)", "\t\treturn ret;", "\tret = b();", "\tif (ret)", "\t\treturn ret;"]  # fmt: skip
    diff = _diff(" \tret = b();", "-\tif (ret)", "+\tif (ret < 0)", " \t\treturn ret;")
    want = ("\tret = a();", "\tif (ret)", "\t\treturn ret;", "\tret = b();", "\tif (ret < 0)", "\t\treturn ret;")  # fmt: skip
    assert algebra.apply_changes(target, algebra.parse_changes(diff)) == want
    old = legacy_algebra.apply_changes(target, legacy_algebra.parse_changes(diff))
    assert old is not None and old[1] == "\tif (ret < 0)"  # the old code rewrote the first block


def test_insertion_anchors_on_all_context_and_needs_a_unique_match():
    target = ["static int a(void)", "{", "\tx();", "}", "static int b(void)", "{", "\ty();", "}"]
    diff = _diff(" static int b(void)", " {", "+\tinit();", " \ty();")
    out = algebra.apply_changes(target, algebra.parse_changes(diff))
    assert out is not None and out.index("\tinit();") == 6
    old = legacy_algebra.apply_changes(target, legacy_algebra.parse_changes(diff))
    assert old is not None and old.index("\tinit();") == 2  # the old code used the first `{`
    # With only `{` as context the location is ambiguous, so the operator gives up.
    assert algebra.apply_changes(target, algebra.parse_changes(_diff(" {", "+\tinit();"))) is None


def test_partial_application_returns_none():
    target = ["a;", "b;", "c;", "x;", "y;"]
    diff = _diff(" a;", "-b;", "+B;", " c;", "@@ -9,3 +9,3 @@", " q;", "-r;", "+R;", " s;")
    assert legacy_algebra.apply_changes(target, legacy_algebra.parse_changes(diff)) == (
        "a;", "B;", "c;", "x;", "y;",
    )  # fmt: skip
    assert algebra.apply_changes(target, algebra.parse_changes(diff)) is None


def test_delta_ignores_changes_outside_the_conflict_and_uses_the_excerpt_context():
    vendor, upstream = ["\tv = 1;", "\tb();"], ["\tv = 2;", "\tB();"]
    diff = _diff(
        " \tint v;",
        "-\tv = 1;",
        "-\tb();",
        "+\tv = 2;",
        "+\tB();",
        " \treturn v;",
        "@@ -40,2 +40,2 @@",
        " far();",
        "-old();",
        "+new();",
    )
    ctx = (("\tint v;",), ("\treturn v;",))
    changes = algebra.parse_changes(diff)
    assert len(algebra.relevant(upstream, changes, ctx)) == 1
    assert algebra.apply_delta(upstream, vendor, changes, ctx) == ("\tv = 2;", "\tB();")
    # A vendor side whose surrounding context differs does not match under strict anchoring.
    assert algebra.apply_delta(upstream, vendor, changes, (("\tlong v;",), ())) is None


def test_fuzzy_anchoring_uses_one_side_when_the_other_was_edited():
    vendor = ["\tstart();", "", "\tvendor_config();"]
    upstream = ["\tconfig();"]
    diff = _diff(" \tsetup();", "-\tstart();", "-", " \tconfig();")
    ctx = (("\tsetup();",), ("\treturn 0;",))
    changes = algebra.parse_changes(diff)
    assert algebra.apply_delta(upstream, vendor, changes, ctx) is None
    assert algebra.apply_delta(upstream, vendor, changes, ctx, fuzzy=True) == (
        "\tvendor_config();",
    )


def test_truncated_diff_drops_the_cut_run_and_takes_away_the_vote():
    diff = _diff(
        " a;",
        "-b;",
        "+B;",
        " c;",
        "@@ -9,3 +9,3 @@",
        " q;",
        "-r;",
        "... (12 more diff lines omitted)",
    )
    changes = algebra.parse_changes(diff)
    assert [c.removed for c in changes] == [("b;",)]
    assert algebra.diff_truncated(diff)
    h = _hunk(["a;", "b;", "c;"], ["a;", "B;", "c;"], upstream_diff=diff)
    cands = {c.source: c for c in algebra.candidates(h)}
    delta = cands["op:upstream_delta_on_vendor"]
    assert delta.lines == ("a;", "B;", "c;") and not delta.vote
    assert cands["op:keep_vendor"].vote
    # The upstream side equals the delta output here, so take_upstream keeps the cluster's vote.
    cl = gate.cluster(h.vendor, h.upstream, [delta], [("p1", delta.lines, None)])
    d = gate.select(cl, 1, None, {"w_llm": 1.0})
    assert d.lines == delta.lines and not d.has_op and not d.accepted(0.0, False)
