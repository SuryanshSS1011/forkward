"""Run untrusted build and test commands without secrets, with resource limits and a deadline.

A sync builds code that came from upstream and from every crate it pulls in. Build scripts, proc
macros and tests run arbitrary code, so by default (FORKWARD_SANDBOX=docker) they run in a container
with no network, a read-only root and only the worktree mounted writable, with a scrubbed
environment: an allowlisted PATH and locale, HOME and CARGO_HOME in a throwaway directory, and none
of GITHUB_TOKEN, DATABASE_URL or any API key or secret. Dependencies are first fetched in a separate
container that has network access but still no secrets. FORKWARD_SANDBOX=gvisor does the same under
gVisor's runsc runtime.

FORKWARD_SANDBOX=env is an explicit opt-out that runs builds on this machine with only the scrubbed
environment and resource limits. Build code can then still read any file this user can (such as
.env or ~/.config/gh) and use the network, so use it only for repositories you trust.

The hosted service never runs a build or test command at all (`hosted()`).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

# Only these variables reach a build. Everything else, including every credential, is dropped.
PASS_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "RUSTUP_TOOLCHAIN")
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
SECRET_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|API_KEY|_KEY$|MONGODB|DATABASE|DSN|CREDENTIAL|AUTH)", re.IGNORECASE
)

# Build commands come from tool arguments and schedules, so only these cargo forms are run.
SUBCOMMANDS = {"build", "check", "test"}
SAFE_ARG = re.compile(r"[A-Za-z0-9_.,:=/@-]+")
BANNED_FLAGS = ("--config", "-Z", "--target-dir", "-C", "--artifact-dir", "--out-dir")


class CommandRefused(ValueError):
    pass


def hosted() -> bool:
    """True on the shared hosted service, where untrusted code must never run next to the
    service's credentials. Set FORKWARD_HOSTED=1 in every hosted unit; the HTTP transport of the
    MCP server also counts."""
    return os.environ.get("FORKWARD_HOSTED", "").lower() in ("1", "true", "yes") or (
        os.environ.get("FORKWARD_TRANSPORT") == "http"
    )


def refuse_if_hosted() -> None:
    if hosted():
        raise CommandRefused(
            "the hosted service never runs build or test commands; run the sync in your own "
            "environment or CI to verify the build"
        )


def check_command(cmd: str) -> list[str]:
    """Only `cargo build|check|test` with plain flags. No toolchain overrides, no `--config` (which
    can set a runner program), no paths outside the worktree."""
    refuse_if_hosted()
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        raise CommandRefused(f"cannot parse command {cmd!r}: {e}") from None
    if len(argv) < 2 or argv[0] != "cargo" or argv[1] not in SUBCOMMANDS:
        raise CommandRefused(
            f"refusing to run {cmd!r}: build and test commands must be `cargo build`, "
            "`cargo check` or `cargo test` with flags"
        )
    for a in argv[2:]:
        if not SAFE_ARG.fullmatch(a) and a != "--":
            raise CommandRefused(f"refusing to run {cmd!r}: argument {a!r} is not allowed")
        if a.split("=", 1)[0] in BANNED_FLAGS or a.startswith(("-Z", "+")):
            raise CommandRefused(f"refusing to run {cmd!r}: flag {a!r} is not allowed")
        if a.startswith("/") or ".." in a.split("/"):
            raise CommandRefused(f"refusing to run {cmd!r}: paths must stay inside the repository")
    return argv


def scrubbed_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in PASS_ENV if os.environ.get(k)}
    env.setdefault("PATH", DEFAULT_PATH)
    env["HOME"] = str(home)
    env["CARGO_HOME"] = str(home / "cargo")
    env["TMPDIR"] = str(home / "tmp")
    env["CARGO_TERM_COLOR"] = "never"
    # rustup's toolchains are read from the real install; its proxies need to find them.
    rustup = os.environ.get("RUSTUP_HOME") or str(Path.home() / ".rustup")
    if Path(rustup).is_dir():
        env["RUSTUP_HOME"] = rustup
    cargo_bin = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo") / "bin"
    if cargo_bin.is_dir() and str(cargo_bin) not in env["PATH"].split(os.pathsep):
        env["PATH"] = f"{env['PATH']}{os.pathsep}{cargo_bin}"
    # Belt and braces: nothing that looks like a credential survives, whatever PASS_ENV says.
    return {k: v for k, v in env.items() if not SECRET_RE.search(k)}


def limited(args: list[str]) -> list[str]:
    """Wrap `args` so it runs under memory, file size and open-file caps with no core dumps. A
    shell `ulimit` prefix is used instead of preexec_fn, which is unsafe when the caller has
    threads. A cap the host's hard limit forbids is skipped rather than failing the build."""
    mem_kb = int(os.environ.get("FORKWARD_SANDBOX_MEMORY_MB", "16384")) << 10
    file_blocks = int(os.environ.get("FORKWARD_SANDBOX_FILE_MB", "4096")) << 11
    caps = ["ulimit -S -c 0", f"ulimit -S -f {file_blocks}", "ulimit -S -n 4096"]
    if mem_kb > 0:
        caps.append(f"ulimit -S -v {mem_kb}")
    script = "; ".join(f"{c} 2>/dev/null" for c in caps) + '; exec "$@"'
    return ["/bin/sh", "-c", script, "sh", *args]


def run_bounded(
    args: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    limits: bool = False,
) -> subprocess.CompletedProcess:
    """subprocess.run with a deadline that kills the whole process group, so rustc and build
    script grandchildren do not outlive it. Raises TimeoutError."""
    p = subprocess.Popen(
        limited(args) if limits else args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise TimeoutError(
            f"{shlex.join(args[:4])} did not finish within {timeout:.0f} s"
        ) from None
    return subprocess.CompletedProcess(args, p.returncode, out, err)


MODES = ("docker", "gvisor", "env")


def mode() -> str:
    """docker unless FORKWARD_SANDBOX names another mode. An unknown value is refused rather than
    guessed, since the fallback would be the unisolated one."""
    m = (os.environ.get("FORKWARD_SANDBOX") or "docker").lower()
    if m not in MODES:
        raise CommandRefused(f"FORKWARD_SANDBOX={m!r} is not one of {', '.join(MODES)}")
    return m


def docker_argv(
    argv: list[str], worktree: Path, cwd: Path, cargo_home: Path, image: str | None = None
) -> list[str]:
    """No network, no capabilities, a read-only root, and only the worktree and the throwaway
    cargo home writable."""
    mem = int(os.environ.get("FORKWARD_SANDBOX_MEMORY_MB", "16384"))
    wt = str(worktree.resolve())
    # FORKWARD_SANDBOX=gvisor runs the same container under gVisor's runsc, which puts a
    # user-space kernel between the build and the host kernel.
    runtime = ["--runtime=runsc"] if mode() == "gvisor" else []
    return [
        "docker",
        "run",
        *runtime,
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=1024",
        f"--memory={mem}m",
        "--tmpfs=/tmp:rw,exec,size=2g",
        f"--user={os.getuid()}:{os.getgid()}",
        "-v",
        f"{wt}:{wt}:rw",
        "-v",
        f"{cargo_home.resolve()}:/cargo:rw",
        "-e",
        "HOME=/tmp",
        "-e",
        "CARGO_HOME=/cargo",
        "-e",
        "CARGO_NET_OFFLINE=true",
        "-e",
        "CARGO_TERM_COLOR=never",
        "-w",
        str(cwd.resolve()),
        image or os.environ.get("FORKWARD_SANDBOX_IMAGE", "rust:1-slim"),
        *argv,
    ]


class Sandbox:
    """One throwaway home per repair session, shared by its builds so crates download once."""

    def __init__(self, worktree: Path):
        refuse_if_hosted()
        if mode() in ("docker", "gvisor") and not shutil.which("docker"):
            raise CommandRefused(
                "builds run in a Docker container by default and docker is not installed; install "
                "Docker, or set FORKWARD_SANDBOX=env to build on this machine without file or "
                "network isolation (only for repositories you trust)"
            )
        self.worktree = worktree.resolve()
        self.home = Path(tempfile.mkdtemp(prefix="forkward-build-"))
        (self.home / "cargo").mkdir()
        (self.home / "tmp").mkdir()
        self.env = scrubbed_env(self.home)
        self._fetched = False

    def __enter__(self) -> Sandbox:  # noqa: PYI034
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def run(self, argv: list[str], cwd: Path | None = None, timeout: float = 1800):
        refuse_if_hosted()
        cwd = (cwd or self.worktree).resolve()
        if not cwd.is_relative_to(self.worktree):
            raise CommandRefused(f"refusing to run outside the worktree: {cwd}")
        if mode() in ("docker", "gvisor"):
            cargo = self.home / "cargo"
            if not self._fetched and argv[:1] == ["cargo"]:
                # Downloading crates runs none of their code. It still runs in a container, with
                # network but without secrets, so a toolchain file cannot run a binary on the host.
                fetch = docker_argv(["cargo", "fetch"], self.worktree, cwd, cargo)
                fetch.remove("--network=none")
                fetch[fetch.index("CARGO_NET_OFFLINE=true")] = "CARGO_NET_OFFLINE=false"
                run_bounded(fetch, cwd, timeout)
                self._fetched = True
            return run_bounded(docker_argv(argv, self.worktree, cwd, cargo), cwd, timeout)
        return run_bounded(argv, cwd, timeout, env=self.env, limits=True)
