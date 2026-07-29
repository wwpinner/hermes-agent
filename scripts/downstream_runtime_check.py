#!/usr/bin/env python3
"""Fail-closed integrity check for a downstream Hermes runtime checkout.

This script is intentionally dependency-free so it can run before or after a
Hermes dependency update. It never reads process environments or credential
files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _run(
    command: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(("git", *args), cwd=repo)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def normalize_remote(url: str) -> str:
    """Normalize GitHub HTTPS/SSH remotes to ``owner/repository``."""
    value = url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("git@github.com:"):
        return value.removeprefix("git@github.com:").lower()
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
    ):
        if value.startswith(prefix):
            return value.removeprefix(prefix).lower()
    return value.lower()


def _record(results: list[CheckResult], name: str, ok: bool, detail: str) -> None:
    results.append(CheckResult(name=name, ok=ok, detail=detail))


def check_repo(
    repo: Path,
    *,
    branch: str,
    expected_origin: str,
    expected_upstream: str,
    allow_unpublished: bool,
    upstream_base: str | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    try:
        repo_root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    except RuntimeError as exc:
        return [CheckResult("repository", False, str(exc))]

    _record(results, "repository", repo_root == repo, str(repo_root))

    current_branch = _git(repo, "branch", "--show-current")
    _record(results, "branch", current_branch == branch, current_branch or "detached")

    dirty = _git(repo, "status", "--porcelain")
    _record(
        results, "clean", not dirty, "clean" if not dirty else "working tree is dirty"
    )

    for remote, expected in (
        ("origin", expected_origin),
        ("upstream", expected_upstream),
    ):
        try:
            actual_url = _git(repo, "remote", "get-url", remote)
            actual = normalize_remote(actual_url)
            wanted = normalize_remote(expected)
            _record(results, f"remote:{remote}", actual == wanted, actual)
        except RuntimeError as exc:
            _record(results, f"remote:{remote}", False, str(exc))

    head = _git(repo, "rev-parse", "HEAD")
    try:
        remote_head = _git(repo, "rev-parse", f"refs/remotes/origin/{branch}")
        exact = head == remote_head
        detail = f"HEAD={head[:12]} origin/{branch}={remote_head[:12]}"
        _record(results, "published-head", exact or allow_unpublished, detail)
    except RuntimeError as exc:
        _record(results, "published-head", allow_unpublished, str(exc))

    try:
        upstream_ref = upstream_base or "refs/remotes/upstream/main"
        upstream_head = _git(repo, "rev-parse", upstream_ref)
        ancestry = _run(
            ("git", "merge-base", "--is-ancestor", upstream_head, head), cwd=repo
        )
        _record(
            results,
            "upstream-contained",
            ancestry.returncode == 0,
            f"base={upstream_head[:12]} HEAD={head[:12]}",
        )
    except RuntimeError as exc:
        _record(results, "upstream-contained", False, str(exc))

    return results


def _service_properties(service: str) -> dict[str, str]:
    result = _run((
        "systemctl",
        "--user",
        "show",
        service,
        "--property=ActiveState,MainPID,FragmentPath,DropInPaths,ExecStart",
    ))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"systemctl show failed: {detail}")
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _unit_text(service: str) -> str:
    result = _run(("systemctl", "--user", "cat", service))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"systemctl cat failed: {detail}")
    return result.stdout


def check_service(
    repo: Path, service: str, *, allow_inactive: bool
) -> list[CheckResult]:
    results: list[CheckResult] = []
    try:
        properties = _service_properties(service)
        unit_text = _unit_text(service)
    except RuntimeError as exc:
        return [CheckResult(f"service:{service}", False, str(exc))]

    active = properties.get("ActiveState") == "active"
    _record(
        results,
        f"service:{service}:active",
        active or allow_inactive,
        properties.get("ActiveState", "unknown"),
    )

    lowered = unit_text.lower()
    forbidden = []
    if "pythonpath=" in lowered:
        forbidden.append("PYTHONPATH")
    if "/worktrees/" in lowered:
        forbidden.append("worktree path")
    _record(
        results,
        f"service:{service}:unit-routing",
        not forbidden,
        "standard" if not forbidden else ", ".join(forbidden),
    )

    try:
        pid = int(properties.get("MainPID", "0") or "0")
    except ValueError:
        pid = 0
    if pid <= 0:
        _record(
            results,
            f"service:{service}:process",
            allow_inactive,
            "no main process",
        )
        return results

    try:
        raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        argv = [
            part.decode("utf-8", errors="replace")
            for part in raw_cmdline.split(b"\0")
            if part
        ]
        cwd = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError as exc:
        _record(results, f"service:{service}:process", False, str(exc))
        return results

    expected_python = {
        os.path.abspath(repo / "venv" / "bin" / "python"),
        os.path.abspath(repo / ".venv" / "bin" / "python"),
    }
    argv0 = os.path.abspath(argv[0]) if argv else ""
    process_uses_repo = argv0 in expected_python
    process_uses_worktree = (
        any("/worktrees/" in arg.lower() for arg in argv)
        or "/worktrees/" in str(cwd).lower()
    )

    imported_from_repo = False
    if process_uses_repo:
        probe = _run(
            (
                argv0,
                "-c",
                "from pathlib import Path; import hermes_cli; "
                "print(Path(hermes_cli.__file__).resolve())",
            ),
            # Probe outside the checkout. Using cwd=repo would let sys.path[0]
            # mask a stale or incorrect editable installation.
            cwd=Path("/"),
        )
        if probe.returncode == 0 and probe.stdout.strip():
            try:
                Path(probe.stdout.strip()).resolve().relative_to(repo)
                imported_from_repo = True
            except ValueError:
                pass
    _record(
        results,
        f"service:{service}:process",
        process_uses_repo and imported_from_repo and not process_uses_worktree,
        f"pid={pid} cwd={cwd}",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", default="runtime")
    parser.add_argument(
        "--origin", required=True, help="Expected fork remote URL or slug"
    )
    parser.add_argument(
        "--upstream", required=True, help="Expected upstream URL or slug"
    )
    parser.add_argument("--service", action="append", default=[])
    parser.add_argument("--allow-inactive", action="store_true")
    parser.add_argument("--allow-unpublished", action="store_true")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch origin branch and upstream main before checking",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.expanduser().resolve()
    if args.fetch:
        fetches = (
            ("git", "fetch", "--prune", "origin", args.branch),
            ("git", "fetch", "--prune", "upstream", "main"),
        )
        for command in fetches:
            result = _run(command, cwd=repo)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                print(json.dumps({"ok": False, "error": detail}, sort_keys=True))
                return 2

    try:
        base_file = repo / ".downstream-runtime-base"
        upstream_base = (
            base_file.read_text(encoding="utf-8").strip()
            if base_file.is_file()
            else None
        )
        results = check_repo(
            repo,
            branch=args.branch,
            expected_origin=args.origin,
            expected_upstream=args.upstream,
            allow_unpublished=args.allow_unpublished,
            upstream_base=upstream_base,
        )
        for service in args.service:
            results.extend(
                check_service(repo, service, allow_inactive=args.allow_inactive)
            )
    except Exception as exc:  # pragma: no cover - final CLI safety boundary
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    ok = all(result.ok for result in results)
    print(
        json.dumps(
            {"ok": ok, "checks": [asdict(result) for result in results]},
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
