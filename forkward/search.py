"""Round controller: baseline, population, reflection, curation, promotion, freeze, single test."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from forkward import agents, algebra, gate, llm, retrieval, score, stats
from forkward.agents import EMPTY_PLAYBOOK, Playbook
from forkward.data import Hunk, conflict_key, halves, load_hunks
from forkward.store import RUNS, Store, read_jsonl

TRAIN_THRESHOLD = 0.5
BASELINE_RULE = {"w_llm": 1.0, "w_cls": 0.0, "op_prior": {}}
RULE_FLAGS = ("retrieval", "samples", "judge", "token_ops", "fuzzy_ops")
DEFAULT_RULE = {"w_llm": 1.0, "w_cls": 0.5, "op_prior": {"op:keep_vendor": 0.5}}
# Three proposers per team: enough for a clear vote, and each extra persona costs a call per hunk.
SEED_TEAMS = [
    ["vendor_first", "minimal_diff", "operator_picker"],
    ["vendor_first", "upstream_fix_first", "refactor_aware"],
    ["minimal_diff", "operator_picker", "refactor_aware"],
]
FROZEN = RUNS / "frozen.json"
PACKAGED = Path(__file__).resolve().parent / "strategy.json"
TEST_DONE = RUNS / "test_done.json"


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


@dataclass
class Strategy:
    id: str
    personas: list[str]
    playbook: str
    rule: dict
    use_classifier: bool
    parent: str | None = None
    round: int = 0
    train: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)

    def doc(self) -> dict:
        return asdict(self)


@dataclass
class Outcome:
    tag: str
    ops: list[algebra.Candidate]
    proposals: list[agents.Proposal]
    classifier: str | None
    decision: gate.Decision
    n_proposers: int = 0


def new_id(prefix: str, *parts) -> str:
    """A content hash of the parts that define the object, so reruns reproduce the same ids."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def stable_seed(*parts) -> int:
    """A sampling seed derived from the call's identity, so reruns send the same seed."""
    raw = json.dumps(parts, default=str).encode()
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def operator_candidates(h: Hunk, rule: dict) -> list[algebra.Candidate]:
    """Line-level operators, plus the fuzzy and token-level sets when the rule turns them on."""
    return algebra.candidates(
        h, token=bool(rule.get("token_ops")), fuzzy=bool(rule.get("fuzzy_ops"))
    )


class Runner:
    """Runs strategies over hunks. Shares proposals across strategies with the same playbook."""

    def __init__(
        self,
        store: Store,
        playbooks: dict[str, Playbook],
        cache: dict | None = None,
        seed: int | None = None,
        confirmed: set[str] | frozenset[str] = frozenset(),
    ):
        self.store = store
        # SAMPLING_SEED salts every per-call seed; a control that needs fresh samples uses another.
        self.seed = env_int("SAMPLING_SEED", 0) if seed is None else seed
        self.calls: dict[str, int] = {}
        self.playbooks = playbooks
        self.rerere = cache or {}
        # Keys of cached resolutions a person merged unchanged. Only those count as an operator
        # vote in the gate; a resolution the system produced itself is a candidate, not evidence.
        self.confirmed = frozenset(confirmed)
        self._props: dict[tuple, asyncio.Task] = {}
        self._cls: dict[tuple, asyncio.Task] = {}
        self._judges: dict[tuple, asyncio.Task] = {}
        self.tokens: dict[str, int] = {}

    def _count(self, ctx: dict, c: llm.Completion) -> None:
        sid = ctx.get("strategy", "")
        self.tokens[sid] = self.tokens.get(sid, 0) + c.prompt_tokens + c.completion_tokens
        self.calls[sid] = self.calls.get(sid, 0) + 1

    async def _proposal(
        self,
        h: Hunk,
        persona: str,
        pb: Playbook,
        ops,
        ctx: dict,
        hint: str = "",
        sample: int = 0,
    ):
        agent = f"resolver:{persona}"
        self.store.event(agent, "start", hunk=h.tag, task="propose", **ctx)
        temperature = 0.2 if sample == 0 else 0.8
        seed = stable_seed(self.seed, h.split, h.tag, persona, pb.version, sample, bool(hint))
        p = await agents.propose(h, persona, pb, ops, temperature=temperature, hint=hint, seed=seed)
        c = p.completion
        self._count(ctx, c)
        label = (
            score.class_of_candidate(p.lines, h.vendor, h.upstream) if p.lines is not None else None
        )
        self.store.event(
            agent,
            "error" if not c.ok else "done",
            hunk=h.tag,
            task="propose",
            latency_s=round(c.latency_s, 2),
            tokens=c.prompt_tokens + c.completion_tokens,
            decision=label or "no answer",
            error=c.error,
            mean_logprob=c.mean_logprob,
            **ctx,
        )
        self.store.add(
            "attempts",
            {
                "kind": "propose",
                "tag": h.tag,
                "persona": persona,
                "sample": sample,
                "seed": seed,
                "retrieval": bool(hint),
                "playbook": pb.version,
                "prompt": p.prompt,
                "response": c.text,
                "lines": p.lines,
                "lessons_used": p.lessons_used,
                "reason": p.reason,
                "model": c.model,
                "provider": c.provider,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "latency_s": c.latency_s,
                "mean_logprob": c.mean_logprob,
                "error": c.error,
                **ctx,
            },
        )
        return p

    async def _classify(self, h: Hunk, pb: Playbook, ctx: dict):
        self.store.event("classifier", "start", hunk=h.tag, task="classify", **ctx)
        label, c, prompt = await agents.classify(h, pb)
        self._count(ctx, c)
        self.store.event(
            "classifier",
            "error" if not c.ok else "done",
            hunk=h.tag,
            task="classify",
            latency_s=round(c.latency_s, 2),
            tokens=c.prompt_tokens + c.completion_tokens,
            decision=label or "no answer",
            error=c.error,
            **ctx,
        )
        self.store.add(
            "attempts",
            {
                "kind": "classify",
                "tag": h.tag,
                "playbook": pb.version,
                "prompt": prompt,
                "response": c.text,
                "label": label,
                "model": c.model,
                "provider": c.provider,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "latency_s": c.latency_s,
                "error": c.error,
                **ctx,
            },
        )
        return label

    def _shared(self, table: dict, key: tuple, factory) -> asyncio.Task:
        if key not in table:
            table[key] = asyncio.ensure_future(factory())
        return table[key]

    async def _judge(self, h: Hunk, a: tuple, b: tuple, ctx: dict):
        self.store.event("judge", "start", hunk=h.tag, task="judge", **ctx)
        pick, c, prompt = await agents.judge(h, a, b)
        self._count(ctx, c)
        self.store.event(
            "judge",
            "error" if not c.ok else "done",
            hunk=h.tag,
            task="judge",
            latency_s=round(c.latency_s, 2),
            tokens=c.prompt_tokens + c.completion_tokens,
            decision=pick or "no answer",
            error=c.error,
            **ctx,
        )
        self.store.add(
            "attempts",
            {
                "kind": "judge",
                "tag": h.tag,
                "prompt": prompt,
                "response": c.text,
                "pick": pick,
                "model": c.model,
                "provider": c.provider,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "error": c.error,
                **ctx,
            },
        )
        return pick

    def preload(self, run: Path) -> int:
        """Reuse logged proposals and labels whose prompts are identical (no hint, first sample)."""
        loop = asyncio.get_running_loop()
        n = 0
        for a in read_jsonl(run, "attempts"):
            if a.get("kind") == "propose" and not a.get("retrieval") and not a.get("sample"):
                key = (a["persona"], a["playbook"], a["split"], a["tag"], False, 0)
                if key in self._props:
                    continue
                c = llm.Completion(
                    a.get("response") or "",
                    a.get("model") or "",
                    a.get("provider") or "",
                    a.get("prompt_tokens") or 0,
                    a.get("completion_tokens") or 0,
                    a.get("latency_s") or 0.0,
                    a.get("mean_logprob"),
                    a.get("error"),
                )
                lines = tuple(a["lines"]) if a.get("lines") is not None else None
                prop = agents.Proposal(
                    a["persona"],
                    lines,
                    tuple(a.get("lessons_used") or ()),
                    c,
                    a.get("reason") or "",
                    a.get("prompt") or "",
                )
                f = loop.create_future()
                f.set_result(prop)
                self._props[key] = f
                n += 1
            elif a.get("kind") == "classify":
                key = (a["playbook"], a["split"], a["tag"])
                if key not in self._cls:
                    f = loop.create_future()
                    f.set_result(a.get("label"))
                    self._cls[key] = f
        return n

    def cached_candidate(self, h: Hunk) -> algebra.Candidate | None:
        """The cached resolution for this conflict, voting only if a person confirmed it."""
        key = conflict_key(h)
        hit = self.rerere.get(key)
        if hit is None:
            return None
        return algebra.Candidate(tuple(hit), "op:rerere", True, key in self.confirmed)

    async def run_hunk(self, s: Strategy, h: Hunk, ctx: dict) -> Outcome:
        pb = self.playbooks[s.playbook]
        ops = operator_candidates(h, s.rule)
        if cached := self.cached_candidate(h):
            ops.append(cached)
        hint = retrieval.index().hint(h) if s.rule.get("retrieval") else ""
        samples = int(s.rule.get("samples", 1))
        slots = [(p, i) for p in s.personas for i in range(samples)]
        tasks = [
            self._shared(
                self._props,
                (p, pb.version, h.split, h.tag, bool(hint), i),
                lambda p=p, i=i: self._proposal(h, p, pb, ops, ctx, hint, i),
            )
            for p, i in slots
        ]
        cls_task = (
            self._shared(
                self._cls, (pb.version, h.split, h.tag), lambda: self._classify(h, pb, ctx)
            )
            if s.use_classifier
            else None
        )
        proposals = list(await asyncio.gather(*tasks))
        label = await cls_task if cls_task else None
        votes = [
            (p if i == 0 else f"{p}#{i}", prop.lines, prop.completion.mean_logprob)
            for (p, i), prop in zip(slots, proposals, strict=True)
        ]
        clusters = gate.cluster(h.vendor, h.upstream, ops, votes)
        n = len(slots)
        if s.rule.get("judge"):
            voted = sorted((c for c in clusters if c.personas), key=lambda c: -len(c.personas))
            if len(voted) >= 2 and len(voted[0].personas) - len(voted[1].personas) <= 1:
                a, b = voted[0].lines, voted[1].lines
                pick = await self._shared(
                    self._judges,
                    (h.split, h.tag, hash(score.normalize(a)), hash(score.normalize(b))),
                    lambda: self._judge(h, a, b, ctx),
                )
                if pick in ("A", "B"):
                    votes.append(("judge", a if pick == "A" else b, None))
                    clusters = gate.cluster(h.vendor, h.upstream, ops, votes)
                    n += 1
        d = gate.select(clusters, n, label, s.rule)
        out = Outcome(h.tag, ops, proposals, label, d, n)
        self.store.event(
            "gate",
            "decision",
            hunk=h.tag,
            source=d.source,
            confidence=round(d.confidence, 3),
            accepted=d.accepted(TRAIN_THRESHOLD, s.use_classifier),
            classifier=label,
            **ctx,
        )
        if d.disagreement:
            self.store.event(
                "gate",
                "disagreement",
                hunk=h.tag,
                clusters=[
                    {"label": c.label, "personas": c.personas, "ops": c.ops}
                    for c in clusters
                    if c.personas
                ],
                **ctx,
            )
        return out

    async def run(
        self, s: Strategy, hunks: list[Hunk], split: str, round_no: int
    ) -> dict[str, Outcome]:
        ctx = {"strategy": s.id, "split": split, "round": round_no}
        self.store.event("controller", "start", task=f"run {s.id} on {split}", **ctx)
        t0 = time.monotonic()
        res = await asyncio.gather(*(self.run_hunk(s, h, ctx) for h in hunks))
        self.store.event(
            "controller",
            "done",
            task=f"run {s.id} on {split}",
            latency_s=round(time.monotonic() - t0, 1),
            **ctx,
        )
        self.store.flush()
        return {o.tag: o for o in res}


def reselect(outcomes: dict[str, Outcome], s: Strategy) -> dict[str, Outcome]:
    out = {}
    for tag, o in outcomes.items():
        n = o.n_proposers or len(s.personas)
        d = gate.select(o.decision.clusters, n, o.classifier, s.rule)
        out[tag] = Outcome(tag, o.ops, o.proposals, o.classifier, d, n)
    return out


def _key(split: str, outcomes: dict[str, Outcome]) -> dict:
    """The split's key restricted to hunks that have an outcome, so a half split scores alone."""
    return {t: e for t, e in score.load_key(split).items() if t in outcomes}


def subset(outcomes: dict[str, Outcome], tags) -> dict[str, Outcome]:
    return {t: o for t, o in outcomes.items() if t in tags}


def measure(
    split: str, outcomes: dict[str, Outcome], s: Strategy, threshold: float = TRAIN_THRESHOLD
) -> dict:
    key = _key(split, outcomes)
    res = {t: o.decision.lines for t, o in outcomes.items()}
    acc = {t: o.decision.accepted(threshold, s.use_classifier) for t, o in outcomes.items()}
    m = score.evaluate(key, res, acc)
    m["threshold"] = threshold
    m["f1"] = sum(score.line_f1(res.get(t), e) for t, e in key.items()) / max(1, len(key))
    return m


def per_hunk(split: str, outcomes: dict[str, Outcome]) -> dict[str, float]:
    key = _key(split, outcomes)
    c = score.correctness(key, {t: o.decision.lines for t, o in outcomes.items()})
    return {t: float(v) for t, v in c.items()}


def calibrate(split: str, outcomes: dict[str, Outcome], s: Strategy) -> dict:
    key = _key(split, outcomes)
    correct = score.correctness(key, {t: o.decision.lines for t, o in outcomes.items()})
    return gate.calibrate(
        {t: o.decision for t, o in outcomes.items()},
        correct,
        s.use_classifier,
        env_float("TARGET_ESCAPED_ERROR", 0.05),
        env_float("CONFIDENCE_LEVEL", 0.95),
    )


def tune_rule(s: Strategy, outcomes: dict[str, Outcome]) -> tuple[dict, dict]:
    """Grid search over selection weights on train only. No model calls."""
    best_rule, best_m = s.rule, None
    for kv, cls, w_llm in itertools.product((0.0, 0.5, 1.0, 1.5), (0.0, 0.5, 1.0), (1.0, 2.0)):
        flags = {k: v for k, v in s.rule.items() if k in RULE_FLAGS}
        rule = {**flags, "w_llm": w_llm, "w_cls": cls if s.use_classifier else 0.0, "op_prior": {}}
        if kv:
            rule["op_prior"]["op:keep_vendor"] = kv
        trial = Strategy(s.id, s.personas, s.playbook, rule, s.use_classifier)
        m = measure("train", reselect(outcomes, trial), trial)
        if best_m is None or (m["accuracy"], m["coverage"]) > (
            best_m["accuracy"],
            best_m["coverage"],
        ):
            best_rule, best_m = rule, m
    return best_rule, best_m or {}


def update_counters(pb: Playbook, outcomes: dict[str, Outcome]) -> None:
    key = score.load_key("train")
    by_id = {x.id: x for x in pb.lessons}
    for tag, o in outcomes.items():
        if tag not in key:
            continue
        for p in o.proposals:
            ok = score.is_correct(p.lines, key[tag])
            for lid in p.lessons_used:
                if lid in by_id:
                    if ok:
                        by_id[lid].helpful += 1
                    else:
                        by_id[lid].harmful += 1


def failures(hunks: dict[str, Hunk], outcomes: dict[str, Outcome]) -> list[dict]:
    """Scored training failures with candidate-level outcomes. Never includes reference text."""
    key = score.load_key("train")
    out = []
    for tag, o in outcomes.items():
        if tag not in key or score.is_correct(o.decision.lines, key[tag]):
            continue
        cands = {}
        for c in o.decision.clusters:
            name = "+".join(c.ops + [f"llm:{p}" for p in c.personas])
            ok = score.is_correct(c.lines, key[tag])
            cands[name] = 1.0 if ok else round(score.line_f1(c.lines, key[tag]), 2)
        out.append(
            {
                "tag": tag,
                "prompt": hunks[tag].prompt,
                "chosen": list(o.decision.lines or []),
                "chosen_source": o.decision.source,
                "candidate_outcomes": cands,
            }
        )
    # Hunks some candidate got right come first, then near misses by line overlap.
    out.sort(key=lambda f: -max(f["candidate_outcomes"].values(), default=0.0))
    return out


def pareto(strategies: list[Strategy], size: int) -> list[Strategy]:
    def dominated(a: Strategy) -> bool:
        return any(
            b is not a
            and b.train["accuracy"] >= a.train["accuracy"]
            and b.train["coverage"] >= a.train["coverage"]
            and (
                b.train["accuracy"] > a.train["accuracy"]
                or b.train["coverage"] > a.train["coverage"]
            )
            for b in strategies
        )

    front = [s for s in strategies if not dominated(s)]
    rest = [s for s in strategies if dominated(s)]
    key = lambda s: (s.train["accuracy"], s.train["coverage"])
    return (sorted(front, key=key, reverse=True) + sorted(rest, key=key, reverse=True))[:size]


def summary(m: dict) -> dict:
    return {
        k: m[k]
        for k in (
            "n",
            "correct",
            "accuracy",
            "accepted",
            "coverage",
            "escaped",
            "certified_bound",
            "per_class",
            "f1",
        )
        if k in m
    }


async def search(rounds: int, objective: str = "accuracy", run_id: str | None = None) -> Path:
    llm.load_env()
    llm.BENCHMARK = True
    store = Store(run_id)
    if not llm.reportable():
        store.event("controller", "alert", level="warn", message="dev provider: not reportable")
    model = llm.model_for("generator")
    pop_size = env_int("POPULATION_SIZE", 6)
    children_per_round = env_int("CHILDREN_PER_ROUND", 2)
    cap = env_int("PLAYBOOK_MAX_LESSONS", 12)
    train = load_hunks("train")
    val = load_hunks("validation")
    train_key, val_key = score.load_key("train"), score.load_key("validation")
    train = [h for h in train if h.tag in train_key]
    val = [h for h in val if h.tag in val_key]
    by_tag = {h.tag: h for h in train}
    # Promotion and gate calibration use disjoint, seeded halves of validation, so the bound is
    # never computed on the hunks that chose the strategy.
    split_seed = env_int("VALIDATION_SPLIT_SEED", 0)
    sel_half, cal_half = halves("validation", split_seed)
    alpha = promotion_alpha(rounds)
    playbooks = {EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK}
    store.add("playbook_versions", {**EMPTY_PLAYBOOK.to_doc(), "round": 0})
    runner = Runner(store, playbooks)
    if warm := os.environ.get("WARM_CACHE"):
        # Reuse logged calls whose prompts are identical, so arms compared across runs share them.
        loop_reused = runner.preload(RUNS / warm)
        store.event("controller", "start", task=f"reused {loop_reused} logged calls from {warm}")

    kv_train = score.evaluate(train_key, {h.tag: h.vendor for h in train})
    kv_val = score.evaluate(val_key, {h.tag: h.vendor for h in val})

    baseline = Strategy("s-baseline", ["zero_shot"], "pb-0", BASELINE_RULE, False)
    teams = SEED_TEAMS[:pop_size]
    if os.environ.get("SEED_TEAM"):
        # A single-strategy control starts from one named seed team instead of the first ones.
        teams = [SEED_TEAMS[int(os.environ["SEED_TEAM"])]]
    seeds = [
        Strategy(new_id("s", team, "pb-0", DEFAULT_RULE, 0), team, "pb-0", dict(DEFAULT_RULE), True)
        for team in teams
    ]
    store.event("controller", "start", task=f"round 0 with model {model}", round=0)
    b_train, b_val, *seed_train = await asyncio.gather(
        runner.run(baseline, train, "train", 0),
        runner.run(baseline, val, "validation", 0),
        *(runner.run(s, train, "train", 0) for s in seeds),
    )
    baseline.train = summary(measure("train", b_train, baseline))
    b_cal = calibrate("validation", subset(b_val, cal_half), baseline)
    baseline.validation = summary(measure("validation", b_val, baseline, b_cal["threshold"]))
    baseline.validation["calibration"] = b_cal
    store.add("strategies", baseline.doc())
    train_outs: dict[str, dict[str, Outcome]] = {}
    for s, outs in zip(seeds, seed_train, strict=True):
        # Seeds get the same in-sample rule tuning as children, so learning is not confounded
        # with tuning.
        s.rule, m = tune_rule(s, outs)
        train_outs[s.id] = reselect(outs, s)
        s.train = summary(m)
        store.add("strategies", s.doc())

    population = pareto(seeds, pop_size)
    incumbent = population[0]
    inc_val = await runner.run(incumbent, val, "validation", 0)
    incumbent.validation = summary(measure("validation", inc_val, incumbent))
    store.add(
        "rounds",
        {
            "round": 0,
            "validation_halves": {
                "seed": split_seed,
                "selection": sorted(sel_half),
                "calibration": sorted(cal_half),
            },
            "promotion": {"alpha_per_test": alpha, "planned_tests": rounds},
            "baseline": {"train": baseline.train, "validation": baseline.validation},
            "keep_vendor": {"train": kv_train["accuracy"], "validation": kv_val["accuracy"]},
            "best_train": incumbent.train,
            "incumbent": incumbent.id,
            "incumbent_validation": incumbent.validation,
            "population": [s.id for s in population],
        },
    )
    store.event(
        "controller",
        "alert",
        level="info",
        message=f"round 0 done: baseline train {baseline.train['accuracy']:.3f}, "
        f"val {baseline.validation['accuracy']:.3f}; incumbent {incumbent.id} "
        f"val {incumbent.validation['accuracy']:.3f}",
        round=0,
    )
    store.flush()

    for r in range(1, rounds + 1):
        parents = population[:children_per_round]
        children = await asyncio.gather(
            *(
                make_child(p, r, train_outs[p.id], by_tag, train, runner, store, cap)
                for p in parents
            )
        )
        children = [c for c in children if c is not None]
        for c, outs in children:
            train_outs[c.id] = outs
        kids = [c for c, _ in children]
        population = pareto(population + kids, pop_size)
        decision = {"promoted": False, "reason": "no child"}
        if kids:
            challenger = max(kids, key=lambda s: (s.train["accuracy"], s.train["coverage"]))
            ch_val = await runner.run(challenger, val, "validation", r)
            challenger.validation = summary(measure("validation", ch_val, challenger))
            decision = promote(
                objective, incumbent, inc_val, challenger, ch_val, alpha, sel_half, cal_half
            )
            store.add(
                "strategies", {**challenger.doc(), "promotion": decision, "evaluated_on_val": True}
            )
            if decision["promoted"]:
                incumbent, inc_val = challenger, ch_val
            store.event(
                "controller",
                "alert",
                level="promotion" if decision["promoted"] else "info",
                message=f"round {r}: {challenger.id} "
                + ("promoted" if decision["promoted"] else "rejected")
                + f" ({decision['reason']})",
                round=r,
            )
        best = population[0]
        store.add(
            "rounds",
            {
                "round": r,
                "best_train": best.train,
                "best_train_id": best.id,
                # The max over children is selection on train; the mean is the fair summary.
                "children_train": [k.train["accuracy"] for k in kids],
                "children_train_mean": sum(k.train["accuracy"] for k in kids) / len(kids)
                if kids
                else None,
                "incumbent": incumbent.id,
                "incumbent_validation": incumbent.validation,
                "promotion": decision,
                "population": [s.id for s in population],
                "keep_vendor": {"train": kv_train["accuracy"], "validation": kv_val["accuracy"]},
            },
        )
        store.flush()

    cal = calibrate("validation", subset(inc_val, cal_half), incumbent)
    cal["half"] = {"seed": split_seed, "calibration": len(cal_half), "selection": len(sel_half)}
    incumbent.validation = summary(measure("validation", inc_val, incumbent, cal["threshold"]))
    incumbent.validation["selection_half"] = summary(
        measure("validation", subset(inc_val, sel_half), incumbent, cal["threshold"])
    )
    incumbent.validation["calibration_half"] = summary(
        measure("validation", subset(inc_val, cal_half), incumbent, cal["threshold"])
    )
    incumbent.validation["calibration"] = cal
    rerere = {}
    for hunks, outs in ((train, train_outs.get(incumbent.id, {})), (val, inc_val)):
        for h in hunks:
            o = outs.get(h.tag)
            if o and o.decision.accepted(cal["threshold"], incumbent.use_classifier):
                rerere[conflict_key(h)] = list(o.decision.lines or [])
    final = {
        "run_id": store.run_id,
        "model": model,
        "thinking": llm.thinking(),
        "provider": llm.provider()[0],
        "strategy": incumbent.doc(),
        "playbook": playbooks[incumbent.playbook].to_doc(),
        "baseline": baseline.doc(),
        "rerere": rerere,
        "created": time.time(),
    }
    (store.dir / "final.json").write_text(json.dumps(final, indent=1, default=str))
    store.add("evals", {"kind": "final_validation", **final})
    store.event(
        "controller",
        "alert",
        level="info",
        message=f"search finished: incumbent {incumbent.id} val accuracy "
        f"{incumbent.validation['accuracy']:.3f}, coverage {incumbent.validation['coverage']:.3f} "
        f"at threshold {cal['threshold']:.3f} (bound {cal['bound']:.3f}, "
        f"target met: {cal['target_met']})",
    )
    store.close()
    await llm.close()
    return store.dir


async def make_child(
    parent: Strategy,
    r: int,
    outs: dict[str, Outcome],
    by_tag: dict[str, Hunk],
    train: list[Hunk],
    runner: Runner,
    store: Store,
    cap: int,
) -> tuple[Strategy, dict[str, Outcome]] | None:
    pb = runner.playbooks[parent.playbook]
    fails = failures(by_tag, outs)
    if not fails:
        return None
    ctx = {"strategy": parent.id, "split": "train", "round": r}
    store.event("reflector", "start", task=f"reflect on {len(fails)} failures", **ctx)
    lessons, c, prompt = await agents.reflect(fails)
    store.event(
        "reflector",
        "error" if not c.ok else "done",
        task="reflect",
        latency_s=round(c.latency_s, 2),
        tokens=c.prompt_tokens + c.completion_tokens,
        decision=f"{len(lessons)} lessons",
        error=c.error,
        **ctx,
    )
    store.add(
        "attempts",
        {"kind": "reflect", "prompt": prompt, "response": c.text, "lessons": lessons, **ctx},
    )
    store.event("curator", "start", task="curate", **ctx)
    child_pb, rejected, cc, curate_prompt = await agents.curate(pb, lessons, r, cap)
    store.add(
        "attempts",
        {
            "kind": "curate",
            "prompt": curate_prompt,
            "response": cc.text if cc else "",
            "playbook": child_pb.version,
            "parent_playbook": pb.version,
            "rejected": rejected,
            **ctx,
        },
    )
    store.event(
        "curator",
        "done" if cc is None or cc.ok else "error",
        task="curate",
        latency_s=round(cc.latency_s, 2) if cc else 0,
        tokens=(cc.prompt_tokens + cc.completion_tokens) if cc else 0,
        decision=f"{len(child_pb.lessons)} lessons, {len(rejected)} rejected",
        rejected=rejected,
        **ctx,
    )
    runner.playbooks[child_pb.version] = child_pb
    store.add("playbook_versions", {**child_pb.to_doc(), "round": r, "rejected": rejected})
    child = Strategy(
        new_id("s", parent.personas, child_pb.version, parent.rule, parent.id, r),
        parent.personas,
        child_pb.version,
        parent.rule,
        True,
        parent.id,
        r,
    )
    child_outs = await runner.run(child, train, "train", r)
    # Score the new lessons on the child's own train run, so a playbook made in the last round
    # still carries helpful and harmful counts.
    update_counters(child_pb, child_outs)
    store.add("playbook_versions", {**child_pb.to_doc(), "round": r, "scored_on": "train"})
    child.rule, m = tune_rule(child, child_outs)
    child_outs = reselect(child_outs, child)
    child.train = summary(m)
    store.add("strategies", child.doc())
    return child, child_outs


def promotion_alpha(rounds: int) -> float:
    """Alpha for one promotion test: PROMOTION_ALPHA spent evenly over the planned rounds, so the
    chance of any false promotion in the run stays at or below PROMOTION_ALPHA."""
    return env_float("PROMOTION_ALPHA", 0.05) / max(1, rounds)


def promote(
    objective: str,
    incumbent: Strategy,
    inc_val: dict[str, Outcome],
    challenger: Strategy,
    ch_val: dict[str, Outcome],
    alpha: float = 0.05,
    select: frozenset | None = None,
    calib: frozenset | None = None,
) -> dict:
    """Promote only if a one-sided exact McNemar test on the selection half rejects "the
    challenger is no better" at `alpha`. With the coverage objective each strategy's gate is
    calibrated on the calibration half and safe accuracy is compared on the selection half."""
    sel = select if select is not None else frozenset(inc_val)
    cal = calib if calib is not None else frozenset(inc_val)
    if objective == "coverage":
        key = score.load_key("validation")

        def safe_acc(s: Strategy, outs: dict[str, Outcome]) -> dict[str, float]:
            thr = gate.usable_threshold(calibrate("validation", subset(outs, cal), s))
            return {
                t: float(
                    o.decision.accepted(thr, s.use_classifier)
                    and score.is_correct(o.decision.lines, key[t])
                )
                for t, o in subset(outs, sel).items()
                if t in key
            }

        a, b = safe_acc(incumbent, inc_val), safe_acc(challenger, ch_val)
    else:
        a = per_hunk("validation", subset(inc_val, sel))
        b = per_hunk("validation", subset(ch_val, sel))
    test = stats.mcnemar(a, b)
    boot = score.paired_bootstrap(a, b, env_int("BOOTSTRAP_RESAMPLES", 10000))
    promoted = test["b_only"] > test["a_only"] and test["p_b_better"] < alpha
    reason = (
        f"{objective} on {test['n']} selection hunks: discordant {test['a_only']} vs "
        f"{test['b_only']}, one-sided exact p {test['p_b_better']:.3f} vs alpha {alpha:.4f}; "
        f"delta {boot['delta']:+.3f}, 95% CI [{boot['lo']:+.3f}, {boot['hi']:+.3f}]"
    )
    return {
        "promoted": promoted,
        "reason": reason,
        "objective": objective,
        "alpha": alpha,
        "mcnemar": test,
        **boot,
    }


def freeze(run_dir: Path) -> dict:
    if FROZEN.exists():
        raise RuntimeError(f"a strategy is already frozen: {FROZEN}")
    final = json.loads((run_dir / "final.json").read_text())
    frozen = {**final, "frozen_at": time.time(), "frozen_strategy_id": final["strategy"]["id"]}
    FROZEN.write_text(json.dumps(frozen, indent=1))
    # Shipped inside the package so `uvx forkward-mcp` resolves with the frozen strategy.
    PACKAGED.write_text(json.dumps({k: v for k, v in frozen.items() if k != "baseline"}, indent=1))
    return frozen


async def evaluate_test() -> Path:
    """The single test evaluation of the frozen strategy and the round-0 baseline."""
    llm.load_env()
    llm.BENCHMARK = True
    if TEST_DONE.exists():
        raise RuntimeError(f"the test split was already evaluated: {TEST_DONE}")
    if not FROZEN.exists():
        raise RuntimeError("no frozen strategy: run `python -m forkward freeze <run>` first")
    if not llm.reportable():
        raise RuntimeError("the test evaluation must run on Luminal Cloud")
    frozen = json.loads(FROZEN.read_text())
    model = llm.model_for("generator")
    if model != frozen["model"]:
        raise RuntimeError(
            f"resolver model {model} differs from the frozen model {frozen['model']}"
        )
    if llm.thinking() != frozen.get("thinking", False):
        raise RuntimeError("LLM_THINKING differs from the setting the strategy was frozen with")
    TEST_DONE.write_text(
        json.dumps({"started": time.time(), "frozen": frozen["frozen_strategy_id"]})
    )

    store = Store(f"test-{time.strftime('%Y%m%dT%H%M%S')}")
    pb = Playbook.from_doc(frozen["playbook"])
    s = Strategy(
        **{k: v for k, v in frozen["strategy"].items() if k in Strategy.__dataclass_fields__}
    )
    b = Strategy(
        **{k: v for k, v in frozen["baseline"].items() if k in Strategy.__dataclass_fields__}
    )
    runner = Runner(
        store, {pb.version: pb, EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK}, frozen["rerere"]
    )
    hunks = load_hunks("test", allow_test=True)
    store.event("controller", "start", task=f"test run of frozen {s.id}", split="test")
    s_outs, b_outs = await asyncio.gather(
        runner.run(s, hunks, "test", -1), runner.run(b, hunks, "test", -1)
    )
    key = score.load_key("test")
    s_thr = s.validation["calibration"]["threshold"]
    b_thr = b.validation["calibration"]["threshold"]
    s_m = measure("test", s_outs, s, s_thr)
    b_m = measure("test", b_outs, b, b_thr)
    kv = score.evaluate(key, {h.tag: h.vendor for h in hunks})

    def safe(outs: dict[str, Outcome], st: Strategy, thr: float) -> dict[str, float]:
        return {
            t: float(
                outs[t].decision.accepted(thr, st.use_classifier)
                and score.is_correct(outs[t].decision.lines, key[t])
            )
            for t in key
        }

    n_boot = env_int("BOOTSTRAP_RESAMPLES", 10000)
    result = {
        "frozen_strategy_id": s.id,
        "model": model,
        "thinking": llm.thinking(),
        "provider": llm.provider()[0],
        "frozen": summary(s_m),
        "baseline": summary(b_m),
        "keep_vendor": summary(kv),
        "accuracy_delta": score.paired_bootstrap(
            per_hunk("test", b_outs), per_hunk("test", s_outs), n_boot
        ),
        # The primary comparison: the frozen strategy against doing nothing (keep vendor).
        "frozen_vs_keep_vendor": stats.compare(
            {t: float(score.is_correct(h.vendor, key[t])) for h in hunks if (t := h.tag) in key},
            per_hunk("test", s_outs),
            stats.clusters_for("test"),
            n_boot,
        ),
        "frozen_vs_baseline": stats.compare(
            per_hunk("test", b_outs), per_hunk("test", s_outs), stats.clusters_for("test"), n_boot
        ),
        "safe_coverage_delta": score.paired_bootstrap(
            safe(b_outs, b, b_thr), safe(s_outs, s, s_thr), n_boot
        ),
        "thresholds": {"frozen": s_thr, "baseline": b_thr},
        "attempts_per_hunk": 1,
        "tokens": {
            "frozen": runner.tokens.get(s.id, 0),
            "baseline": runner.tokens.get(b.id, 0),
            "frozen_per_correct_hunk": round(runner.tokens.get(s.id, 0) / max(1, s_m["correct"])),
            "baseline_per_correct_hunk": round(runner.tokens.get(b.id, 0) / max(1, b_m["correct"])),
        },
        "llm_usage": asdict(llm.USAGE),
    }
    store.add("evals", {"kind": "test", **result})
    (store.dir / "results.json").write_text(json.dumps(result, indent=1, default=str))
    TEST_DONE.write_text(
        json.dumps({"finished": time.time(), "frozen": s.id, "run": store.run_id}, indent=1)
    )
    store.event(
        "controller",
        "alert",
        level="info",
        message=f"test finished: accuracy {b_m['accuracy']:.3f} -> {s_m['accuracy']:.3f}, "
        f"coverage {b_m['coverage']:.3f} -> {s_m['coverage']:.3f}, "
        f"escaped {s_m['escaped']}/{s_m['accepted']}, bound {s_m['certified_bound']:.3f}",
        split="test",
    )
    for t, o in s_outs.items():
        if t in key and not o.decision.accepted(s_thr, s.use_classifier):
            store.event("gate", "needs_review", hunk=t, split="test", source=o.decision.source)
    store.close()
    await llm.close()
    return store.dir
