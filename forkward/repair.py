"""Build-break repair for clean syncs that no longer compile (Rust).

Git can report a sync as conflict-free while the build breaks, because upstream changed an API the
fork still uses. This loop builds, reads rustc's structured diagnostics, gathers evidence (rustc's
own help, sibling call sites, upstream commits that touch the symbol), asks the model for exact
line edits, and keeps an edit only if a rebuild clears that error without adding new ones.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from forkward import llm, sandbox
from forkward.store import Store

_SYSTEM = (
    "You repair Rust code after an upstream dependency sync. You change as little as possible: "
    "usually one line. You follow the evidence: the compiler's help text, how the same API is "
    "called elsewhere in the new code, and the upstream commit that changed it."
)

_ASK = """Fix this compile error with the smallest edit.

Answer with JSON only: {{"edits": [{{"line": <line number>, "old": "<exact current line>", \
"new": "<replacement line>"}}], "evidence": "<one sentence naming what justified the edit>"}}

Error:
{rendered}

{file} (lines {lo} to {hi}):
{window}

Evidence:
{evidence}"""


@dataclass
class Diagnostic:
    code: str
    message: str
    file: str
    line: int
    symbol: str
    rendered: str

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.code)


@dataclass
class Fix:
    file: str
    line: int
    old: str
    new: str
    code: str
    evidence: str
    model: str
    tokens: int
    verified: str = ""


@dataclass
class Report:
    repo: str
    build_cmd: str
    rounds: int = 0
    fixes: list[Fix] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    remaining: list[dict] = field(default_factory=list)
    build_ok: bool = False
    test: dict | None = None


# Lines a repair may never introduce: process, network, filesystem and FFI access, unsafe code and
# compile-time file inclusion. Repository text reaches the prompt, so an injected instruction could
# otherwise steer a one-line "fix" that still compiles.
SUSPICIOUS = re.compile(
    r"\bunsafe\b|\bCommand\b|std::process|std::net|std::fs|std::env|\bTcpStream\b|"
    r"\bUdpSocket\b|include(_str|_bytes)?!|\benv!|option_env!|\basm!|global_asm!|"
    r"extern\s+\"|#\s*\[\s*link|#\s*!?\s*\[\s*no_mangle|\bexport_name\b|\blink_section\b"
)
GIT_TIMEOUT = 120


def run(args: list[str], cwd: Path, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Read-only git queries for evidence. Builds and tests go through the sandbox instead."""
    try:
        return sandbox.run_bounded(args, cwd, timeout)
    except TimeoutError as e:
        return subprocess.CompletedProcess(args, 124, "", str(e))


def inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def source_path(path: Path, crate_dir: Path, root: Path | None = None) -> Path:
    """rustc reports paths relative to the workspace root, which may sit above the crate. The search
    never leaves `root` (the worktree)."""
    if path.is_absolute():
        return path
    for d in (crate_dir, *crate_dir.parents):
        if root is not None and not inside(d, root):
            break
        if (d / path).is_file():
            return d / path
    return crate_dir / path


def build(
    repo: Path, argv: list[str], sb: sandbox.Sandbox, timeout: float = 1800
) -> list[Diagnostic]:
    # The JSON flag goes before any `--`, which hands the rest to the test binary.
    cut = argv.index("--") if "--" in argv else len(argv)
    try:
        p = sb.run([*argv[:cut], "--message-format=json", *argv[cut:]], repo, timeout)
    except TimeoutError as e:
        return [Diagnostic("timeout", "build timed out", "", 0, "", str(e))]
    out: list[Diagnostic] = []
    seen = set()
    for raw in p.stdout.splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        m = d.get("message") if d.get("reason") == "compiler-message" else None
        if not m or m.get("level") != "error" or not m.get("spans"):
            continue
        span = next((s for s in m["spans"] if s["is_primary"]), m["spans"][0])
        text = span["text"][0] if span.get("text") else {}
        sym = text.get("text", "")[
            text.get("highlight_start", 1) - 1 : text.get("highlight_end", 1) - 1
        ]
        sym = (re.findall(r"[A-Za-z_]\w*", sym) or [""])[-1]
        root = Path(d.get("manifest_path", repo / "Cargo.toml")).parent
        path = source_path(Path(span["file_name"]), root, repo)
        diag = Diagnostic(
            code=(m.get("code") or {}).get("code") or "error",
            message=m["message"],
            # Errors in dependencies or anywhere outside the worktree are reported, never edited.
            file=str(path) if inside(path, repo) else "",
            line=span["line_start"],
            symbol=sym,
            rendered=m.get("rendered") or m["message"],
        )
        if diag.key not in seen:
            seen.add(diag.key)
            out.append(diag)
    if p.returncode != 0 and not out:
        out.append(Diagnostic("build", "build failed", "", 0, "", p.stderr[-3000:]))
    return out


def evidence(d: Diagnostic, repo: Path, upstream: list[tuple[Path, str]]) -> str:
    parts = []
    if d.symbol:
        names = {d.symbol}
        names.update(re.findall(r"`(\w+)`", d.rendered))
        hits = []
        for name in sorted(names):
            if len(name) < 4:
                continue
            g = run(["git", "grep", "-n", "-w", name, "--", "*.rs"], Path(d.file).parent)
            for line in g.stdout.splitlines():
                if f":{d.line}:" in line and Path(d.file).name in line:
                    continue
                hits.append(line.strip()[:200])
        if hits:
            parts.append("Other uses in this repository:\n" + "\n".join(hits[:12]))
        for repo_path, rng in upstream:
            log = run(
                ["git", "log", "--format=%h %s", f"-G\\b{d.symbol}\\b", rng, "--", "*.rs"],
                repo_path,
            ).stdout.split("\n")[:3]
            log = [x for x in log if x.strip()]
            if not log:
                continue
            sha = log[0].split()[0]
            show = run(["git", "show", sha, "--", "*.rs"], repo_path).stdout.splitlines()
            lines = [
                x
                for x in show
                if x[:1] in "+-" and d.symbol in x and not x.startswith(("+++", "---"))
            ]
            parts.append(
                f"Upstream commits in {rng} touching `{d.symbol}`:\n"
                + "\n".join(log)
                + f"\nLines changed by {sha} that mention it:\n"
                + "\n".join(lines[:16])
            )
    return "\n\n".join(parts) or "(none found)"


def _window(path: Path, line: int, radius: int = 12) -> tuple[int, int, str]:
    lines = path.read_text().splitlines()
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return lo, hi, "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(lo, hi + 1))


def suspicious(old: str, new: str) -> str | None:
    """The risky construct an edit would add, if any."""
    added = {m.group(0) for m in SUSPICIOUS.finditer(new)} - {
        m.group(0) for m in SUSPICIOUS.finditer(old)
    }
    return min(added) if added else None


def _apply(path: Path, edits: list[dict], root: Path) -> list[tuple[int, str, str]]:
    if not inside(path, root):
        raise sandbox.CommandRefused(f"refusing to edit {path}: it is outside the worktree")
    lines = path.read_text().split("\n")
    done = []
    for e in edits:
        try:
            n = int(e.get("line", 0))
        except (TypeError, ValueError):
            continue
        old, new = str(e.get("old", "")), str(e.get("new", ""))
        if "\n" in new or suspicious(old, new):
            continue
        if 1 <= n <= len(lines) and lines[n - 1].strip() == old.strip() and old.strip():
            indent = lines[n - 1][: len(lines[n - 1]) - len(lines[n - 1].lstrip())]
            lines[n - 1] = indent + new.strip()
            done.append((n, old, lines[n - 1]))
    if done:
        path.write_text("\n".join(lines))
    return done


async def repair(
    repo: Path,
    build_cmd: str = "cargo build",
    test_cmd: str | None = None,
    upstream: list[tuple[Path, str]] | None = None,
    max_rounds: int = 4,
    store: Store | None = None,
    max_builds: int = 12,
    max_seconds: float = 3600,
    build_timeout: float = 1800,
    test_timeout: float = 3600,
) -> Report:
    # Both commands are checked before anything runs: hosted mode refuses all builds, and
    # elsewhere only plain `cargo build|check|test` forms are accepted.
    build_argv = sandbox.check_command(build_cmd)
    test_argv = sandbox.check_command(test_cmd) if test_cmd else None
    llm.load_env()
    store = store or Store()
    upstream = upstream or []
    rep = Report(str(repo), build_cmd)
    repo = repo.resolve()
    deadline = time.monotonic() + max_seconds
    builds = 0
    with sandbox.Sandbox(repo) as sb:

        def rebuild() -> list[Diagnostic]:
            nonlocal builds
            builds += 1
            left = deadline - time.monotonic()
            if left <= 0:
                return [Diagnostic("timeout", "repair time budget used up", "", 0, "", "")]
            return build(repo, build_argv, sb, timeout=min(build_timeout, left))

        diags = await asyncio.to_thread(rebuild)
        store.event(
            "builder", "done", task="build", decision=f"{len(diags)} errors", repo=str(repo)
        )
        while diags and rep.rounds < max_rounds and builds < max_builds:
            rep.rounds += 1
            progress = False
            for d in diags:
                if not d.file or builds >= max_builds or time.monotonic() >= deadline:
                    continue
                path = Path(d.file)
                lo, hi, win = _window(path, d.line)
                prompt = _ASK.format(
                    rendered=d.rendered[:4000],
                    file=path.name,
                    lo=lo,
                    hi=hi,
                    window=win,
                    evidence=evidence(d, repo, upstream)[:6000],
                )
                store.event(
                    "repairer", "start", task=f"repair {d.code}", hunk=f"{path.name}:{d.line}"
                )
                c = await llm.complete(
                    [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=1500,
                )
                store.add(
                    "attempts",
                    {
                        "kind": "repair",
                        "prompt": prompt,
                        "response": c.text,
                        "model": c.model,
                        "error": c.error,
                        "file": d.file,
                        "line": d.line,
                    },
                )
                m = re.search(r"\{.*\}", c.text, re.DOTALL)
                try:
                    plan = json.loads(m.group(0)) if m else {}
                except json.JSONDecodeError:
                    plan = {}
                before = path.read_text()
                edits = plan.get("edits") if isinstance(plan, dict) else None
                try:
                    applied = _apply(path, edits if isinstance(edits, list) else [], repo)
                except sandbox.CommandRefused:
                    applied = []
                if not applied:
                    rep.rejected.append(
                        {
                            "error": d.message,
                            "at": f"{path.name}:{d.line}",
                            "reason": "edit did not match the file or was not allowed",
                            "response": c.text[:500],
                        }
                    )
                    store.event(
                        "repairer",
                        "error",
                        task=f"repair {d.code}",
                        hunk=f"{path.name}:{d.line}",
                        decision="no applicable edit",
                        tokens=c.prompt_tokens + c.completion_tokens,
                        latency_s=round(c.latency_s, 2),
                    )
                    continue
                after = await asyncio.to_thread(rebuild)
                fixed = d.key not in {x.key for x in after}
                edited = {(d.file, n) for n, _, _ in applied}
                worse = any((x.file, x.line) in edited for x in after) or any(
                    x.code == "timeout" for x in after
                )
                if fixed and not worse:
                    for n, old, new in applied:
                        rep.fixes.append(
                            Fix(
                                os.path.relpath(path, repo),
                                n,
                                old.strip(),
                                new.strip(),
                                d.code,
                                str(plan.get("evidence", ""))[:300],
                                c.model,
                                c.prompt_tokens + c.completion_tokens,
                                "build",
                            )
                        )
                    store.event(
                        "repairer",
                        "done",
                        task=f"repair {d.code}",
                        hunk=f"{path.name}:{d.line}",
                        decision="fixed, rebuild clean of it",
                        tokens=c.prompt_tokens + c.completion_tokens,
                        latency_s=round(c.latency_s, 2),
                    )
                    diags = after
                    progress = True
                    break
                path.write_text(before)
                rep.rejected.append(
                    {
                        "error": d.message,
                        "at": f"{path.name}:{d.line}",
                        "reason": "rebuild did not clear it"
                        if not fixed
                        else "rebuild added errors",
                        "edits": applied,
                    }
                )
                store.event(
                    "repairer",
                    "error",
                    task=f"repair {d.code}",
                    hunk=f"{path.name}:{d.line}",
                    decision="reverted by build gate",
                    tokens=c.prompt_tokens + c.completion_tokens,
                )
            if not progress:
                break
        rep.remaining = [asdict(d) | {"rendered": d.rendered[:400]} for d in diags]
        rep.build_ok = not diags
        if rep.build_ok and test_argv:
            try:
                p = await asyncio.to_thread(
                    sb.run,
                    test_argv,
                    repo,
                    max(1.0, min(test_timeout, deadline - time.monotonic())),
                )
                out, ok = p.stdout, p.returncode == 0
            except TimeoutError as e:
                out, ok = str(e), False
            m = re.findall(r"test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored", out)
            rep.test = {
                "cmd": test_cmd,
                "ok": ok,
                "passed": sum(int(x[1]) for x in m),
                "failed": sum(int(x[2]) for x in m),
                "ignored": sum(int(x[3]) for x in m),
            }
            store.event(
                "tester",
                "done",
                task="test",
                decision=f"{rep.test['passed']} passed, {rep.test['failed']} failed",
            )
    store.add("evals", {"kind": "repair", **asdict(rep)})
    store.flush()
    await llm.close()
    return rep


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward repair")
    p.add_argument("repo", type=Path)
    p.add_argument("--build", default="cargo build")
    p.add_argument("--test", default=None)
    p.add_argument("--upstream", action="append", default=[], help="PATH:RANGE for evidence")
    p.add_argument("--run-id", default=None)
    a = p.parse_args(argv)
    ups = [(Path(x.split(":", 1)[0]).resolve(), x.split(":", 1)[1]) for x in a.upstream]
    store = Store(a.run_id)
    rep = asyncio.run(repair(a.repo.resolve(), a.build, a.test, ups, store=store))
    store.close()
    print(json.dumps(asdict(rep), indent=1))
    return 0 if rep.build_ok and (rep.test is None or rep.test["ok"]) else 1
