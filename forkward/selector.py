"""H0: a deterministic, non-LLM operator selector.

Each hunk gets boolean features computed from the visible block only: whether the sides are
equal after normalization, whether a side is empty or equals the rebuilt base, which side's diff
touched the conflict and how (insertions only, removals only), diff truncation, file type, line
overlap between sides, and which operators produced a candidate or agreed with each other.

The model is an ordered rule list learned by sequential covering on train. Each rule is one or
two feature literals plus an operator, chosen greedily by Laplace-smoothed precision among the
hunks no earlier rule covered. A hunk takes the first rule that fires and whose operator produced
a candidate, otherwise keep vendor. The rule's train precision is its confidence. Rule-list size
and minimum support are tuned on one seeded half of validation, the abstention threshold is
calibrated by Learn-then-Test on the other half, and the test split is never read.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from forkward import algebra, gate, score, stats, token_algebra
from forkward.data import Hunk, context, halves, load_hunks
from forkward.score import normalize

DEFAULT_OP = "keep_vendor"
GRID = {"min_support": (2, 3, 5), "max_rules": (1, 2, 4, 6, 8), "pairs": (False, True)}


def _subset(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return bool(a) and set(a) <= set(b)


def _diff_shape(side: tuple[str, ...], diff: str, ctx) -> tuple[bool, bool]:
    rel = algebra.relevant(side, algebra.parse_changes(diff), ctx)
    return bool(rel) and all(not c.removed for c in rel), bool(rel) and all(
        not c.added for c in rel
    )


def outputs(h: Hunk) -> dict[str, tuple[str, ...]]:
    """Normalized candidate lines per operator name, line-level, fuzzy and token-level."""
    cands = algebra.candidates(h, token=True, fuzzy=True)
    return {c.source.removeprefix("op:"): normalize(c.lines) for c in cands}


def features(h: Hunk, outs: dict[str, tuple[str, ...]] | None = None) -> dict[str, bool]:
    outs = outs if outs is not None else outputs(h)
    v, u = normalize(h.vendor), normalize(h.upstream)
    base = token_algebra.reconstruct_base(h)
    b = normalize(base.lines) if base else None
    ext = PurePosixPath(h.file).suffix
    ctx = context(h)
    up_adds, up_removes = _diff_shape(h.upstream, h.upstream_diff, ctx)
    ven_adds, ven_removes = _diff_shape(h.vendor, h.vendor_diff, ctx)
    jac = len(set(v) & set(u)) / max(1, len(set(v) | set(u)))
    ud = outs.get("upstream_delta_on_vendor") or outs.get("upstream_delta_on_vendor_fuzzy")
    vd = outs.get("vendor_delta_on_upstream") or outs.get("vendor_delta_on_upstream_fuzzy")
    return {
        "sides_equal": v == u,
        "vendor_empty": not v,
        "upstream_empty": not u,
        "vendor_in_upstream": _subset(v, u),
        "upstream_in_vendor": _subset(u, v),
        "vendor_longer": len(v) > len(u),
        "small": len(v) + len(u) <= 4,
        "overlap_high": jac >= 0.5,
        "base_known": b is not None,
        "base_confirmed": bool(base and base.confirmed),
        "vendor_is_base": b is not None and v == b,
        "upstream_is_base": b is not None and u == b,
        "upstream_adds_only": up_adds,
        "upstream_removes_only": up_removes,
        "vendor_adds_only": ven_adds,
        "vendor_removes_only": ven_removes,
        "upstream_diff_truncated": algebra.diff_truncated(h.upstream_diff),
        "vendor_diff_truncated": algebra.diff_truncated(h.vendor_diff),
        "upstream_delta": ud is not None,
        "vendor_delta": vd is not None,
        "deltas_agree": ud is not None and ud == vd,
        "token_interleave": "tok_interleave" in outs,
        "token_conflict": "tok_vendor" in outs,
        "ext_c": ext in (".c", ".h"),
        "ext_devicetree": ext in (".dts", ".dtsi"),
        "ext_other": ext not in (".c", ".h", ".dts", ".dtsi"),
    }


@dataclass(frozen=True)
class Row:
    tag: str
    label: str
    feats: dict[str, bool]
    outs: dict[str, tuple[str, ...]]
    correct_ops: frozenset[str]


def rows_for(split: str) -> list[Row]:
    if split == "test" or split.startswith("holdout"):
        raise ValueError("the selector never reads a test or holdout pool")
    key = score.load_key(split)
    out = []
    for h in load_hunks(split):
        if h.tag not in key:
            continue
        outs = outputs(h)
        ok = frozenset(op for op, lines in outs.items() if lines == key[h.tag].gt)
        out.append(Row(h.tag, key[h.tag].label, features(h, outs), outs, ok))
    return out


@dataclass(frozen=True)
class Rule:
    cond: tuple[str, ...]
    op: str
    n: int
    k: int

    @property
    def confidence(self) -> float:
        return (self.k + 1) / (self.n + 2)

    def fires(self, feats: dict[str, bool], outs: dict) -> bool:
        return self.op in outs and all(_lit(feats, c) for c in self.cond)


def _lit(feats: dict[str, bool], lit: str) -> bool:
    return not feats.get(lit[1:], False) if lit.startswith("!") else feats.get(lit, False)


def _mask(rows: list[Row], pred) -> int:
    return sum(1 << i for i, r in enumerate(rows) if pred(r))


@dataclass
class Selector:
    rules: list[Rule]
    default: Rule
    params: dict

    def predict(self, feats: dict[str, bool], outs: dict) -> tuple[str, tuple[str, ...], float]:
        for r in self.rules:
            if r.fires(feats, outs):
                return r.op, outs[r.op], r.confidence
        return self.default.op, outs.get(self.default.op, ()), self.default.confidence

    def doc(self) -> dict:
        return {
            "rules": [asdict(r) | {"confidence": r.confidence} for r in self.rules],
            "default": asdict(self.default) | {"confidence": self.default.confidence},
            "params": self.params,
        }


def fit(rows: list[Row], min_support: int = 3, max_rules: int = 6, pairs: bool = False) -> Selector:
    """Sequential covering: repeatedly add the rule with the best Laplace precision among the
    hunks not yet covered, requiring at least `min_support` covered hunks."""
    names = sorted(rows[0].feats) if rows else []
    lits = {f: _mask(rows, lambda r, f=f: r.feats[f]) for f in names}
    lits |= {f"!{f}": _mask(rows, lambda r, f=f: not r.feats[f]) for f in names}
    conds = [(a,) for a in lits]
    if pairs:
        conds += [
            (a, b)
            for a, b in itertools.combinations(sorted(lits), 2)
            if a.lstrip("!") != b.lstrip("!")
        ]
    ops = sorted({op for r in rows for op in r.outs})
    made = {op: _mask(rows, lambda r, op=op: op in r.outs) for op in ops}
    right = {op: _mask(rows, lambda r, op=op: op in r.correct_ops) for op in ops}
    remaining = (1 << len(rows)) - 1
    rules: list[Rule] = []
    while len(rules) < max_rules:
        best = None
        for cond in conds:
            m = remaining
            for lit in cond:
                m &= lits[lit]
            for op in ops:
                cov = m & made[op]
                n = cov.bit_count()
                if n < min_support:
                    continue
                rule = Rule(cond, op, n, (cov & right[op]).bit_count())
                key = (rule.confidence, n, -len(cond))
                if best is None or key > best[0]:
                    best = (key, rule, cov)
        if best is None:
            break
        rules.append(best[1])
        remaining &= ~best[2]
    n = remaining.bit_count()
    k = (remaining & right.get(DEFAULT_OP, 0)).bit_count()
    params = {"min_support": min_support, "max_rules": max_rules, "pairs": pairs}
    return Selector(rules, Rule((), DEFAULT_OP, n, k), params)


def predictions(sel: Selector, rows: list[Row]) -> dict[str, dict]:
    out = {}
    for r in rows:
        op, lines, conf = sel.predict(r.feats, r.outs)
        out[r.tag] = {"op": op, "lines": lines, "confidence": conf, "correct": op in r.correct_ops}
    return out


def per_class(rows: list[Row], preds: dict[str, dict]) -> dict:
    out = {}
    for c in score.CLASSES:
        sub = [r for r in rows if r.label == c]
        out[c] = {"n": len(sub), "correct": sum(preds[r.tag]["correct"] for r in sub)}
    n = len(rows)
    k = sum(p["correct"] for p in preds.values())
    return {
        "n": n,
        "correct": k,
        "accuracy": k / n if n else 0.0,
        "wilson": stats.wilson(k, n),
        "per_class": out,
    }


def tune(train: list[Row], select: list[Row]) -> Selector:
    """Pick the grid point with the best accuracy on the selection half, simplest on ties."""
    best = None
    for ms, mr, pr in itertools.product(*GRID.values()):
        sel = fit(train, ms, mr, pr)
        acc = sum(p["correct"] for p in predictions(sel, select).values())
        key = (acc, -len(sel.rules), not pr, ms)
        if best is None or key > best[0]:
            best = (key, sel)
    return best[1]


def _abstention(sel: Selector, calib: list[Row], select: list[Row], target: float, conf: float):
    """Calibrate the confidence threshold on the calibration half, pooled and per predicted
    class, then report what that threshold accepts on the selection half as a check."""
    pc, ps = predictions(sel, calib), predictions(sel, select)
    scores = {t: p["confidence"] for t, p in pc.items()}
    correct = {t: p["correct"] for t, p in pc.items()}
    pooled = gate.calibrate_scores(scores, correct, target, conf)
    thr = gate.usable_threshold(pooled)
    acc_s = [t for t, p in ps.items() if p["confidence"] >= thr]
    errs_s = sum(not ps[t]["correct"] for t in acc_s)
    cls = {t: _predicted_class(p["op"]) for t, p in (pc | ps).items()}
    grouped = gate.calibrate_groups(scores, correct, cls, target, conf)
    return {
        "target": target,
        "confidence": conf,
        "min_error_free_accepted_needed": gate.min_accepted_for(target, conf),
        "pooled": _summary(pooled),
        "pooled_on_calibration_half": _coverage(pc, thr),
        "pooled_on_selection_half": {
            "accepted": len(acc_s),
            "errors": errs_s,
            "coverage": len(acc_s) / max(1, len(ps)),
            "empirical_bound": score.clopper_pearson_upper(errs_s, len(acc_s), conf),
        },
        "by_predicted_class": {g: _summary(c) for g, c in grouped["groups"].items()},
        "by_predicted_class_worst_bound": grouped["worst_bound"],
    }


def _predicted_class(op: str) -> str:
    return op if op in ("keep_vendor", "take_upstream") else "merge"


def _summary(c: dict) -> dict:
    keep = ("threshold", "target_met", "bound", "accepted", "errors", "level")
    out = {k: c[k] for k in keep if k in c}
    if "best_effort" in c:
        out["best_effort"] = c["best_effort"]
    return out


def _coverage(preds: dict[str, dict], thr: float) -> dict:
    acc = [t for t, p in preds.items() if p["confidence"] >= thr]
    errs = sum(not preds[t]["correct"] for t in acc)
    return {"accepted": len(acc), "errors": errs, "coverage": len(acc) / max(1, len(preds))}


def team_outcomes(run_id: str) -> dict[str, float] | None:
    """Per-hunk validation correctness of a search run's final strategy, replayed from its logs
    with no model calls. None when the run did not log every proposal."""
    from forkward import analysis

    final = json.loads((analysis.RUNS / run_id / "final.json").read_text())
    fs = final["strategy"]
    return analysis.replay(
        analysis.RUNS / run_id,
        "validation",
        fs["personas"],
        fs["playbook"],
        fs["rule"],
        fs["use_classifier"],
    )


def run(
    seed: int = 0, target: float = 0.2, confidence: float = 0.95, team_run: str | None = None
) -> dict:
    train, val = rows_for("train"), rows_for("validation")
    sel_tags, cal_tags = halves("validation", seed)
    select = [r for r in val if r.tag in sel_tags]
    calib = [r for r in val if r.tag in cal_tags]
    sel = tune(train, select)
    pv = predictions(sel, val)
    kv = {r.tag: float(DEFAULT_OP in r.correct_ops) for r in val}
    res = {
        "seed": seed,
        "halves": {"selection": len(select), "calibration": len(calib)},
        "selector": sel.doc(),
        "train": per_class(train, predictions(sel, train)),
        "validation": per_class(val, pv),
        "validation_selection_half": per_class(select, predictions(sel, select)),
        "validation_calibration_half": per_class(calib, predictions(sel, calib)),
        "validation_vs_keep_vendor": stats.compare(
            kv, {t: float(p["correct"]) for t, p in pv.items()}, stats.clusters_for("validation")
        ),
        "abstention": _abstention(sel, calib, select, target, confidence),
        "leave_one_vendor_out": _lovo(sel, train, val, target, confidence),
    }
    if team_run and (team := team_outcomes(team_run)):
        mine = {t: float(p["correct"]) for t, p in pv.items()}
        res["selector_vs_team"] = {
            "team_run": team_run,
            **stats.compare(team, mine, stats.clusters_for("validation")),
        }
    return res


def _lovo(sel: Selector, train: list[Row], val: list[Row], target: float, conf: float) -> dict:
    """Leave-one-vendor-out over train (NXP) and validation (ST). The selector was fit on NXP, so
    the NXP scores are in-sample and every fold is optimistic; it shows the procedure, not a
    guarantee. A fresh vendor is needed for an honest fold."""
    preds = predictions(sel, train) | predictions(sel, val)
    vendor = {r.tag: "nxp" for r in train} | {r.tag: "st" for r in val}
    scores = {t: p["confidence"] for t, p in preds.items()}
    correct = {t: p["correct"] for t, p in preds.items()}
    res = gate.leave_one_group_out(scores, correct, vendor, target, conf)
    res["note"] = "selector fit on nxp, so nxp scores are in-sample"
    return res


def render(res: dict) -> str:
    lines = [f"H0 operator selector (seed {res['seed']}), rules learned on train:"]
    for r in res["selector"]["rules"]:
        lines.append(f"  if {' and '.join(r['cond'])} -> {r['op']}  ({r['k']}/{r['n']} on train)")
    d = res["selector"]["default"]
    lines.append(f"  else -> {d['op']}  ({d['k']}/{d['n']} on train)")
    lines.append(f"  params {res['selector']['params']}")
    for name in ("train", "validation", "validation_selection_half", "validation_calibration_half"):
        m = res[name]
        pc = "  ".join(f"{c} {v['correct']}/{v['n']}" for c, v in m["per_class"].items())
        lo, hi = m["wilson"]
        lines.append(
            f"{name:<28} {m['correct']}/{m['n']} ({m['accuracy']:.1%}, Wilson [{lo:.1%}, {hi:.1%}])  {pc}"
        )
    lines.append(stats.render("validation vs keep vendor", res["validation_vs_keep_vendor"], 26))
    if "selector_vs_team" in res:
        lines.append(stats.render("validation vs team", res["selector_vs_team"], 26))
    a = res["abstention"]
    p = a["pooled"]
    lines.append(
        f"abstention target {a['target']:.0%} at {a['confidence']:.0%}: needs at least "
        f"{a['min_error_free_accepted_needed']} error-free accepted hunks"
    )
    lines.append(
        f"  pooled on calibration half: target met {p['target_met']}, threshold {p['threshold']:.3f}, "
        f"accepted {a['pooled_on_calibration_half']['accepted']}, "
        f"errors {a['pooled_on_calibration_half']['errors']}"
    )
    s = a["pooled_on_selection_half"]
    lines.append(
        f"  same threshold on selection half: accepted {s['accepted']}, errors {s['errors']}, "
        f"coverage {s['coverage']:.1%}, empirical bound {s['empirical_bound']:.1%}"
    )
    for g, c in a["by_predicted_class"].items():
        lines.append(f"  group {g}: target met {c['target_met']}, accepted {c['accepted']}")
    lv = res["leave_one_vendor_out"]
    lines.append(
        f"leave one vendor out ({lv['note']}): worst vendor {lv['worst_vendor']}, "
        f"worst bound {lv['worst_bound']}, folds over target {lv['violations']}"
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward selector")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--target", type=float, default=0.2)
    p.add_argument("--confidence", type=float, default=0.95)
    p.add_argument("--team-run", default=None, help="search run whose final strategy to compare")
    a = p.parse_args(argv)
    res = run(a.seed, a.target, a.confidence, a.team_run)
    print(render(res))
    out = Path("results") / "selector-h0.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=list))
    print(f"written {out}")
    return 0


V2_FOLDS = ("nxp", "st", "rk")
V2_TARGETS = (0.05, 0.10, 0.20)


def _apply(preds: dict[str, dict], accept) -> dict:
    acc = [t for t, p in preds.items() if accept(t, p)]
    errs = sum(not preds[t]["correct"] for t in acc)
    return {
        "accepted": len(acc),
        "errors": errs,
        "coverage": len(acc) / max(1, len(preds)),
        "escaped_rate": errs / len(acc) if acc else 0.0,
    }


def run_v2(confidence: float = 0.95, targets=V2_TARGETS, method: str = "bonferroni") -> dict:
    """E10: for each held-out vendor fold of the v2 dev pool, fit on half A of the other two
    folds with fit's default parameters, calibrate the abstention threshold on their half B
    (pooled and per predicted class), and apply both to every scorable hunk of the held-out
    fold. Never reads the holdout pool."""
    folds = {}
    for v in V2_FOLDS:
        others = [f for f in V2_FOLDS if f != v]
        train = [r for o in others for r in rows_for(f"dev:{o}:A")]
        calib = [r for o in others for r in rows_for(f"dev:{o}:B")]
        held = rows_for(f"dev:{v}")
        sel = fit(train)
        ph, pc = predictions(sel, held), predictions(sel, calib)
        kv = {r.tag: float(DEFAULT_OP in r.correct_ops) for r in held}
        mine = {t: float(p["correct"]) for t, p in ph.items()}
        scores = {t: p["confidence"] for t, p in pc.items()}
        correct = {t: p["correct"] for t, p in pc.items()}
        cls_c = {t: _predicted_class(p["op"]) for t, p in pc.items()}
        at = {}
        for target in targets:
            pooled = gate.calibrate_scores(scores, correct, target, confidence, method=method)
            thr = gate.usable_threshold(pooled)
            grouped = gate.calibrate_groups(
                scores, correct, cls_c, target, confidence, method=method
            )
            rows = {
                "pooled": _apply(ph, lambda t, p, thr=thr: p["confidence"] >= thr),
                "grouped": _apply(
                    ph,
                    lambda t, p, g=grouped: gate.accept_grouped(
                        p["confidence"], _predicted_class(p["op"]), g
                    ),
                ),
            }
            for r in rows.values():
                r["bound"] = score.clopper_pearson_upper(r["errors"], r["accepted"], confidence)
                r["violates"] = bool(r["accepted"]) and r["escaped_rate"] > target
            rows["pooled"]["calibration"] = _summary(pooled)
            rows["grouped"]["calibration"] = {g: _summary(c) for g, c in grouped["groups"].items()}
            at[str(target)] = rows
        folds[v] = {
            "train": {"fold_halves": [f"{o}:A" for o in others], "n": len(train)},
            "calibration": {"fold_halves": [f"{o}:B" for o in others], "n": len(calib)},
            "selector": sel.doc(),
            "held_out": per_class(held, ph),
            "keep_vendor": per_class(held, {r.tag: {"correct": bool(kv[r.tag])} for r in held}),
            "vs_keep_vendor": stats.compare(kv, mine, stats.clusters_for(f"dev:{v}")),
            "abstention": at,
        }
    stats.adjust({v: f["vs_keep_vendor"] for v, f in folds.items()})
    wins = [
        v
        for v, f in folds.items()
        if f["vs_keep_vendor"]["mcnemar"]["p_holm"] < 0.05
        and f["vs_keep_vendor"]["mcnemar"]["b_only"] > f["vs_keep_vendor"]["mcnemar"]["a_only"]
    ]
    viol = {
        kind: sum(
            f["abstention"][str(t)][kind]["violates"] for f in folds.values() for t in targets
        )
        for kind in ("pooled", "grouped")
    }
    return {
        "experiment": "E10",
        "calibration_method": method,
        "confidence": confidence,
        "targets": list(targets),
        "folds": folds,
        "h0": {
            "folds_beating_keep_vendor": wins,
            "verdict": "supported" if len(wins) >= 2 else "not supported",
        },
        "h3": {
            "violations": viol,
            "accepted_anywhere": any(
                f["abstention"][str(t)][k]["accepted"]
                for f in folds.values()
                for t in targets
                for k in ("pooled", "grouped")
            ),
            # The registered rule counts no reduction as falsified. When nothing was accepted
            # anywhere, that verdict is vacuous, and the result says so.
            "verdict": "falsified" if viol["grouped"] >= viol["pooled"] else "not falsified",
        },
    }


def render_v2(res: dict) -> str:
    lines = ["E10: H0 selector, leave one vendor out on the v2 dev pool"]
    for v, f in res["folds"].items():
        h, k, c = f["held_out"], f["keep_vendor"], f["vs_keep_vendor"]
        m = c["mcnemar"]
        pc = "  ".join(f"{cl} {x['correct']}/{x['n']}" for cl, x in h["per_class"].items())
        lines.append(
            f"held out {v}: selector {h['correct']}/{h['n']} ({h['accuracy']:.1%}) vs keep vendor "
            f"{k['correct']}/{k['n']} ({k['accuracy']:.1%}); discordant {m['b_only']} vs "
            f"{m['a_only']}, p {m['p_two_sided']:.3g}, Holm {m['p_holm']:.3g}"
        )
        lines.append(f"  per class {pc}")
        rules = "; ".join(
            f"{' and '.join(r['cond'])} -> {r['op']} ({r['k']}/{r['n']})"
            for r in f["selector"]["rules"]
        )
        lines.append(f"  rules: {rules or 'none'}")
        for t, rows in f["abstention"].items():
            for kind in ("pooled", "grouped"):
                r = rows[kind]
                lines.append(
                    f"  target {float(t):.0%} {kind:<7} accepted {r['accepted']:>3} "
                    f"({r['coverage']:.1%}), escaped {r['errors']} "
                    f"({r['escaped_rate']:.1%}), bound {r['bound']:.1%}"
                    + ("  VIOLATES" if r["violates"] else "")
                )
    wins = res["h0"]["folds_beating_keep_vendor"]
    lines.append(f"H0: {res['h0']['verdict']} (folds beating keep vendor: {wins})")
    vac = "" if res["h3"]["accepted_anywhere"] else ", vacuous: nothing was certified anywhere"
    lines.append(f"H3: {res['h3']['verdict']} (violations {res['h3']['violations']}{vac})")
    return "\n".join(lines)


def main_v2(argv: list[str]) -> int:
    # E10 was registered with Bonferroni Learn-then-Test; --method fixed_sequence is exploratory.
    method = argv[argv.index("--method") + 1] if "--method" in argv else "bonferroni"
    res = run_v2(method=method)
    print(render_v2(res))
    name = "selector-v2-e10.json" if method == "bonferroni" else f"selector-v2-e10-{method}.json"
    out = Path("results") / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=list))
    print(f"written {out}")
    return 0
