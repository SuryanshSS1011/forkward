"""The conflict parser on real merges: odd file names, marker-like content, non-text conflicts."""

import subprocess

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


DOC = "Resolve like this:\n<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\nend\n"


def merge_repo(root):
    """A fork and an upstream that conflict in five different ways."""
    g(root, "init", "-q", "-b", "main")
    files = {
        "my file.c": "int a;\nint keep = 1;\nint z;\n",
        "docs.md": DOC + "version 1\n",
        "gone.c": "int x;\n",
        "blob.bin": "\0\1\2",
        "two seps.c": "a\nb\nc\n",
        "ok.c": "int ok = 1;\n",
    }
    for name, text in files.items():
        (root / name).write_text(text)
    g(root, "add", ".")
    g(root, "commit", "-qm", "base")
    g(root, "checkout", "-qb", "upstream")
    (root / "my file.c").write_text("int a;\nint keep = 2;\nint z;\n")
    (root / "docs.md").write_text(DOC + "version 2 upstream\n")
    (root / "gone.c").write_text("int x = 1;\n")
    (root / "blob.bin").write_bytes(b"\0\1\3")
    (root / "two seps.c").write_text("a\n=======\nB\nc\n")
    (root / "ok.c").write_text("int ok = 2;\n")
    g(root, "commit", "-qam", "upstream")
    up = g(root, "rev-parse", "HEAD")
    g(root, "checkout", "-q", "main")
    (root / "my file.c").write_text("int a;\nint keep = 3; /* vendor */\nint z;\n")
    (root / "docs.md").write_text(DOC + "version 2 fork\n")
    g(root, "rm", "-q", "gone.c")
    (root / "blob.bin").write_bytes(b"\0\1\4")
    (root / "two seps.c").write_text("a\nV\nc\n")
    (root / "ok.c").write_text("int ok = 3;\n")
    g(root, "commit", "-qam", "vendor")
    assert not sync._merge(root, up)
    return up


def test_parser_handles_odd_names_markers_and_non_text_conflicts(tmp_path):
    up = merge_repo(tmp_path)
    skipped = []
    hunks = sync.conflict_hunks(tmp_path, up, skipped)
    by_file = {}
    for h, f, _ in hunks:
        by_file.setdefault(f, []).append(h)
    # A name with a space is one file, and the doc's own marker-like lines are not conflicts.
    assert set(by_file) == {"my file.c", "docs.md", "ok.c"}
    assert by_file["my file.c"][0].vendor == ("int keep = 3; /* vendor */",)
    assert by_file["my file.c"][0].upstream == ("int keep = 2;",)
    assert len(by_file["docs.md"]) == 1
    assert by_file["docs.md"][0].vendor == ("version 2 fork",)
    reasons = dict(skipped)
    assert set(reasons) == {"gone.c", "blob.bin", "two seps.c"}
    assert reasons["gone.c"].startswith("modify/delete conflict: deleted in this fork")
    assert "binary" in reasons["blob.bin"]
    assert reasons["two seps.c"].startswith("could not read the conflict")


def test_one_bad_file_never_blocks_the_commit(tmp_path):
    up = merge_repo(tmp_path)
    skipped = []
    hunks = sync.conflict_hunks(tmp_path, up, skipped)
    results = [
        sync.HunkResult(
            f,
            h.tag,
            "int ok = 3;" if f == "ok.c" else "x",
            0.9,
            "auto_apply" if f == "ok.c" else "needs_review",
            "r",
        )
        for h, f, _ in hunks
    ] + [sync.HunkResult(f, f"{f}#file", "", 0.0, "blocked", why) for f, why in skipped]
    unresolved = sync.apply(tmp_path, hunks, results)
    assert unresolved == sorted(["blob.bin", "docs.md", "gone.c", "my file.c", "two seps.c"])
    g(tmp_path, "commit", "-qm", "merge with markers left for review")
    assert g(tmp_path, "show", "HEAD:ok.c") == "int ok = 3;"
    assert "<<<<<<< HEAD" in g(tmp_path, "show", "HEAD:my file.c")
    # The doc's literal marker lines are untouched; only the real block keeps git's markers.
    doc = g(tmp_path, "show", "HEAD:docs.md")
    assert doc.startswith(DOC.strip()) and f">>>>>>> {up}" in doc
    assert g(tmp_path, "show", "HEAD:gone.c") == "int x = 1;"
    assert g(tmp_path, "diff", "--name-only", "--diff-filter=U") == ""


def test_blocked_files_reach_the_pull_request_body(monkeypatch):
    from forkward import history

    monkeypatch.setattr(history, "stats", lambda repo: {"auto_applied": 0})
    res = sync.SyncResult("o/fork", "forkward/sync-1", "up/r@main", False)
    res.hunks = [
        sync.HunkResult(
            "gone.c", "gone.c#file", "", 0.0, "blocked", "modify/delete conflict: deleted upstream"
        )
    ]
    body = sync.pr_body(res)
    assert "No proposal: modify/delete conflict: deleted upstream." in body


def test_a_merge_git_refuses_raises_instead_of_reporting_conflicts(tmp_path):
    g(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.c").write_text("int a;\n")
    g(tmp_path, "add", ".")
    g(tmp_path, "commit", "-qm", "base")
    with pytest.raises(RuntimeError, match="git merge failed"):
        sync._merge(tmp_path, "0" * 40)


def test_file_level_conflicts_alone_still_reach_the_commit_message(tmp_path):
    g(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "gone.c").write_text("int x;\n")
    g(tmp_path, "add", ".")
    g(tmp_path, "commit", "-qm", "base")
    g(tmp_path, "checkout", "-qb", "upstream")
    (tmp_path / "gone.c").write_text("int x = 1;\n")
    g(tmp_path, "commit", "-qam", "upstream")
    up = g(tmp_path, "rev-parse", "HEAD")
    g(tmp_path, "checkout", "-q", "main")
    g(tmp_path, "rm", "-q", "gone.c")
    g(tmp_path, "commit", "-qm", "vendor")
    assert not sync._merge(tmp_path, up)
    skipped = []
    assert sync.conflict_hunks(tmp_path, up, skipped) == []
    results = [sync.HunkResult(f, f"{f}#file", "", 0.0, "blocked", why) for f, why in skipped]
    unresolved = sync._commit_merge(tmp_path, [], results, "merge", tmp_path / "lock")
    assert unresolved == ["gone.c"]
    assert "gone.c" in g(tmp_path, "log", "-1", "--format=%B")
