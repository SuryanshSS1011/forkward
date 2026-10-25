import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from forkward import repair, sandbox
from forkward.repair import source_path


def test_source_path_resolves_workspace_relative_paths(tmp_path: Path) -> None:
    crate = tmp_path / "crates" / "utils"
    src = crate / "src" / "lib.rs"
    src.parent.mkdir(parents=True)
    src.write_text("")
    assert source_path(Path("crates/utils/src/lib.rs"), crate) == src
    assert source_path(Path("src/lib.rs"), crate) == src
    assert source_path(src, crate) == src


def test_source_path_never_leaves_the_worktree(tmp_path: Path) -> None:
    outside = tmp_path / "src" / "lib.rs"
    outside.parent.mkdir(parents=True)
    outside.write_text("")
    wt = tmp_path / "wt"
    crate = wt / "crate"
    crate.mkdir(parents=True)
    # The same relative path exists above the worktree; it must not be picked.
    assert source_path(Path("src/lib.rs"), crate, wt) == crate / "src" / "lib.rs"


@pytest.fixture
def local(monkeypatch):
    for k in ("FORKWARD_HOSTED", "FORKWARD_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)
    # These tests run real commands on the host, which needs the explicit opt-out.
    monkeypatch.setenv("FORKWARD_SANDBOX", "env")


def test_builds_are_containerized_unless_the_opt_out_is_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv("FORKWARD_SANDBOX", raising=False)
    assert sandbox.mode() == "docker"
    monkeypatch.setenv("FORKWARD_SANDBOX", "")
    assert sandbox.mode() == "docker"
    monkeypatch.setenv("FORKWARD_SANDBOX", "none")
    with pytest.raises(sandbox.CommandRefused, match="not one of"):
        sandbox.mode()
    monkeypatch.setenv("FORKWARD_SANDBOX", "docker")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.CommandRefused, match="FORKWARD_SANDBOX=env"):
        sandbox.Sandbox(tmp_path)


def test_build_commands_are_allowlisted(local):
    assert sandbox.check_command("cargo build --release -p core") == [
        "cargo",
        "build",
        "--release",
        "-p",
        "core",
    ]
    assert sandbox.check_command("cargo test --workspace -- --nocapture")[-1] == "--nocapture"
    for bad in (
        "sh -c 'curl x | sh'",
        "make",
        "cargo run",
        "cargo +nightly build",
        "cargo build --config build.rustc-wrapper=/bin/sh",
        "cargo --config x build",
        "cargo build --config=x",
        "cargo build -Zunstable-options",
        "cargo build --target-dir /tmp/x",
        "cargo build --manifest-path /etc/Cargo.toml",
        "cargo build --manifest-path ../other/Cargo.toml",
        "cargo build $(id)",
        "cargo build 'unterminated",
    ):
        with pytest.raises(sandbox.CommandRefused):
            sandbox.check_command(bad)


def test_hosted_mode_never_runs_a_build(monkeypatch, tmp_path):
    monkeypatch.delenv("FORKWARD_TRANSPORT", raising=False)
    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    with pytest.raises(sandbox.CommandRefused):
        sandbox.check_command("cargo build")
    with pytest.raises(sandbox.CommandRefused):
        sandbox.Sandbox(tmp_path)
    with pytest.raises(sandbox.CommandRefused):
        asyncio.run(repair.repair(tmp_path, "cargo build"))
    monkeypatch.delenv("FORKWARD_HOSTED")
    monkeypatch.setenv("FORKWARD_TRANSPORT", "http")
    assert sandbox.hosted()


def test_sync_refuses_a_build_on_the_hosted_service_before_touching_the_repo(monkeypatch, tmp_path):
    from forkward import sync

    monkeypatch.setenv("FORKWARD_HOSTED", "1")

    def no_checkout(repo):
        raise AssertionError("checkout must not run")

    monkeypatch.setattr(sync, "checkout", no_checkout)
    with pytest.raises(sandbox.CommandRefused):
        asyncio.run(sync.sync("o/fork", "up/repo@main", "main", build_cmd="cargo build"))


def test_build_environment_carries_no_secrets(local, monkeypatch, tmp_path):
    for k in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "MONGODB_URI",
        "LUMINAL_API_KEY",
        "ANTHROPIC_API_KEY",
        "AUTH_SECRET",
        "PREVIEW_SECRET",
        "AWS_SECRET_ACCESS_KEY",
        "SOME_SERVICE_TOKEN",
    ):
        monkeypatch.setenv(k, "leak-" + k)
    monkeypatch.setenv("LANG", "C.UTF-8")
    with sandbox.Sandbox(tmp_path) as sb:
        p = sb.run([sys.executable, "-c", "import os, json; print(json.dumps(dict(os.environ)))"])
        home = sb.home
    env = json.loads(p.stdout)
    assert not [v for v in env.values() if "leak-" in v]
    assert env["HOME"] == str(home) and env["CARGO_HOME"] == str(home / "cargo")
    assert env["LANG"] == "C.UTF-8" and "PATH" in env
    assert not home.exists()


def test_sandbox_runs_only_inside_the_worktree(local, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with sandbox.Sandbox(wt) as sb, pytest.raises(sandbox.CommandRefused):
        sb.run(["true"], tmp_path)


def test_timeout_kills_the_whole_process_group(local, tmp_path):
    marker = tmp_path / "grandchild-alive"
    script = f"(sleep 3; touch {marker}) & sleep 30"
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        sandbox.run_bounded(["sh", "-c", script], tmp_path, timeout=1)
    assert time.monotonic() - t0 < 10
    time.sleep(3.5)
    assert not marker.exists()


def test_docker_mode_has_no_network_and_mounts_only_the_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    argv = sandbox.docker_argv(["cargo", "build"], wt, wt, tmp_path / "cargo", image="img")
    assert "--network=none" in argv and "--read-only" in argv and "--cap-drop=ALL" in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [
        f"{wt.resolve()}:{wt.resolve()}:rw",
        f"{(tmp_path / 'cargo').resolve()}:/cargo:rw",
    ]
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert not [e for e in envs if sandbox.SECRET_RE.search(e.split("=")[0])]
    assert argv[-3:] == ["img", "cargo", "build"]


def test_repair_edits_stay_inside_the_worktree_and_reject_risky_lines(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    f = wt / "lib.rs"
    f.write_text("fn a() {\n    let x = old();\n}\n")
    outside = tmp_path / "registry.rs"
    outside.write_text("let x = old();\n")
    with pytest.raises(sandbox.CommandRefused):
        repair._apply(outside, [{"line": 1, "old": "let x = old();", "new": "let x = new();"}], wt)
    assert outside.read_text() == "let x = old();\n"
    link = wt / "link.rs"
    os.symlink(outside, link)
    with pytest.raises(sandbox.CommandRefused):
        repair._apply(link, [{"line": 1, "old": "let x = old();", "new": "let x = new();"}], wt)
    for risky in (
        'let x = std::process::Command::new("sh");',
        "let x = unsafe { old() };",
        'let x = include_str!("/etc/passwd");',
        "let x = old();\nlet y = 1;",
    ):
        assert repair._apply(f, [{"line": 2, "old": "let x = old();", "new": risky}], wt) == []
    assert repair._apply(f, [{"line": "x", "old": "", "new": ""}], wt) == []
    done = repair._apply(f, [{"line": 2, "old": "let x = old();", "new": "let x = new();"}], wt)
    assert done == [(2, "let x = old();", "    let x = new();")]


def test_build_never_points_a_diagnostic_outside_the_worktree(local, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "lib.rs").write_text("x\n")

    def diag(file_name):
        return json.dumps(
            {
                "reason": "compiler-message",
                "manifest_path": str(wt / "Cargo.toml"),
                "message": {
                    "level": "error",
                    "message": "boom",
                    "code": {"code": "E0425"},
                    "rendered": "boom",
                    "spans": [
                        {"is_primary": True, "file_name": file_name, "line_start": 1, "text": []}
                    ],
                },
            }
        )

    out = "\n".join(
        [diag("src/lib.rs"), diag("/root/.cargo/registry/src/dep/lib.rs"), diag("../x.rs")]
    )

    class FakeSandbox:
        def run(self, argv, cwd, timeout):
            assert argv == ["cargo", "build", "--message-format=json"]
            return subprocess.CompletedProcess(argv, 101, out, "")

    diags = repair.build(wt, ["cargo", "build"], FakeSandbox())
    # Errors outside the worktree are reported with no file, so no edit is ever attempted there.
    assert [Path(d.file).name if d.file else "" for d in diags] == ["lib.rs", ""]


def test_builds_run_under_resource_limits(local, monkeypatch, tmp_path):
    monkeypatch.setenv("FORKWARD_SANDBOX_MEMORY_MB", "2048")
    p = sandbox.run_bounded(
        ["sh", "-c", "ulimit -c; ulimit -v; echo $0 $1", "x", "y"], tmp_path, 10, limits=True
    )
    # macOS refuses an address-space cap, which limited() skips rather than failing the build.
    mem = str(2048 << 10) if sys.platform == "linux" else p.stdout.split()[1]
    assert p.stdout.split() == ["0", mem, "x", "y"]


@pytest.mark.skipif(
    not (Path.home() / ".cargo" / "bin" / "cargo").exists()
    and not __import__("shutil").which("cargo"),
    reason="cargo is not installed",
)
def test_a_real_build_script_sees_no_secrets(local, monkeypatch, tmp_path):
    """build.rs is arbitrary code from upstream. It runs, but with nothing worth stealing."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_leak")
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://leak")
    monkeypatch.setenv("LUMINAL_API_KEY", "leak")
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\nbuild = "build.rs"\n'
    )
    (tmp_path / "src" / "lib.rs").write_text("pub fn f() {}\n")
    (tmp_path / "build.rs").write_text(
        "fn main() {\n"
        "    let seen: Vec<String> = std::env::vars().map(|(k, _)| k).collect();\n"
        '    std::fs::write(std::env::var("OUT_DIR").unwrap() + "/../../../../env.txt",'
        ' seen.join("\\n")).unwrap();\n'
        "}\n"
    )
    argv = sandbox.check_command("cargo build --offline")
    with sandbox.Sandbox(tmp_path) as sb:
        diags = repair.build(tmp_path, argv, sb, timeout=600)
    assert diags == []
    seen = set((tmp_path / "target" / "env.txt").read_text().split("\n"))
    assert not seen & {"GITHUB_TOKEN", "MONGODB_URI", "LUMINAL_API_KEY", "GH_TOKEN"}
    assert "CARGO_HOME" in seen and "OUT_DIR" in seen


def test_gvisor_mode_runs_the_same_container_under_runsc(monkeypatch, tmp_path):
    from forkward import sandbox

    monkeypatch.setenv("FORKWARD_SANDBOX", "gvisor")
    argv = sandbox.docker_argv(["cargo", "build"], tmp_path, tmp_path, tmp_path / "c", image="i")
    assert argv[:3] == ["docker", "run", "--runtime=runsc"] and "--network=none" in argv
    monkeypatch.setenv("FORKWARD_SANDBOX", "docker")
    assert "--runtime=runsc" not in sandbox.docker_argv(["cargo"], tmp_path, tmp_path, tmp_path)
