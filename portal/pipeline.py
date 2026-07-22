"""Local git-based promotion pipeline: dev -> testare -> productie.

Purely local, no external hosting: each environment is a separate git
worktree of this same repository, on its own branch, with its own data
directory and port. Promoting an environment fast-forwards its branch
to match the source branch's current commit and updates its working
tree files. It never force-overwrites diverged history, and it never
touches a running server — the operator restarts that environment's
own launcher afterwards.
"""
import pathlib
import subprocess
from datetime import datetime, timezone

ENVIRONMENTS = {
    "dev": {"branch": "dev", "label": "Dezvoltare"},
    "testare": {"branch": "testare", "label": "Testare"},
    "productie": {"branch": "main", "label": "Productie"},
}

# Allowed promotion paths, in order.
PROMOTIONS = [("dev", "testare"), ("testare", "productie")]


class PipelineError(Exception):
    pass


def _repo_paths() -> dict:
    """Absolute paths of the three worktrees, derived from this file's location."""
    here = pathlib.Path(__file__).resolve().parents[1]
    name = here.name
    for suffix in ("-dev", "-testare"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parent = here.parent
    return {"dev": parent / f"{name}-dev",
            "testare": parent / f"{name}-testare",
            "productie": parent / name}


def _git(repo_path, *args) -> str:
    result = subprocess.run(["git", "-C", str(repo_path), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def _is_clean(repo_path) -> bool:
    return _git(repo_path, "status", "--porcelain") == ""


def branch_info(env: str) -> dict:
    """Current commit/subject/date/path for one environment, plus whether
    its worktree exists on disk at all."""
    paths = _repo_paths()
    repo = paths[env]
    branch = ENVIRONMENTS[env]["branch"]
    if not repo.exists():
        return {"env": env, "branch": branch, "path": str(repo), "exists": False}
    return {"env": env, "branch": branch, "path": str(repo), "exists": True,
            "commit": _git(repo, "rev-parse", "--short", branch),
            "subject": _git(repo, "log", "-1", "--format=%s", branch),
            "date": _git(repo, "log", "-1", "--format=%ci", branch)}


def ahead_count(source_env: str, target_env: str) -> int:
    """How many commits `source` has that `target` doesn't yet."""
    paths = _repo_paths()
    repo = paths[target_env]
    src_branch, tgt_branch = (ENVIRONMENTS[source_env]["branch"],
                             ENVIRONMENTS[target_env]["branch"])
    return int(_git(repo, "rev-list", "--count", f"{tgt_branch}..{src_branch}"))


def can_promote(source_env: str, target_env: str) -> bool:
    """True if target's tip is an ancestor of source's tip (safe fast-forward)."""
    paths = _repo_paths()
    result = subprocess.run(
        ["git", "-C", str(paths[target_env]), "merge-base", "--is-ancestor",
         ENVIRONMENTS[target_env]["branch"], ENVIRONMENTS[source_env]["branch"]],
        capture_output=True, text=True)
    return result.returncode == 0


def promote(source_env: str, target_env: str) -> str:
    """Fast-forward target's branch (and working tree) to source's commit.

    Returns the new short commit hash. Raises PipelineError if the
    promotion path isn't allowed, the target worktree has uncommitted
    changes, or target has commits of its own that source doesn't
    (not a fast-forward — needs a manual merge first).
    """
    if (source_env, target_env) not in PROMOTIONS:
        raise PipelineError(f"Promovarea {source_env} -> {target_env} nu e permisa.")
    paths = _repo_paths()
    tgt_repo = paths[target_env]
    if not tgt_repo.exists():
        raise PipelineError(f"Folderul pentru '{target_env}' nu exista: {tgt_repo}")
    if not _is_clean(tgt_repo):
        raise PipelineError(
            f"Mediul '{target_env}' are modificari nesalvate pe disc - "
            "rezolva-le manual inainte de a promova.")
    if not can_promote(source_env, target_env):
        raise PipelineError(
            f"'{ENVIRONMENTS[target_env]['branch']}' are commit-uri proprii "
            f"care nu sunt in '{ENVIRONMENTS[source_env]['branch']}' - "
            "promovarea directa nu e sigura (rezolva manual cu git merge/rebase).")
    src_branch = ENVIRONMENTS[source_env]["branch"]
    result = subprocess.run(["git", "-C", str(tgt_repo), "merge", "--ff-only", src_branch],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise PipelineError((result.stderr or result.stdout).strip())
    return _git(tgt_repo, "rev-parse", "--short", "HEAD")


def log_promotion(conn, source_env: str, target_env: str, commit_hash: str,
                  username: str) -> None:
    conn.execute(
        "INSERT INTO pipeline_log(source_env, target_env, commit_hash, "
        "promoted_by, promoted_at) VALUES(?,?,?,?,?)",
        (source_env, target_env, commit_hash, username,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def history(conn, limit: int = 20) -> list:
    rows = conn.execute(
        "SELECT source_env, target_env, commit_hash, promoted_by, promoted_at "
        "FROM pipeline_log ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
