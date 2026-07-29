from pathlib import Path
import os
import subprocess

from scripts import downstream_runtime_check as drc


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _runtime_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "runtime"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "runtime")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("runtime\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", "git@github.com:wwpinner/hermes-agent.git")
    _git(
        repo,
        "remote",
        "add",
        "upstream",
        "https://github.com/NousResearch/hermes-agent.git",
    )
    _git(repo, "update-ref", "refs/remotes/origin/runtime", head)
    _git(repo, "update-ref", "refs/remotes/upstream/main", head)
    return repo


def test_normalize_remote_handles_github_ssh_and_https() -> None:
    assert (
        drc.normalize_remote("git@github.com:WwPinner/hermes-agent.git")
        == "wwpinner/hermes-agent"
    )
    assert (
        drc.normalize_remote("https://github.com/NousResearch/hermes-agent.git")
        == "nousresearch/hermes-agent"
    )


def test_repo_check_accepts_exact_published_runtime(tmp_path: Path) -> None:
    repo = _runtime_repo(tmp_path)

    results = drc.check_repo(
        repo,
        branch="runtime",
        expected_origin="wwpinner/hermes-agent",
        expected_upstream="NousResearch/hermes-agent",
        allow_unpublished=False,
    )

    assert results
    assert all(result.ok for result in results), results


def test_repo_check_rejects_dirty_or_unpublished_runtime(tmp_path: Path) -> None:
    repo = _runtime_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "unpublished")

    results = drc.check_repo(
        repo,
        branch="runtime",
        expected_origin="wwpinner/hermes-agent",
        expected_upstream="NousResearch/hermes-agent",
        allow_unpublished=False,
    )
    by_name = {result.name: result for result in results}

    assert not by_name["clean"].ok
    assert not by_name["published-head"].ok


def test_repo_check_uses_pinned_upstream_base_not_moving_tip(tmp_path: Path) -> None:
    repo = _runtime_repo(tmp_path)
    pinned = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "newer upstream")
    moving_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/upstream/main", moving_tip)
    _git(repo, "reset", "--hard", "-q", pinned)

    results = drc.check_repo(
        repo,
        branch="runtime",
        expected_origin="wwpinner/hermes-agent",
        expected_upstream="NousResearch/hermes-agent",
        allow_unpublished=False,
        upstream_base=pinned,
    )

    assert all(result.ok for result in results), results


def test_service_check_accepts_repo_python_without_override(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "runtime"
    repo.mkdir()
    expected_python = str((repo / "venv" / "bin" / "python").resolve())
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "1234"},
    )
    monkeypatch.setattr(
        drc,
        "_unit_text",
        lambda _service: f"ExecStart={expected_python} -m gateway.run\n",
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: f"{expected_python}\0-m\0gateway.run\0".encode(),
    )
    monkeypatch.setattr(os, "readlink", lambda _path: str(repo))

    results = drc.check_service(repo, "hermes-gateway.service", allow_inactive=False)

    assert all(result.ok for result in results), results


def test_service_check_rejects_worktree_pythonpath_override(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "runtime"
    repo.mkdir()
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "0"},
    )
    monkeypatch.setattr(
        drc,
        "_unit_text",
        lambda _service: (
            "Environment=PYTHONPATH=/home/user/.hermes/worktrees/feature\n"
            "WorkingDirectory=/home/user/.hermes/worktrees/feature\n"
        ),
    )

    results = drc.check_service(repo, "hermes-gateway.service", allow_inactive=False)
    by_name = {result.name: result for result in results}

    assert not by_name["service:hermes-gateway.service:unit-routing"].ok
