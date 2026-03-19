from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Sequence

HOOKS_DIR_NAME = ".githooks"
POST_COMMIT_HOOK = "post-commit"


def build_install_commands(
    *,
    repo_root: Path,
    has_multiple_worktrees: bool,
    worktree_config_enabled: bool,
) -> list[list[str]]:
    hooks_path = str(repo_root / HOOKS_DIR_NAME)
    if has_multiple_worktrees:
        commands: list[list[str]] = []
        if not worktree_config_enabled:
            commands.append(["git", "config", "--local", "extensions.worktreeConfig", "true"])
        commands.append(["git", "config", "--worktree", "core.hooksPath", hooks_path])
        return commands
    return [["git", "config", "--local", "core.hooksPath", hooks_path]]


def _run_git(repo_root: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        capture_output=True,
        text=True,
    )


def _has_multiple_worktrees(repo_root: Path) -> bool:
    completed = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    count = sum(1 for line in completed.stdout.splitlines() if line.startswith("worktree "))
    return count > 1


def _is_worktree_config_enabled(repo_root: Path) -> bool:
    completed = _run_git(repo_root, ["config", "--bool", "extensions.worktreeConfig"], check=False)
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def _ensure_hook_is_executable(hook_path: Path) -> None:
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_commit_hook(repo_root: Path) -> list[list[str]]:
    hook_path = repo_root / HOOKS_DIR_NAME / POST_COMMIT_HOOK
    if not hook_path.exists():
        raise FileNotFoundError(f"Missing hook script: {hook_path}")

    _ensure_hook_is_executable(hook_path)

    commands = build_install_commands(
        repo_root=repo_root,
        has_multiple_worktrees=_has_multiple_worktrees(repo_root),
        worktree_config_enabled=_is_worktree_config_enabled(repo_root),
    )
    for command in commands:
        subprocess.run(command, cwd=repo_root, check=True, capture_output=True, text=True)
    return commands


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the repo-local post-commit notification hook.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the repository root. Defaults to the current working directory.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    commands = install_commit_hook(repo_root)
    for command in commands:
        print(" ".join(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
