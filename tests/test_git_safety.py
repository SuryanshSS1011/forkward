"""Clone cache and git input safety, against local bare repositories standing in for GitHub."""

import subprocess
import threading
import time

import pytest

from forkward import sync


def g(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def github(tmp_path, monkeypatch):
    """https://github.com/<owner>/<name>.git is rewritten to bare repos under tmp_path/gh."""
    gh = tmp_path / "gh"
    for k in ("FORKWARD_HOSTED", "FORKWARD_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{gh.as_uri()}/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    monkeypatch.setattr(sync, "CACHE", tmp_path / "cache")
    monkeypatch.chdir(tmp_path)

    def make(slug, content):
        bare = gh / f"{slug}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        g(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
        work = tmp_path / "work" / slug
        g(tmp_path, "clone", "-q", str(bare), str(work))
        (work / "f.c").write_text(content)
        g(work, "add", ".")
        g(work, "commit", "-qm", "init")
        g(work, "push", "-q", "origin", "main")
        return work

    return make


def commit(work, content, msg="change"):
    (work / "f.c").write_text(content)
    g(work, "commit", "-qam", msg)
    g(work, "push", "-q", "origin", "HEAD")
    return g(work, "rev-parse", "HEAD")


def test_repo_and_ref_validation():
    for ok in ("torvalds/linux", "a-b/c.d_e", "o/.github", "o/x"):
        assert sync.validate_repo(ok) == ok
    for bad in (
        "../forkward-data",
        "o/..",
        "o/.",
        "-o/x",
        "o/-x",
        "o/x/y",
        "a__b/c",
        "o/x.git",
        "o",
        "o/x y",
        "/etc/passwd",
        "",
    ):
        with pytest.raises(ValueError):
            sync.validate_repo(bad)
    for ok in ("main", "v6.6.30", "feature/x-1", "HEAD", "a" * 40, "release_1.2"):
        assert sync.validate_ref(ok) == ok
    for bad in (
        "+refs/heads/main:refs/tags/v6.6.30",
        "main:evil",
        "-uhelp",
        "--upload-pack=x",
        "a..b",
        "a b",
        "a\tb",
        "a\nb",
        "a\x00b",
        "refs/*",
        "main^",
        "main~1",
        "main@{1}",
        "x.lock",
        "x/",
        "/x",
        ".x",
        "a//b",
        "",
    ):
        with pytest.raises(ValueError):
            sync.validate_ref(bad)
    assert sync.validate_upstream("up/repo@v1.2") == "up/repo@v1.2"
    for bad in ("up/repo@+refs/heads/main:refs/tags/v1", "../x@main", "up/repo@-x", "a:b"):
        with pytest.raises(ValueError):
            sync.validate_upstream(bad)


def test_hosted_mode_never_uses_a_local_path(github, monkeypatch, tmp_path):
    (tmp_path / "forkward-data").mkdir()
    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    for repo in ("../forkward-data", str(tmp_path / "forkward-data"), "forkward-data"):
        with pytest.raises(ValueError):
            sync.checkout(repo)
        with pytest.raises(ValueError):
            sync.lock_target(repo)


def test_base_is_fetched_fresh_from_origin(github):
    fork = github("o/fork", "v1\n")
    path = sync.checkout("o/fork")
    first, name = sync.resolve_base(path, None)
    assert name == "main" and first == g(fork, "rev-parse", "HEAD")
    newer = commit(fork, "v2\n")
    path = sync.checkout("o/fork")
    for base in (None, "HEAD", "main", "origin/main"):
        assert sync.resolve_base(path, base) == (newer, "main")
    with pytest.raises(ValueError):
        sync.resolve_base(path, "no-such-branch")


def test_upstream_fetch_uses_private_refs_and_poisoned_tags_are_reset(github):
    fork = github("o/fork", "v1\n")
    up = github("up/repo", "u1\n")
    g(up, "tag", "v1")
    g(up, "push", "-q", "origin", "v1")
    up_sha = g(up, "rev-parse", "HEAD")
    path = sync.checkout("o/fork")
    assert sync.fetch_upstream(path, "up/repo@main") == up_sha
    assert sync.fetch_upstream(path, f"up/repo@{up_sha}") == up_sha
    assert sync.fetch_upstream(path, f"up/repo@{up_sha[:10]}") == up_sha
    # Nothing is left behind in the shared clone, and FETCH_HEAD is never what decides the commit.
    assert g(path, "for-each-ref", "refs/forkward/") == ""
    # A tag an earlier request planted in the cache is reset to origin's on the next checkout.
    g(fork, "tag", "v6")
    g(fork, "push", "-q", "origin", "v6")
    path = sync.checkout("o/fork")
    g(path, "tag", "-f", "v6", up_sha)
    g(path, "tag", "planted", up_sha)
    path = sync.checkout("o/fork")
    assert g(path, "rev-parse", "v6^{commit}") == g(fork, "rev-parse", "HEAD")
    assert g(path, "tag", "-l", "planted") == ""
    # A commit another request fetched is in the shared object store, but not usable by name.
    with pytest.raises(ValueError):
        sync.resolve_base(path, up_sha)
    with pytest.raises(ValueError):
        sync.fetch_upstream(path, up_sha)
    with pytest.raises(ValueError):
        sync.fetch_upstream(path, "up/repo@+refs/heads/main:refs/tags/v6")


def test_concurrent_fetches_each_get_their_own_commit(github):
    github("o/fork", "v1\n")
    a = github("up/a", "a\n")
    b = github("up/b", "b\n")
    want = {"up/a@main": g(a, "rev-parse", "HEAD"), "up/b@main": g(b, "rev-parse", "HEAD")}
    path = sync.checkout("o/fork")
    got, errors = {}, []

    def run(spec):
        try:
            for _ in range(5):
                with sync.repo_lock(path):
                    got.setdefault(spec, set()).add(sync.fetch_upstream(path, spec))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(s,)) for s in want for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert {k: v for k, v in got.items()} == {k: {v} for k, v in want.items()}


def test_repo_lock_serializes_and_gives_up(github, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "LOCK_WAIT", 0.3)
    target = tmp_path / "cache" / "o__fork"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with sync.repo_lock(target):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="busy"), sync.repo_lock(target):
        pass
    assert time.monotonic() - t0 < 3
    release.set()
    t.join()
    with sync.repo_lock(target):
        pass


def test_worktree_merges_into_the_fresh_base_of_a_cached_clone(github):
    fork = github("o/fork", "int keep = 1;\n")
    up = github("up/repo", "int keep = 1;\n")
    # Give both the same root so they share history, then diverge.
    g(up, "fetch", "-q", str(fork.parent.parent.parent / "gh" / "o" / "fork.git"), "main")
    g(up, "reset", "-q", "--hard", "FETCH_HEAD")
    g(up, "push", "-q", "-f", "origin", "HEAD:main")
    up_sha = commit(up, "int keep = 2;\n", "upstream fix")
    path = sync.checkout("o/fork")
    newer = commit(fork, "int keep = 3; /* vendor */\n", "vendor change")
    path = sync.checkout("o/fork")
    sha = sync.fetch_upstream(path, "up/repo@main")
    assert sha == up_sha
    base, _ = sync.resolve_base(path, "main")
    assert base == newer
    wt = sync.worktree(path, base, "forkward/preview-test")
    try:
        assert g(wt, "rev-parse", "HEAD") == newer
        assert not sync._merge(wt, sha)
        assert "<<<<<<<" in (wt / "f.c").read_text()
    finally:
        sync.drop_worktree(path, wt, "forkward/preview-test", keep_branch=False)
    assert not wt.exists()


def test_clone_and_fetch_send_the_token_without_dropping_other_git_config(github, monkeypatch):
    github("o/private", "int a;\n")
    seen = []
    real = sync.git

    def spy(repo, *args, **kw):
        if args[0] in ("clone", "fetch"):
            seen.append((kw.get("env") or {}).get("GIT_CONFIG_KEY_1"))
        return real(repo, *args, **kw)

    monkeypatch.setattr(sync, "git", spy)
    path = sync.checkout("o/private", token="ghs_installation")
    sync.checkout("o/private", token="ghs_installation")
    assert (path / "HEAD").exists() or (path / ".git").exists()
    assert seen == ["http.https://github.com/.extraheader"] * 2
