"""Tests run against scratch git repos/worktrees, never the real project repo."""
import subprocess
import sqlite3

import pytest

from portal import pipeline


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def envs(tmp_path, monkeypatch):
    """DEV/TESTARE/PROD nested inside one container, matching the real layout."""
    container = tmp_path / "e-TVA-Reconciliere"
    productie = container / "PROD"
    productie.mkdir(parents=True)
    _git(productie, "init", "-q", "-b", "main")
    _git(productie, "config", "user.email", "t@example.com")
    _git(productie, "config", "user.name", "Test")
    (productie / "f.txt").write_text("v1")
    _git(productie, "add", "f.txt")
    _git(productie, "commit", "-q", "-m", "c1")
    _git(productie, "branch", "dev", "main")
    _git(productie, "branch", "testare", "main")

    dev = container / "DEV"
    testare = container / "TESTARE"
    _git(productie, "worktree", "add", "-q", str(dev), "dev")
    _git(productie, "worktree", "add", "-q", str(testare), "testare")

    paths = {"dev": dev, "testare": testare, "productie": productie}
    monkeypatch.setattr(pipeline, "_repo_paths", lambda: paths)
    return paths


def test_branch_info_reports_current_commit(envs):
    info = pipeline.branch_info("productie")
    assert info["exists"] is True
    assert info["subject"] == "c1"
    assert len(info["commit"]) >= 7


def test_branch_info_missing_worktree(envs, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_repo_paths", lambda: {
        **envs, "testare": tmp_path / "does-not-exist"})
    info = pipeline.branch_info("testare")
    assert info["exists"] is False


def test_ahead_count_zero_when_equal(envs):
    assert pipeline.ahead_count("dev", "testare") == 0
    assert pipeline.can_promote("dev", "testare") is True


def test_promote_dev_to_testare_fast_forwards(envs):
    (envs["dev"] / "f.txt").write_text("v2")
    _git(envs["dev"], "commit", "-aq", "-m", "c2 on dev")

    assert pipeline.ahead_count("dev", "testare") == 1
    result = pipeline.promote("dev", "testare")

    assert (envs["testare"] / "f.txt").read_text() == "v2"
    assert pipeline.ahead_count("dev", "testare") == 0
    assert result["commit"] == _git(envs["testare"], "rev-parse", "--short", "HEAD")


def test_promote_reports_push_failure_without_losing_local_promotion(envs):
    """No 'origin' remote in the test repo - push must fail gracefully,
    but the local fast-forward must still have happened (and be reported)."""
    (envs["dev"] / "f.txt").write_text("v2")
    _git(envs["dev"], "commit", "-aq", "-m", "c2 on dev")

    result = pipeline.promote("dev", "testare")

    assert result["pushed"] is False
    assert result["push_error"]
    assert (envs["testare"] / "f.txt").read_text() == "v2"  # still promoted locally


def test_promote_pushes_to_a_real_remote(tmp_path, envs):
    """With a real 'origin' configured, promote() must push the target branch."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    # remotes live in the shared .git config, so adding one from any
    # worktree makes it visible to all worktrees of the same repo.
    _git(envs["dev"], "remote", "add", "origin", str(bare))
    for env in ("dev", "testare"):
        _git(envs[env], "push", "-q", "origin", env)

    (envs["dev"] / "f.txt").write_text("v2")
    _git(envs["dev"], "commit", "-aq", "-m", "c2 on dev")
    _git(envs["dev"], "push", "-q", "origin", "dev")

    result = pipeline.promote("dev", "testare")

    assert result["pushed"] is True
    assert result["push_error"] is None
    remote_head = _git(bare, "rev-parse", "--short", "refs/heads/testare")
    assert remote_head == result["commit"]


def test_promote_rejects_disallowed_path(envs):
    with pytest.raises(pipeline.PipelineError):
        pipeline.promote("testare", "dev")


def test_promote_blocked_by_uncommitted_target_changes(envs):
    (envs["dev"] / "f.txt").write_text("v2")
    _git(envs["dev"], "commit", "-aq", "-m", "c2 on dev")
    (envs["testare"] / "f.txt").write_text("dirty, uncommitted")

    with pytest.raises(pipeline.PipelineError, match="modificari nesalvate"):
        pipeline.promote("dev", "testare")


def test_promote_blocked_when_target_has_diverged(envs):
    (envs["testare"] / "g.txt").write_text("only on testare")
    _git(envs["testare"], "add", "g.txt")
    _git(envs["testare"], "commit", "-q", "-m", "diverged commit")
    (envs["dev"] / "f.txt").write_text("v2")
    _git(envs["dev"], "commit", "-aq", "-m", "c2 on dev")

    with pytest.raises(pipeline.PipelineError, match="commit-uri proprii"):
        pipeline.promote("dev", "testare")


def test_log_and_history_round_trip():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE pipeline_log(id INTEGER PRIMARY KEY, source_env TEXT, "
        "target_env TEXT, commit_hash TEXT, promoted_by TEXT, promoted_at TEXT)")
    pipeline.log_promotion(conn, "dev", "testare", "abc123", "sef")
    rows = pipeline.history(conn)
    assert rows[0]["source_env"] == "dev"
    assert rows[0]["target_env"] == "testare"
    assert rows[0]["commit_hash"] == "abc123"
    assert rows[0]["promoted_by"] == "sef"


def test_capture_started_commit_handles_missing_git(monkeypatch):
    def _boom(*a, **kw):
        raise pipeline.PipelineError("git not found")
    monkeypatch.setattr(pipeline, "_git", _boom)
    assert pipeline._capture_started_commit() == {
        "commit": None, "subject": None, "started_at": None}


def test_running_vs_current_flags_a_stale_server(monkeypatch):
    monkeypatch.setattr(pipeline, "STARTED_AT", {
        "commit": "abc123", "subject": "Old feature",
        "started_at": "2026-01-01 00:00 UTC"})
    monkeypatch.setattr(pipeline, "_git", lambda repo, *args: "def456")
    result = pipeline.running_vs_current()
    assert result == {
        "started_commit": "abc123", "started_subject": "Old feature",
        "started_at": "2026-01-01 00:00 UTC", "current_commit": "def456",
        "stale": True}


def test_running_vs_current_not_stale_when_commits_match(monkeypatch):
    monkeypatch.setattr(pipeline, "STARTED_AT", {
        "commit": "abc123", "subject": "Latest", "started_at": "t"})
    monkeypatch.setattr(pipeline, "_git", lambda repo, *args: "abc123")
    assert pipeline.running_vs_current()["stale"] is False


def test_running_vs_current_handles_git_unavailable(monkeypatch):
    monkeypatch.setattr(pipeline, "STARTED_AT", {
        "commit": "abc123", "subject": "x", "started_at": "t"})
    def _boom(*a, **kw):
        raise pipeline.PipelineError("git not found")
    monkeypatch.setattr(pipeline, "_git", _boom)
    result = pipeline.running_vs_current()
    assert result["current_commit"] is None and result["stale"] is False
