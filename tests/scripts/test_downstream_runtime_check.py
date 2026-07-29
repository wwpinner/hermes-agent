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
    expected_python = str(repo / "venv" / "bin" / "python")
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "1234"},
    )
    monkeypatch.setattr(
        drc,
        "_unit_text",
        lambda _service: (
            f"ExecStart={expected_python} -m hermes_cli.main gateway run\n"
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (
            f"{expected_python}\0-m\0hermes_cli.main\0gateway\0run\0".encode()
        ),
    )
    monkeypatch.setattr(os, "readlink", lambda _path: str(repo))
    real_run = drc._run

    def _run(command, *, cwd=None):
        if command and command[0] == expected_python:
            assert cwd in {Path("/"), repo}
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    str(repo / "hermes_cli" / "__init__.py")
                    + "\n"
                    + str(repo / "gateway" / "__init__.py")
                    + "\n"
                ),
                stderr="",
            )
        return real_run(command, cwd=cwd)

    monkeypatch.setattr(drc, "_run", _run)

    results = drc.check_service(repo, "hermes-gateway.service", allow_inactive=False)

    assert all(result.ok for result in results), results


def test_service_check_accepts_dashboard_entrypoint(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "runtime"
    repo.mkdir()
    expected_python = str(repo / "venv" / "bin" / "python")
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "1234"},
    )
    monkeypatch.setattr(
        drc,
        "_unit_text",
        lambda _service: (
            f"ExecStart={expected_python} -m hermes_cli.main dashboard --no-open\n"
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (
            f"{expected_python}\0-m\0hermes_cli.main\0dashboard\0--no-open\0".encode()
        ),
    )
    monkeypatch.setattr(os, "readlink", lambda _path: str(repo.parent))
    real_run = drc._run

    def _run(command, *, cwd=None):
        if command and command[0] == expected_python:
            assert cwd in {Path("/"), repo.parent}
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    str(repo / "hermes_cli" / "__init__.py")
                    + "\n"
                    + str(repo / "gateway" / "__init__.py")
                    + "\n"
                ),
                stderr="",
            )
        return real_run(command, cwd=cwd)

    monkeypatch.setattr(drc, "_run", _run)

    results = drc.check_service(
        repo, "hermes-dashboard.service", allow_inactive=False
    )

    assert all(result.ok for result in results), results


def test_service_check_rejects_stale_checkout_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "runtime"
    repo.mkdir()
    stale = tmp_path / "stale-checkout"
    stale.mkdir()
    expected_python = str(repo / "venv" / "bin" / "python")
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "1234"},
    )
    monkeypatch.setattr(
        drc,
        "_unit_text",
        lambda _service: (
            f"ExecStart={expected_python} -m hermes_cli.main gateway run\n"
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (
            f"{expected_python}\0-m\0hermes_cli.main\0gateway\0run\0".encode()
        ),
    )
    monkeypatch.setattr(os, "readlink", lambda _path: str(stale))
    real_run = drc._run

    def _run(command, *, cwd=None):
        if command and command[0] == expected_python:
            source = repo if cwd == Path("/") else stale
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    str(source / "hermes_cli" / "__init__.py")
                    + "\n"
                    + str(source / "gateway" / "__init__.py")
                    + "\n"
                ),
                stderr="",
            )
        return real_run(command, cwd=cwd)

    monkeypatch.setattr(drc, "_run", _run)

    results = drc.check_service(repo, "hermes-gateway.service", allow_inactive=False)
    by_name = {result.name: result for result in results}

    assert not by_name["service:hermes-gateway.service:process"].ok


def test_service_check_rejects_expected_python_as_later_argument(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "runtime"
    repo.mkdir()
    expected_python = str(repo / "venv" / "bin" / "python")
    monkeypatch.setattr(
        drc,
        "_service_properties",
        lambda _service: {"ActiveState": "active", "MainPID": "1234"},
    )
    monkeypatch.setattr(drc, "_unit_text", lambda _service: "standard\n")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: f"/usr/bin/python\0{expected_python}\0-m\0gateway.run\0".encode(),
    )
    monkeypatch.setattr(os, "readlink", lambda _path: str(repo))

    results = drc.check_service(repo, "hermes-gateway.service", allow_inactive=False)
    by_name = {result.name: result for result in results}

    assert not by_name["service:hermes-gateway.service:process"].ok


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
