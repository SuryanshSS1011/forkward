from forkward import algebra, token_algebra
from forkward.data import Hunk


def _hunk(vendor, upstream, base, extra_before="\tint ret;", extra_after="\treturn ret;"):
    def diff(side):
        body = [f" {extra_before}"] + [f"-{x}" for x in base] + [f"+{x}" for x in side]
        return "@@ -1,3 +1,3 @@\n" + "\n".join(body + [f" {extra_after}"])

    return Hunk(
        "t", "train", "f.c", "", tuple(vendor), tuple(upstream), diff(upstream), diff(vendor)
    )


def test_tokenize_round_trips_whitespace_and_punctuation():
    lines = ("\tret = foo(dev,  10);", "", "  /* x */")
    assert token_algebra.detokenize(token_algebra.tokenize(lines)) == lines
    assert token_algebra.detokenize([]) == ()


def test_base_is_rebuilt_from_both_diffs():
    h = _hunk(["\tret = foo(pdev, 10);"], ["\tret = foo(dev, 20);"], ["\tret = foo(dev, 10);"])
    base = token_algebra.reconstruct_base(h)
    assert base == token_algebra.Base(("\tret = foo(dev, 10);",), True)


def test_non_overlapping_token_edits_interleave():
    h = _hunk(["\tret = foo(pdev, 10);"], ["\tret = foo(dev, 20);"], ["\tret = foo(dev, 10);"])
    cands = {c.source: c for c in token_algebra.candidates(h)}
    assert set(cands) == {"op:tok_interleave"}
    assert cands["op:tok_interleave"].lines == ("\tret = foo(pdev, 20);",)
    assert cands["op:tok_interleave"].vote
    # The line-level algebra cannot produce this resolution.
    assert all(c.lines != ("\tret = foo(pdev, 20);",) for c in algebra.candidates(h))
    assert ("\tret = foo(pdev, 20);",) in [c.lines for c in algebra.candidates(h, token=True)]


def test_overlapping_edits_use_one_primitive_pattern_per_hunk():
    out = token_algebra.merge(["x = f(a, 1);"], ["x = f(b, 2);"], ["x = f(a, 3);"])
    assert out["vendor"] == ("x = f(b, 2);",)
    assert out["upstream"] == ("x = f(b, 3);",)  # the vendor's rename of a still applies
    assert out["base"] == ("x = f(b, 1);",)
    assert out["vendor_then_upstream"] == ("x = f(b, 23);",)
    assert out["upstream_then_vendor"] == ("x = f(b, 32);",)


def test_identical_edits_on_both_sides_are_not_a_conflict():
    out = token_algebra.merge(["a = 1;"], ["a = 2;"], ["a = 2;"])
    assert out == {"interleave": ("a = 2;",)}


def test_unknown_or_disagreeing_base_gives_no_candidates_and_one_sided_base_cannot_vote():
    h = Hunk("t", "train", "f.c", "", ("a;",), ("b;",), "", "")
    assert token_algebra.candidates(h) == []
    one_sided = _hunk(["\tv = 1;"], ["\tv = 2;"], ["\tv = 0;"])
    one_sided = Hunk(*[getattr(one_sided, f) for f in ("tag", "split", "file", "prompt")],
                     one_sided.vendor, one_sided.upstream, one_sided.upstream_diff, "")  # fmt: skip
    cands = token_algebra.candidates(one_sided)
    assert cands and not any(c.vote for c in cands)
