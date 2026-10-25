"""Sync a fork with upstream: merge, resolve conflict hunks through the gate, repair the build, open a PR."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from forkward import gate as safety_gate
from forkward import github_app, llm, repair, sandbox
from forkward.agents import EMPTY_PLAYBOOK, Playbook
from forkward.data import Hunk
from forkward.search import FROZEN, PACKAGED, Runner, Strategy, conflict_key
from forkward.store import RUNS, Store

CACHE = Path(os.environ.get("FORKWARD_CACHE", Path.home() / ".cache" / "forkward"))
CONTEXT = 30
# The git host repositories are cloned from. GitHub by default; a GitHub Enterprise Server URL,
# or a local directory for end-to-end tests, also work.
GIT_URL = os.environ.get("FORKWARD_GIT_URL", "https://github.com").rstrip("/")
DIFF_LINES = 110


@dataclass
class HunkResult:
    file: str
    hunk_id: str
    resolution: str
    confidence: float
    gate: str
    reason: str
    key: str = ""
    # What the review screen shows: both sides, every candidate with its sources and votes.
    detail: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    repo: str
    branch: str
    upstream: str
    clean_merge: bool
    hunks: list[HunkResult] = field(default_factory=list)
    repair: dict | None = None
    pr_url: str | None = None
    strategy: str | None = None
    # Why the build was not verified, when it was not. Set while conflict markers remain.
    verification: str | None = None
    upstream_sha: str | None = None

    @property
    def auto_applied(self) -> int:
        return sum(h.gate == "auto_apply" for h in self.hunks)

    @property
    def needs_review(self) -> int:
        return sum(h.gate != "auto_apply" for h in self.hunks)


GIT_TIMEOUT = float(os.environ.get("FORKWARD_GIT_TIMEOUT", "600"))
LOCK_WAIT = float(os.environ.get("FORKWARD_LOCK_WAIT", "900"))


def git(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """Run git with a deadline that kills the whole process group. Raises RuntimeError on failure
    or timeout when `check` is set."""
    argv = ["git", "-c", "merge.conflictStyle=merge", *args]
    try:
        p = sandbox.run_bounded(argv, repo, timeout or GIT_TIMEOUT, env=env)
    except TimeoutError as e:
        raise RuntimeError(f"git {args[0]} timed out: {e}") from None
    if check and p.returncode != 0:
        # Some failures ("nothing to commit") are reported on stdout only.
        detail = p.stderr.strip() or p.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail[:500]}")
    return p.stdout


# GitHub owners are 1 to 39 letters, digits or hyphens and never start with a hyphen. Repository
# names are letters, digits, '.', '_' and '-'. The owner cannot hold '_', so `owner__name` cache
# directories never collide.
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,255}")
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def validate_repo(repo: str) -> str:
    """owner/name on GitHub, with no `..`, leading dash or extra path components."""
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise ValueError(f"repo must be a GitHub owner/name, got {repo!r}")
    name = repo.split("/", 1)[1]
    if name in (".", "..") or name.startswith("-") or name.endswith(".git"):
        raise ValueError(f"repo must be a GitHub owner/name, got {repo!r}")
    return repo


def validate_ref(ref: str) -> str:
    """A branch, tag or commit name. Refspecs (`:`, `+`, `*`), revision syntax (`^`, `~`, `@{`),
    option-like values, `..`, whitespace and control characters are refused."""
    if (
        not isinstance(ref, str)
        or not REF_RE.fullmatch(ref)
        or ref.startswith(("-", "/", "."))
        or ref.endswith(("/", ".", ".lock"))
        or ".." in ref
        or "//" in ref
        or "/." in ref
    ):
        raise ValueError(f"not an acceptable git ref: {ref!r}")
    return ref


def validate_upstream(upstream: str) -> str:
    """`owner/name@ref`, or a ref the repository already has."""
    if isinstance(upstream, str) and "@" in upstream:
        slug, ref = upstream.split("@", 1)
        validate_repo(slug)
        validate_ref(ref)
    else:
        validate_ref(upstream)
    return upstream


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


@contextlib.contextmanager
def repo_lock(path: Path, wait: float | None = None):
    """One git operation at a time per repository, across threads and processes, so concurrent
    requests never race on refs, the index or worktree metadata. Waits up to `wait` seconds
    (FORKWARD_LOCK_WAIT by default), then raises RuntimeError."""
    locks = CACHE / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
    with (locks / f"{name}.lock").open("a") as f:
        deadline = time.monotonic() + (LOCK_WAIT if wait is None else wait)
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "the repository is busy with another sync; try again shortly"
                    ) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def is_cached(path: Path) -> bool:
    """True for clones Forkward made in its cache, as opposed to a user's own local checkout."""
    try:
        return path.resolve().is_relative_to(CACHE.resolve())
    except OSError:
        return False


def checkout(repo: str, allow_local: bool | None = None, token: str | None = None) -> Path:
    """A local path is used as is, except on the hosted service. owner/name is cloned (or
    fetched) from GitHub into the cache, with `token` when given so private forks work. Call with
    the repository lock held."""
    if allow_local is None:
        allow_local = not sandbox.hosted()
    if allow_local and Path(repo).expanduser().is_dir():
        return Path(repo).expanduser().resolve()
    validate_repo(repo)
    path = CACHE / _slug(repo)
    env = github_env(token) if token else None
    if path.exists():
        # Fresh branches every time, and tags forced to match origin, so nothing written into the
        # cache earlier can stand in for what the fork has now.
        git(
            path,
            "fetch",
            "--quiet",
            "--prune",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/tags/*",
            env=env,
        )
        git(path, "remote", "set-head", "origin", "--auto", check=False, env=env)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        git(
            path.parent,
            "clone",
            "--quiet",
            "--no-checkout",
            "--",
            f"{GIT_URL}/{repo}.git",
            str(path),
            env=env,
        )
    return path


def resolve_base(path: Path, base: str | None) -> tuple[str, str]:
    """(commit SHA, branch name) of the base to merge into. In a cached clone the base is always
    read from origin as just fetched, never from a stale local branch."""
    if not is_cached(path):
        name = base or git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
        validate_ref(name)
        return git(path, "rev-parse", "--verify", f"{name}^{{commit}}").strip(), name
    if base and SHA_RE.fullmatch(base):
        _require_from_origin(path, base)
        return base, base
    name = (base or "HEAD").removeprefix("origin/")
    if name == "HEAD":
        name = default_branch(path)
    validate_ref(name)
    for ref in (f"refs/remotes/origin/{name}", f"refs/tags/{name}"):
        sha = git(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        if sha.strip():
            return sha.strip(), name
    raise ValueError(f"base {name!r} is not a branch or tag of origin")


def _require_from_origin(path: Path, sha: str) -> None:
    """A commit in the shared object store may have been fetched by another request. Only a
    commit reachable from origin's own refs may be used by name."""
    hit = git(
        path,
        "for-each-ref",
        "--count=1",
        "--contains",
        sha,
        "refs/remotes/origin/",
        "refs/tags/",
        check=False,
    )
    if not hit.strip():
        raise ValueError(f"{sha} is not a commit of this repository")


def fetch_upstream(path: Path, upstream: str) -> str:
    """`owner/name@ref` fetches from GitHub into a ref private to this request and returns the
    commit SHA. Anything else is a ref already known to the repository. Call with the lock held."""
    validate_upstream(upstream)
    if "@" in upstream:
        slug, ref = upstream.split("@", 1)
        url = f"{GIT_URL}/{slug}.git"
        ns = f"refs/forkward/req/{uuid.uuid4().hex}"
        try:
            try:
                git(path, "fetch", "--quiet", "--no-tags", "--", url, f"+{ref}:{ns}/head")
                return git(path, "rev-parse", "--verify", f"{ns}/head^{{commit}}").strip()
            except RuntimeError:
                # GitHub only serves full SHAs or ref names; a short SHA is resolved after fetching
                # that repository's branches, and must be reachable from them.
                if not re.fullmatch(r"[0-9a-f]{7,39}", ref):
                    raise
                git(path, "fetch", "--quiet", "--no-tags", "--", url, f"+refs/heads/*:{ns}/*")
                sha = git(path, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
                if not git(path, "for-each-ref", "--count=1", "--contains", sha, f"{ns}/").strip():
                    raise ValueError(f"{ref} is not a commit of {slug}") from None
                return sha
        finally:
            for r in git(
                path, "for-each-ref", "--format=%(refname)", f"{ns}/", check=False
            ).split():
                git(path, "update-ref", "-d", r, check=False)
    if is_cached(path):
        if SHA_RE.fullmatch(upstream):
            _require_from_origin(path, upstream)
            return upstream
        for ref in (
            f"refs/remotes/origin/{upstream.removeprefix('origin/')}",
            f"refs/tags/{upstream}",
        ):
            sha = git(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
            if sha.strip():
                return sha.strip()
        raise ValueError(f"{upstream!r} is not a branch or tag of origin")
    return git(path, "rev-parse", "--verify", f"{upstream}^{{commit}}").strip()


def _is_start(line: str, ours: str | None) -> bool:
    line = line.rstrip("\r")
    if ours is None:
        return line.startswith("<<<<<<<")
    return line == f"<<<<<<< {ours}" or line.startswith(f"<<<<<<< {ours}:")


def _is_end(line: str, theirs: str | None) -> bool:
    line = line.rstrip("\r")
    if theirs is None:
        return line.startswith(">>>>>>>")
    return line == f">>>>>>> {theirs}" or line.startswith(f">>>>>>> {theirs}:")


def _blocks(
    lines: list[str], ours: str | None = None, theirs: str | None = None
) -> list[tuple[int, int]]:
    """Conflict blocks, matched on the exact labels git wrote for this merge (`HEAD` and the
    upstream SHA), so marker-like lines already in the file are not mistaken for conflicts."""
    out, start = [], None
    for i, line in enumerate(lines):
        if _is_start(line, ours):
            if start is not None:
                raise ValueError(f"nested conflict marker at line {i + 1}")
            start = i
        elif _is_end(line, theirs) and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        raise ValueError(f"unterminated conflict block at line {start + 1}")
    return out


def _sides(block: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    body = block[1:-1]
    seps = [i for i, x in enumerate(body) if x.rstrip("\r") == "======="]
    if len(seps) != 1:
        raise ValueError(f"expected one ======= separator in a conflict block, found {len(seps)}")
    sep = seps[0]
    ours = body[:sep]
    base = next((i for i, x in enumerate(ours) if x.startswith("|||||||")), None)
    if base is not None:
        ours = ours[:base]
    return tuple(ours), tuple(body[sep + 1 :])


def _diff(path: Path, a: str, b: str, file: str) -> str:
    d = git(path, "diff", "-U3", a, b, "--", file, check=False).splitlines()
    d = [x for x in d if not x.startswith(("diff --git", "index ", "--- ", "+++ "))]
    if len(d) > DIFF_LINES:
        d = d[:DIFF_LINES] + [f"... ({len(d) - DIFF_LINES} more diff lines omitted)"]
    return "\n".join(d)


def unmerged_files(path: Path) -> list[str]:
    """Conflicted paths, NUL-separated so names with spaces or newlines survive."""
    out = git(path, "diff", "-z", "--name-only", "--diff-filter=U")
    return sorted({f for f in out.split("\0") if f})


def _non_text_reason(path: Path, f: str) -> str:
    stages = {
        line.split("\t", 1)[0].split()[-1]
        for line in git(path, "ls-files", "-u", "-z", "--", f).split("\0")
        if line.strip()
    }
    kept = "this branch keeps the version git left in the tree"
    if not (path / f).exists():
        kept = "this branch keeps the deletion"
    if "2" not in stages:
        return f"modify/delete conflict: deleted in this fork, changed upstream; {kept}"
    if "3" not in stages:
        return f"modify/delete conflict: deleted upstream, changed in this fork; {kept}"
    return f"conflict without text markers (binary, rename or mode change); {kept}"


def conflict_hunks(
    path: Path, upstream_sha: str, skipped: list[tuple[str, str]] | None = None
) -> list[tuple[Hunk, str, tuple[int, int]]]:
    """Hunks in the benchmark's agent-visible format, plus their file and block position.
    Conflicts that are not text hunks, and files whose markers cannot be parsed, are appended to
    `skipped` as (file, reason) for a person; one bad file never stops the others."""
    skipped = [] if skipped is None else skipped
    base = git(path, "merge-base", "HEAD", upstream_sha).strip()
    out = []
    for f in unmerged_files(path):
        try:
            target = path / f
            if not target.is_file():
                skipped.append((f, _non_text_reason(path, f)))
                continue
            raw = target.read_bytes()
            if b"\0" in raw[:8000]:
                skipped.append((f, _non_text_reason(path, f)))
                continue
            lines = raw.decode("utf-8", errors="replace").split("\n")
            blocks = _blocks(lines, "HEAD", upstream_sha)
            if not blocks:
                skipped.append((f, _non_text_reason(path, f)))
                continue
            up = _diff(path, base, upstream_sha, f)
            ven = _diff(path, base, "HEAD", f)
            mine = []
            for i, (s, e) in enumerate(blocks):
                lo, hi = max(0, s - CONTEXT), min(len(lines), e + CONTEXT + 1)
                tag = f"{f}#{i + 1}"
                prompt = (
                    f"### HUNK {tag}\nFile: {f}\nConflict lines {s + 1} to {e + 1} of the merged file.\n\n"
                    f"Merged file excerpt (lines {lo + 1} to {hi}); resolve ONLY the conflict block at "
                    f"lines {s + 1} to {e + 1}:\n```\n"
                    + "\n".join(lines[lo:hi])
                    + f"\n```\n\nUpstream change to this file near the conflict:\n```diff\n{up}\n```\n\n"
                    f"Fork change to this file near the conflict:\n```diff\n{ven}\n```\n"
                )
                vendor, upstream = _sides(lines[s : e + 1])
                mine.append((Hunk(tag, "live", f, prompt, vendor, upstream, up, ven), f, (s, e)))
            out += mine
        except (OSError, ValueError, RuntimeError) as err:
            skipped.append((f, f"could not read the conflict ({err}); resolve this file by hand"))
    return out


def _merge(path: Path, sha: str) -> bool:
    """True when the merge is clean, False when it stopped on conflicts. Any other failure raises,
    so a merge git refused (newer git wants a committer identity even with --no-commit) is never
    mistaken for one with zero hunks."""
    try:
        p = sandbox.run_bounded(
            ["git", "-c", "merge.conflictStyle=merge", *commit_identity()]
            + ["merge", "--no-ff", "--no-commit", sha],
            path,
            GIT_TIMEOUT,
        )
    except TimeoutError as e:
        raise RuntimeError(f"git merge timed out: {e}") from None
    if p.returncode == 0:
        return True
    if unmerged_files(path):
        return False
    raise RuntimeError(f"git merge failed: {p.stderr.strip()[:500]}")


def load_strategy() -> tuple[Strategy, Playbook, float, dict, str]:
    """The frozen strategy, or the newest search result when nothing is frozen yet."""
    src = FROZEN if FROZEN.exists() else PACKAGED
    if not src.exists():
        finals = sorted(RUNS.glob("*/final.json"), key=lambda p: p.stat().st_mtime)
        if not finals:
            raise RuntimeError("no trained strategy: run `python -m forkward search` first")
        src = finals[-1]
    d = json.loads(src.read_text())
    fields = Strategy.__dataclass_fields__
    s = Strategy(**{k: v for k, v in d["strategy"].items() if k in fields})
    s.frozen_model = (d.get("model"), d.get("provider"))  # type: ignore[attr-defined]
    threshold = gate_threshold(s, d.get("model"), d.get("provider"))
    return s, Playbook.from_doc(d["playbook"]), threshold, d.get("rerere", {}), str(src)


def gate_threshold_for(s: Strategy) -> float:
    model, provider = getattr(s, "frozen_model", (None, None))
    return gate_threshold(s, model, provider)


UNCALIBRATED = safety_gate.ABSTAIN


def calibration_path(strategy_id: str, provider: str, model: str) -> Path:
    safe = re.sub(r"[^\w.-]+", "_", f"{strategy_id}__{provider}__{model}")
    return RUNS / "calibrations" / f"{safe}.json"


def gate_threshold(s: Strategy, frozen_model: str | None, frozen_provider: str | None) -> float:
    """The gate is calibrated per model. With any other model nothing auto-applies until
    `python -m forkward calibrate` measures that model on the validation split."""
    provider = llm.provider()[0]
    model = llm.model_for("generator")
    if model == frozen_model and provider == (frozen_provider or "luminal"):
        return safety_gate.usable_threshold(s.validation.get("calibration"))
    p = calibration_path(s.id, provider, model)
    if p.exists():
        return safety_gate.usable_threshold(json.loads(p.read_text()))
    return UNCALIBRATED


class TooLarge(ValueError):
    pass


async def resolve(
    path: Path,
    upstream_sha: str,
    store: Store,
    repo: str | None = None,
    max_hunks: int | None = None,
    on_hunks=None,
) -> tuple[list, list[HunkResult], str]:
    """`on_hunks(n)` is awaited with the hunk count before any model call, so a caller can
    reserve billing units or refuse the job. Conflicts that are not text hunks come back as
    `blocked` file-level results after the hunk results."""
    skipped: list[tuple[str, str]] = []
    hunks = await asyncio.to_thread(conflict_hunks, path, upstream_sha, skipped)
    file_results = [HunkResult(f, f"{f}#file", "", 0.0, "blocked", why) for f, why in skipped]
    if max_hunks is not None and len(hunks) > max_hunks:
        raise TooLarge(
            f"{len(hunks)} conflict hunks is more than the {max_hunks} allowed in one call; "
            "sync smaller upstream steps"
        )
    if on_hunks is not None:
        await on_hunks(len(hunks))
    if not hunks:
        return [], file_results, ""
    s, pb, threshold, rerere, src = load_strategy()
    confirmed: dict = {}
    if repo:
        # Resolutions a person already merged for this repository are reused first, and only
        # they may count as an operator vote.
        from forkward import history

        confirmed = history.rerere(repo)
        rerere = {**rerere, **confirmed}
    runner = Runner(
        store,
        {pb.version: pb, EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK},
        rerere,
        confirmed=set(confirmed),
    )
    outs = await runner.run(s, [h for h, _, _ in hunks], "live", -1)
    results = []
    for h, f, _ in hunks:
        o = outs.get(h.tag)
        if o is None:
            # The resolver lost this hunk; it goes to a person rather than failing the sync.
            results.append(
                HunkResult(
                    f,
                    h.tag,
                    "",
                    0.0,
                    "blocked",
                    "The resolvers failed on this hunk",
                    conflict_key(h),
                )
            )
            continue
        d = o.decision
        ok = d.accepted(threshold, s.use_classifier)
        # Reasons are read by people on the job page and in the pull request, so they are plain
        # sentences rather than gate internals.
        if d.lines is None:
            gate, why = "blocked", "No resolver produced a usable resolution"
        elif ok:
            gate = "auto_apply"
            why = "The models and a rule agreed, and confidence cleared the calibrated bar"
        else:
            gate = "needs_review"
            why = (
                "No rule produced the resolution the models chose, so nothing independent confirms it"
                if not d.has_op
                else f"The classifier expected {LABEL_WORDS.get(d.classifier or '', 'something else')}, "
                "which is not what the resolvers chose"
                if s.use_classifier and not d.classifier_agrees
                else "The safety gate is not certified for this model yet, so every hunk goes to a "
                "person"
                if threshold >= UNCALIBRATED
                else f"Confidence {d.confidence:.2f} is under the calibrated bar of {threshold:.2f}"
            )
        results.append(
            HunkResult(
                f,
                h.tag,
                "\n".join(d.lines or ()),
                round(d.confidence, 3),
                gate,
                why,
                conflict_key(h),
                hunk_detail(h, d),
            )
        )
    return hunks, results + file_results, f"{s.id} ({Path(src).name})"


LABEL_WORDS = {
    "keep_vendor": "the fork's side kept",
    "take_upstream": "upstream's side taken",
    "merge": "both sides combined",
}


def hunk_detail(h: Hunk, d) -> dict:
    """Both sides of the conflict and every candidate, for the review screen. Candidates are
    ordered with the chosen one first, then by votes."""
    chosen = tuple(d.lines) if d.lines is not None else None
    cands = []
    for c in d.clusters:
        sources = [s.removeprefix("op:") for s in c.ops] + [f"model:{p}" for p in c.personas]
        if not sources:
            continue
        cands.append(
            {
                "lines": list(c.lines),
                "sources": sources,
                "votes": len(c.personas),
                "label": c.label,
                "chosen": chosen is not None and tuple(c.lines) == chosen,
            }
        )
    cands.sort(key=lambda c: (not c["chosen"], -c["votes"], -len(c["sources"])))
    return {
        "vendor": list(h.vendor),
        "upstream": list(h.upstream),
        "candidates": cands[:8],
        "classifier": d.classifier,
        "margin": d.margin,
        "proposers": d.n_proposers,
    }


def apply(path: Path, hunks: list, results: list[HunkResult]) -> list[str]:
    """Write only the resolutions the gate accepted (`auto_apply`). Hunks that need review or are
    blocked keep their conflict markers, so model text the gate did not certify never lands in the
    tree as if it were merged. File-level conflicts (results after the hunk results) stay as git
    left them. Every conflicted file is staged either way. Returns the files a person must still
    resolve."""
    by_file: dict[str, list] = {}
    for (h, f, pos), r in zip(hunks, results[: len(hunks)], strict=True):
        by_file.setdefault(f, []).append((pos, r))
    unresolved = []
    for f, items in by_file.items():
        target = path / f
        # surrogateescape round-trips bytes that are not UTF-8, so untouched lines stay identical.
        lines = target.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
        for (s, e), r in sorted(items, key=lambda x: x[0][0], reverse=True):
            if r.gate == "auto_apply":
                lines[s : e + 1] = r.resolution.split("\n") if r.resolution else []
        target.write_text("\n".join(lines), encoding="utf-8", errors="surrogateescape")
        if any(r.gate != "auto_apply" for _, r in items):
            unresolved.append(f)
        git(path, "add", "--", f)
    for r in results[len(hunks) :]:
        git(path, "add", "-A", "--", r.file)
        unresolved.append(r.file)
    return sorted(set(unresolved))


def step(store: Store, task: str, message: str) -> None:
    """Pipeline stage events the frontend console labels: fetch_upstream, merge, detect_conflicts,
    apply, open_pr."""
    store.event("sync", "step", task=task, message=message)


def alert(store: Store, kind: str, summary: str, url: str | None = None) -> None:
    store.event("sync", "alert", level=kind, message=summary, url=url)


# The author used only when neither FORKWARD_GIT_NAME/EMAIL nor the GitHub App set an identity.
FALLBACK_BOT = ("forkward-bot", "forkward-bot@noreply.invalid")


def commit_identity() -> list[str]:
    """git -c options for the author of Forkward commits: FORKWARD_GIT_NAME and _EMAIL when set,
    else the GitHub App's bot user (forkward-bot[bot]), else a fixed fallback identity."""
    name, email = os.environ.get("FORKWARD_GIT_NAME"), os.environ.get("FORKWARD_GIT_EMAIL")
    if not (name and email) and github_app.configured():
        try:
            name, email = github_app.bot_identity() or (name, email)
        except (github_app.GitHubAppError, httpx.HTTPError):
            pass
    name, email = name or FALLBACK_BOT[0], email or FALLBACK_BOT[1]
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def repo_token(repo: str) -> str | None:
    """The credential for pushing to and opening pull requests on owner/name: a short-lived
    installation token scoped to that repository when the GitHub App is configured, else
    GITHUB_TOKEN, else None (the local git and gh login)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return os.environ.get("GITHUB_TOKEN") or None
    return github_app.token_for(repo)


def github_env(token: str | None = None) -> dict[str, str] | None:
    """With a token (an installation token, or GITHUB_TOKEN by default), pushes and `gh` act as
    the bot instead of the local git credential helper and `gh auth login`. The header goes
    through GIT_CONFIG_* so the token never appears in argv or in error messages."""
    tok = token or os.environ.get("GITHUB_TOKEN")
    if not tok:
        return None
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    # Appended after any GIT_CONFIG_* entries already in the environment, never over them.
    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    return {
        **os.environ,
        "GH_TOKEN": tok,
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": f"http.{GIT_URL}/.extraheader",
        f"GIT_CONFIG_VALUE_{n}": f"AUTHORIZATION: basic {basic}",
    }


def worktree(path: Path, sha: str, branch: str) -> Path:
    """A throwaway checkout at an exact commit, so a sync never touches the user's working tree
    and never builds on a stale branch."""
    wt = CACHE / "worktrees" / branch.replace("/", "__")
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(path, "worktree", "add", "--quiet", "-b", branch, str(wt), sha)
    return wt


def drop_worktree(path: Path, wt: Path, branch: str, keep_branch: bool) -> None:
    git(path, "worktree", "remove", "--force", str(wt), check=False)
    git(path, "worktree", "prune", check=False)
    if not keep_branch:
        git(path, "branch", "-D", branch, check=False)


def lock_target(repo: str) -> Path:
    """The repository directory `checkout` will use, without touching it."""
    if not sandbox.hosted() and Path(repo).expanduser().is_dir():
        return Path(repo).expanduser().resolve()
    return CACHE / _slug(validate_repo(repo))


PRIVATE_PREFIX = "private-"


def run_id(kind: str, private: bool | None = None) -> str:
    """Runs that name a customer's repository (every run on the hosted service unless the caller
    says otherwise) are private: the public board never lists them, and their unguessable id is
    needed to read one."""
    private = sandbox.hosted() if private is None else private
    stamp = time.strftime("%Y%m%dT%H%M%S")
    if private:
        return f"{PRIVATE_PREFIX}{kind}-{stamp}-{uuid.uuid4().hex}"
    return f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class Prepared:
    path: Path
    upstream_sha: str
    base_sha: str
    base_name: str
    branch: str
    wt: Path
    clean: bool


def prepare(
    repo: str,
    base: str | None,
    upstream: str,
    kind: str,
    lock: Path,
    token: str | None = None,
    branch: str | None = None,
) -> Prepared:
    """Blocking git work, run in a worker thread: fetch, pick the fresh base, add a worktree at
    it and merge. The worktree is removed again if anything fails. A given `branch` is the same on
    every retry of a job, so what a crashed attempt left in the cache is dropped first."""
    hosted = sandbox.hosted()
    if hosted:
        # Refused before any clone or fetch, so an oversized repository costs no bandwidth or disk.
        check_remote_size(repo, token)
        if "@" in upstream:
            check_remote_size(upstream.split("@", 1)[0])
    with repo_lock(lock):
        path = checkout(repo, token=token)
        if hosted:
            check_size(path)
        up = fetch_upstream(path, upstream)
        if hosted:
            check_size(path)
            prune_cache(keep=path)
        base_sha, base_name = resolve_base(path, base)
        if branch:
            validate_ref(branch)
            stale = CACHE / "worktrees" / branch.replace("/", "__")
            drop_worktree(path, stale, branch, keep_branch=False)
        else:
            branch = (
                f"forkward/sync-{up[:8]}-{uuid.uuid4().hex[:4]}"
                if kind == "sync"
                else f"forkward/preview-{uuid.uuid4().hex[:8]}"
            )
        wt = worktree(path, base_sha, branch)
        try:
            clean = _merge(wt, up)
        except BaseException:
            drop_worktree(path, wt, branch, keep_branch=False)
            raise
    return Prepared(path, up, base_sha, base_name, branch, wt, clean)


MAX_REPO_MB = float(os.environ.get("FORKWARD_MAX_REPO_MB", "2048"))


CACHE_MAX_MB = float(os.environ.get("FORKWARD_CACHE_MAX_MB", "20480"))


def check_remote_size(repo: str, token: str | None = None) -> None:
    """The hosted service refuses an owner/name whose size on GitHub is over FORKWARD_MAX_REPO_MB,
    before cloning or fetching it. A size it cannot look up is refused too."""
    tok = token or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        r = httpx.get(f"{github_app.API}/repos/{validate_repo(repo)}", headers=headers, timeout=30)
        r.raise_for_status()
        kb = int(r.json()["size"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        raise RuntimeError(
            f"could not look up the size of {repo} on GitHub ({e}); the hosted service only "
            "fetches repositories whose size it can check"
        ) from None
    if kb > MAX_REPO_MB * 1024:
        raise TooLarge(
            f"{repo} is {kb / 1024:.0f} MB on GitHub, over the hosted limit of {MAX_REPO_MB:.0f} MB"
        )


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def prune_cache(keep: Path) -> list[str]:
    """Keep the clone cache under FORKWARD_CACHE_MAX_MB by deleting the least recently used clones.
    A clone that is locked, or that still has a worktree (a preview or sync is resolving in it),
    is never deleted. Returns the names removed."""
    clones = [
        p
        for p in CACHE.iterdir()
        if p.is_dir() and p.name not in ("locks", "worktrees") and (p / ".git").exists()
    ]
    os.utime(keep)
    sizes = {p: _dir_bytes(p) for p in clones}
    total, budget, removed = sum(sizes.values()), CACHE_MAX_MB * 1024 * 1024, []
    for p in sorted(clones, key=lambda p: p.stat().st_mtime):
        if total <= budget:
            break
        if p.resolve() == keep.resolve() or any((p / ".git" / "worktrees").glob("*")):
            continue
        try:
            with repo_lock(p, wait=0):
                shutil.rmtree(p)
        except (RuntimeError, OSError):
            continue
        total -= sizes[p]
        removed.append(p.name)
    return removed


def check_size(path: Path) -> None:
    """The hosted service refuses repositories whose object store is over FORKWARD_MAX_REPO_MB."""
    out = git(path, "count-objects", "-v", check=False)
    kb = sum(
        int(v)
        for k, _, v in (line.partition(": ") for line in out.splitlines())
        if k in ("size", "size-pack") and v.strip().isdigit()
    )
    if kb > MAX_REPO_MB * 1024:
        raise TooLarge(
            f"repository is {kb / 1024:.0f} MB, over the hosted limit of {MAX_REPO_MB:.0f} MB"
        )


def _drop(lock: Path, prep: Prepared, keep_branch: bool) -> None:
    with repo_lock(lock):
        drop_worktree(prep.path, prep.wt, prep.branch, keep_branch=keep_branch)


async def preview(
    repo: str,
    base: str | None,
    upstream: str,
    max_hunks: int | None = None,
    on_hunks=None,
    run: str | None = None,
    observer=None,
    token: str | None = None,
) -> SyncResult:
    """Resolve the conflicts of merging `upstream` into `base` without committing anything.
    Blocking git and database work runs in worker threads, so a server's event loop stays free."""
    validate_upstream(upstream)
    lock = lock_target(repo)
    llm.load_env()
    store = await asyncio.to_thread(Store, run or run_id("preview"), 200, observer)
    prep = None
    try:
        step(store, "fetch_upstream", f"Fetching {upstream} for {repo}")
        prep = await asyncio.to_thread(prepare, repo, base, upstream, "preview", lock, token)
        up = prep.upstream_sha
        step(store, "merge", f"Merged {up[:12]} into {prep.base_name} in a throwaway worktree")
        res = SyncResult(repo, prep.branch, upstream, prep.clean, upstream_sha=up)
        step(
            store,
            "detect_conflicts",
            "Merge is clean" if res.clean_merge else "Merge has conflicts; resolving each hunk",
        )
        _, res.hunks, res.strategy = await resolve(prep.wt, up, store, repo, max_hunks, on_hunks)
        review = [h for h in res.hunks if h.gate != "auto_apply"]
        step(
            store,
            "apply",
            f"{res.auto_applied} hunks would auto-apply, {len(review)} need a person",
        )
        for h in review:
            store.event("gate", "needs_review", hunk=h.hunk_id, source=h.reason)
        alert(
            store,
            "needs_review" if review else "scheduled_sync",
            f"preview of {repo}: {len(res.hunks)} hunks, {res.auto_applied} auto-applied, {len(review)} need a person",
        )
    finally:
        if prep is not None:
            await asyncio.to_thread(_drop, lock, prep, False)
        await asyncio.to_thread(store.close)
        await llm.close()
    return res


def _commit_merge(wt: Path, hunks: list, results: list[HunkResult], message: str, lock: Path):
    with repo_lock(lock):
        unresolved = apply(wt, hunks, results)
        if unresolved:
            # The branch keeps the markers so a reviewer sees exactly which regions are
            # undecided; the proposals are in the pull request body, which stays a draft.
            message += (
                f"\n\nConflict markers remain in {len(unresolved)} file(s) for a person to "
                "resolve:\n" + "\n".join(f"- {f}" for f in unresolved)
            )
        git(wt, *commit_identity(), "commit", "--quiet", "--no-verify", "-am", message)
    return unresolved


def _commit(wt: Path, message: str, lock: Path) -> None:
    with repo_lock(lock):
        git(wt, *commit_identity(), "commit", "--quiet", "--no-verify", "-am", message)


def _push(
    wt: Path, branch: str, lock: Path, token: str | None = None, app_push: bool = False
) -> None:
    with repo_lock(lock):
        safe_push(wt, branch, token, app_push)


async def sync(
    repo: str,
    upstream: str,
    base_branch: str | None = None,
    build_cmd: str | None = None,
    test_cmd: str | None = None,
    open_pr: bool = False,
    app_push: bool = False,
    max_hunks: int | None = None,
    on_hunks=None,
    observer=None,
    branch: str | None = None,
) -> SyncResult:
    """Merge upstream into a new forkward/* branch (`branch`, or a fresh name), resolve hunks
    through the gate, optionally repair the build, and optionally push and open a pull request.

    `app_push` means the caller (the durable sync workflow) has checked that `repo` belongs to
    an installation of the tenant it runs for, so the push uses that installation's
    repository-scoped token. Only then may the hosted service push; it still never builds."""
    if open_pr and sandbox.hosted() and not (app_push and github_app.configured()):
        raise PushRefused("the hosted service only pushes through the GitHub App")
    # Refuse a disallowed build or test command, or any build on the hosted service, before
    # touching the repository.
    for cmd in (build_cmd, test_cmd):
        if cmd:
            sandbox.check_command(cmd)
    validate_upstream(upstream)
    lock = lock_target(repo)
    llm.load_env()
    store = await asyncio.to_thread(Store, run_id("sync"), 200, observer)
    prep = res = None
    try:
        step(store, "fetch_upstream", f"Fetching {upstream} for {repo}")
        early = await asyncio.to_thread(repo_token, repo) if app_push else None
        prep = await asyncio.to_thread(
            prepare, repo, base_branch, upstream, "sync", lock, early, branch
        )
        up, wt, branch, base_branch = prep.upstream_sha, prep.wt, prep.branch, prep.base_name
        step(store, "merge", f"Merged {up[:12]} into {base_branch} on {branch}")
        res = SyncResult(repo, branch, upstream, prep.clean, upstream_sha=up)
        step(
            store,
            "detect_conflicts",
            "Merge is clean" if res.clean_merge else "Merge has conflicts; resolving each hunk",
        )
        hunks, res.hunks, res.strategy = await resolve(wt, up, store, repo, max_hunks, on_hunks)
        step(store, "apply", f"Applying {res.auto_applied} of {len(res.hunks)} resolutions")
        unresolved = await asyncio.to_thread(
            _commit_merge, wt, hunks, res.hunks, f"Merge upstream {upstream} ({up[:12]})", lock
        )
        if unresolved:
            alert(
                store,
                "needs_review",
                f"{repo}: {len(unresolved)} "
                f"{'file keeps' if len(unresolved) == 1 else 'files keep'} conflict markers "
                "for you to review",
            )
        if unresolved and build_cmd:
            # A tree with conflict markers cannot be built, and repairing around them would let a
            # model edit code nobody has reviewed yet.
            res.verification = (
                f"pending human review: {len(unresolved)} file(s) still hold conflict markers, "
                "so the build was not run"
            )
        elif build_cmd:
            rep = await repair.repair(wt, build_cmd, test_cmd, [(wt, f"HEAD^1..{up}")], store=store)
            res.repair = asdict(rep)
            if rep.fixes:
                await asyncio.to_thread(
                    _commit,
                    wt,
                    "Repair build after upstream sync\n\n"
                    + "\n".join(f"{f.file}:{f.line} {f.code}: {f.evidence}" for f in rep.fixes),
                    lock,
                )
        if open_pr:
            step(
                store, "open_pr", f"Pushing {branch} and opening a pull request into {base_branch}"
            )
            token = await asyncio.to_thread(repo_token, repo)
            await asyncio.to_thread(_push, wt, branch, lock, token, app_push)
            res.pr_url = await asyncio.to_thread(
                open_pull_request, wt, repo, branch, base_branch, res, token
            )
        alert(
            store,
            "scheduled_sync",
            f"{repo}: merged {upstream}, {res.auto_applied} hunks auto-applied, "
            f"{res.needs_review} need review"
            + (
                ", build not verified until review"
                if res.verification
                else f", build {'fixed' if res.repair and res.repair['build_ok'] else 'broken'}"
                if build_cmd
                else ""
            ),
            res.pr_url,
        )
        return res
    finally:
        if prep is not None:
            await asyncio.to_thread(_drop, lock, prep, True)
        if res is not None:
            store.add("evals", {"kind": "sync", **asdict(res)})
        await asyncio.to_thread(store.close)
        await llm.close()


# GitHub rejects pull request bodies over 65,536 characters.
PR_BODY_LIMIT = 60_000
PROPOSAL_BUDGET = 40_000


def code(text: str) -> str:
    """An inline code span that holds `text` verbatim, so file names cannot render links or
    mentions."""
    text = " ".join(str(text).split())
    ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def fenced(text: str) -> list[str]:
    """A fenced block longer than any backtick run inside `text`."""
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return [fence, *text.split("\n"), fence]


def pr_body(res: SyncResult) -> str:
    review = [h for h in res.hunks if h.gate != "auto_apply"]
    lines = [
        f"Merges {code(res.upstream)} into this fork. Prepared by Forkward.",
        "",
        (
            "Only hunks marked `auto_apply` below passed a safety gate whose error bound was "
            "certified on held-out merges, and only those were written into this branch. Every "
            "other hunk still has its conflict markers in the branch. Its proposed resolution is "
            "below for a person to check and apply, and this pull request stays a draft until "
            "they do."
            if review
            else "Every hunk below passed a safety gate whose error bound was certified on "
            "held-out merges."
        ),
        "",
        f"**Auto-applied hunks:** {res.auto_applied}. **Hunks that need a person:** {res.needs_review}.",
    ]
    if res.verification:
        lines += ["", f"**Build verification:** {res.verification}."]
    if review:
        lines += ["", "### Needs human review"]
        lines += [f"- [ ] {code(h.file)} {code(h.hunk_id)}: {h.reason}" for h in review]
        lines += [
            "",
            "### Proposed resolutions",
            "",
            (
                "Each block replaces the lines from `<<<<<<<` to `>>>>>>>` of that hunk. None of "
                "them has been applied."
            ),
        ]
        used = 0
        for h in review:
            lines += ["", f"#### {code(h.file)} {code(h.hunk_id)} ({h.gate})", ""]
            if h.gate == "blocked":
                lines.append(f"No proposal: {h.reason}.")
                continue
            if used + len(h.resolution) > PROPOSAL_BUDGET:
                lines.append(
                    "Proposal omitted to keep this description under GitHub's size limit. It is "
                    "in the sync record."
                )
                continue
            used += len(h.resolution)
            lines.append(
                f"Proposed resolution, confidence {h.confidence:.2f}"
                + (" (empty, so the whole block is deleted):" if not h.resolution else ":")
            )
            lines += ["", *fenced(h.resolution)]
    if res.hunks:
        lines += [
            "",
            "### Audit record",
            "",
            "| File | Hunk | Decision | Confidence | Why |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {code(h.file)} | {code(h.hunk_id)} | {h.gate} | {h.confidence:.2f} | {h.reason} |"
            for h in res.hunks
        ]
    if res.repair:
        r = res.repair
        lines += [
            "",
            "### Build repair",
            f"Build after repair: {'passes' if r['build_ok'] else 'FAILS'}.",
        ]
        lines += [
            f"- {code(str(f['file']) + ':' + str(f['line']))} {f['code']}: {code(f['old'])} to "
            f"{code(f['new'])}. {' '.join(str(f['evidence']).split())}"
            for f in r["fixes"]
        ]
        if r.get("test"):
            t = r["test"]
            lines.append(
                f"- `{t['cmd']}`: {t['passed']} passed, {t['failed']} failed, {t['ignored']} ignored."
            )
    from forkward import history

    h = history.stats(res.repo)
    if h["auto_applied"]:
        lines += [
            "",
            (
                f"This repository's merge history: {h['auto_applied']} auto-applied hunks merged, "
                f"{h['auto_applied_changed_by_reviewer']} changed by a reviewer; 95% upper bound on "
                f"the auto-apply error rate {100 * h['certified_bound']:.1f}%."
            ),
        ]
    lines += [
        "",
        f"Strategy: {res.strategy or 'none (no conflict hunks)'}",
        "",
        "Assisted-by: Forkward",
    ]
    body = "\n".join(lines)
    if len(body) > PR_BODY_LIMIT:
        body = (
            body[:PR_BODY_LIMIT]
            + "\n\n(Truncated to GitHub's size limit. The full record is in the sync log.)"
        )
    return body


class PushRefused(RuntimeError):
    pass


def default_branch(path: Path) -> str:
    ref = git(path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).strip()
    if not ref:
        git(path, "remote", "set-head", "origin", "--auto", check=False)
        ref = git(path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).strip()
    return ref.rsplit("/", 1)[-1] if ref else "main"


def safe_push(path: Path, branch: str, token: str | None = None, app_push: bool = False) -> None:
    """Forkward only ever pushes a brand-new forkward/* branch. It never pushes to the default
    branch, never overwrites an existing remote branch, and never force-pushes; changes reach
    main only when a person merges the pull request."""
    if sandbox.hosted() and not (app_push and token and github_app.configured()):
        raise PushRefused("the hosted service only pushes through the GitHub App")
    if not branch.startswith("forkward/"):
        raise PushRefused(f"refusing to push {branch!r}: only forkward/* branches are pushed")
    if branch == default_branch(path):
        raise PushRefused(f"refusing to push to the default branch {branch!r}")
    env = github_env(token)
    if git(path, "ls-remote", "--heads", "origin", branch, check=False, env=env).strip():
        raise PushRefused(f"refusing to overwrite existing remote branch {branch!r}")
    git(path, "push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}", env=env)


def open_pull_request(
    path: Path, repo: str, branch: str, base: str | None, res: SyncResult, token: str | None = None
) -> str:
    """Open the sync pull request: as a draft while any hunk needs a person. With the GitHub App
    configured it goes through the REST API with the installation token and also posts the
    Forkward gate check run on the merge commit, with one annotation per hunk that needs review;
    otherwise it uses the gh CLI."""
    if sandbox.hosted() and not (token and github_app.configured()):
        raise PushRefused("the hosted service only opens pull requests through the GitHub App")
    slug = repo if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else None
    if slug is None:
        remote = git(path, "remote", "get-url", "origin").strip()
        m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", remote)
        if not m:
            raise RuntimeError(f"origin is not a GitHub repository: {remote}")
        slug = m.group(1)
    if token and github_app.configured():
        target = base.removeprefix("origin/") if base else default_branch(path)
        url = github_app.create_pull(
            token,
            slug,
            branch,
            target,
            f"Sync with upstream {res.upstream}",
            pr_body(res),
            draft=bool(res.needs_review),
        )
        sha = git(path, "rev-parse", "HEAD").strip()
        try:
            github_app.post_check_run(token, slug, sha, res.hunks, pr_body(res))
        except github_app.GitHubAppError as e:
            print(f"[sync] check run not posted: {e}", file=sys.stderr)
        return url
    args = [
        "gh",
        "pr",
        "create",
        "--repo",
        slug,
        "--head",
        branch,
        "--title",
        f"Sync with upstream {res.upstream}",
        "--body-file",
        "-",
    ]
    if res.needs_review:
        # Hunks the gate did not certify must be checked by a person before this can merge.
        args.append("--draft")
    if base:
        # A sync can start from a remote-tracking ref; the pull request targets the branch itself.
        args += ["--base", base.removeprefix("origin/")]
    p = subprocess.run(
        args,
        cwd=path,
        input=pr_body(res),
        capture_output=True,
        text=True,
        check=False,
        env=github_env(token),
        timeout=120,
    )
    if p.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {p.stderr.strip()[:500]}")
    return p.stdout.strip().splitlines()[-1]


def updates(since: float | None = None, limit: int = 20) -> list[dict]:
    """Alerts across every run: promotions, finished runs, syncs, hunks that need a person."""
    from forkward.store import read_jsonl

    rows = []
    if RUNS.exists():
        for d in RUNS.iterdir():
            if d.is_dir() and not d.name.startswith("aborted-"):
                rows += [
                    e
                    for e in read_jsonl(d, "events")
                    if e.get("kind") in ("alert", "needs_review")
                    and (since is None or e.get("t", 0) > since)
                ]
    rows.sort(key=lambda e: e.get("t", 0))
    return rows[-limit:]


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward sync")
    p.add_argument("repo")
    p.add_argument("upstream", help="owner/name@ref, or a ref the repo already has")
    p.add_argument("--base", default=None)
    p.add_argument("--build", default=None)
    p.add_argument("--test", default=None)
    p.add_argument("--open-pr", action="store_true")
    a = p.parse_args(argv)
    res = asyncio.run(sync(a.repo, a.upstream, a.base, a.build, a.test, a.open_pr))
    print(
        json.dumps(
            asdict(res) | {"auto_applied": res.auto_applied, "needs_review": res.needs_review},
            indent=1,
        )
    )
    return 0


async def calibrate_current_model() -> dict:
    """Run the frozen strategy on the validation split with the configured model and store the
    Learn-then-Test threshold for it. Never touches the test split."""
    from forkward import score, search
    from forkward.data import load_hunks

    llm.load_env()
    src = FROZEN if FROZEN.exists() else PACKAGED
    if not src.exists():
        raise RuntimeError("no frozen strategy to calibrate")
    d = json.loads(src.read_text())
    s = Strategy(**{k: v for k, v in d["strategy"].items() if k in Strategy.__dataclass_fields__})
    pb = Playbook.from_doc(d["playbook"])
    provider, model = llm.provider()[0], llm.model_for("generator")
    key = score.load_key("validation")
    hunks = [h for h in load_hunks("validation") if h.tag in key]
    store = Store(f"calibrate-{time.strftime('%Y%m%dT%H%M%S')}")
    runner = Runner(store, {pb.version: pb, EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK})
    outs = await runner.run(s, hunks, "validation", -1)
    cal = search.calibrate("validation", outs, s)
    m = search.measure("validation", outs, s, cal["threshold"])
    out = {
        "strategy_id": s.id,
        "provider": provider,
        "model": model,
        "threshold": cal["threshold"],
        "certified_bound": cal["bound"],
        "target_met": cal["target_met"],
        "accuracy": m["accuracy"],
        "coverage": m["coverage"],
        "accepted": m["accepted"],
        "escaped": m["escaped"],
        "n": m["n"],
        "created": time.time(),
    }
    p = calibration_path(s.id, provider, model)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    store.add("evals", {"kind": "model_calibration", **out})
    store.close()
    await llm.close()
    return out


def apply_review(
    repo: str,
    branch: str,
    upstream_sha: str,
    accepted: list[dict],
    unresolved: dict[str, list[int]],
    message: str,
    token: str | None = None,
) -> str:
    """Commit reviewed resolutions onto an open sync branch and push it as a fast-forward.

    `accepted` holds {"hunk_ref": "path#n", "resolution": text} for hunks a person accepted,
    with any edit they made. `unresolved` maps each file to the original numbers (the n in
    path#n) of every hunk still carrying git's markers on the branch, in file order. Markers are
    labelled HEAD and the upstream SHA, so the k-th block in a file is unresolved[file][k].
    Rejected hunks keep their markers. Returns the new commit SHA.

    Only a forkward/* branch is ever updated, never with a force push, and the push fails if the
    branch moved since it was fetched."""
    validate_repo(repo)
    if not branch.startswith("forkward/"):
        raise PushRefused(f"refusing to update {branch!r}: only forkward/* branches")
    lock = lock_target(repo)
    with repo_lock(lock):
        path = checkout(repo, allow_local=False, token=token)
        head = git(path, "rev-parse", f"refs/remotes/origin/{branch}").strip()
        wt = CACHE / "worktrees" / f"review-{uuid.uuid4().hex[:8]}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(path, "worktree", "add", "--quiet", "--detach", str(wt), head)
        try:
            by_file: dict[str, dict[int, str]] = {}
            for a in accepted:
                f, _, n = a["hunk_ref"].rpartition("#")
                by_file.setdefault(f, {})[int(n)] = a["resolution"]
            for f, picks in by_file.items():
                target = (wt / f).resolve()
                if not target.is_relative_to(wt.resolve()) or not target.is_file():
                    raise ValueError(f"{f} is not a file in the sync branch")
                lines = target.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
                blocks = _blocks(lines, "HEAD", upstream_sha)
                remaining = sorted(unresolved.get(f, []))
                if len(blocks) != len(remaining) or not set(picks) <= set(remaining):
                    raise ValueError(
                        f"{f} has {len(blocks)} conflict blocks but the job expected "
                        f"{len(remaining)}; it changed since the sync, so review it on GitHub"
                    )
                for n in sorted(picks, reverse=True):
                    s, e = blocks[remaining.index(n)]
                    text = picks[n]
                    lines[s : e + 1] = text.split("\n") if text else []
                target.write_text("\n".join(lines), encoding="utf-8", errors="surrogateescape")
                git(wt, "add", "--", f)
            git(wt, *commit_identity(), "commit", "--quiet", "--no-verify", "-m", message)
            sha = git(wt, "rev-parse", "HEAD").strip()
            if sandbox.hosted() and not (token and github_app.configured()):
                raise PushRefused("the hosted service only pushes through the GitHub App")
            env = github_env(token)
            # A lease on the fetched head refuses the push if anyone moved the branch meanwhile.
            git(
                wt,
                "push",
                "--quiet",
                f"--force-with-lease=refs/heads/{branch}:{head}",
                "origin",
                f"{sha}:refs/heads/{branch}",
                env=env,
            )
            return sha
        finally:
            git(path, "worktree", "remove", "--force", str(wt), check=False)
