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
    del repo_root, has_multiple_worktrees, worktree_config_enabled
    # A tracked relative hooks path is inherited by fresh linked worktrees.
    return [["git", "config", "--local", "core.hooksPath", HOOKS_DIR_NAME]]


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


def resolve_hooks_dir(repo_root: Path, configured_hooks_path: str) -> Path:
    hooks_dir = Path(configured_hooks_path)
    if hooks_dir.is_absolute():
        return hooks_dir.resolve()
    return (repo_root / hooks_dir).resolve()


def _read_configured_hooks_path(repo_root: Path) -> str | None:
    completed = _run_git(repo_root, ["config", "--get", "core.hooksPath"], check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def verify_installed_hook(repo_root: Path, *, configured_hooks_path: str | None = None) -> Path:
    hook_path = repo_root / HOOKS_DIR_NAME / POST_COMMIT_HOOK
    if not hook_path.exists():
        raise FileNotFoundError(f"Missing hook script: {hook_path}")
    if not hook_path.is_file():
        raise RuntimeError(f"Hook path is not a file: {hook_path}")
    if not os.access(hook_path, os.X_OK):
        raise PermissionError(f"Hook is not executable: {hook_path}")

    configured_value = configured_hooks_path or _read_configured_hooks_path(repo_root)
    if not configured_value:
        raise RuntimeError("core.hooksPath is not configured for this repo/worktree")

    resolved_hooks_dir = resolve_hooks_dir(repo_root, configured_value)
    expected_hooks_dir = (repo_root / HOOKS_DIR_NAME).resolve()
    if resolved_hooks_dir != expected_hooks_dir:
        raise RuntimeError(
            f"Configured core.hooksPath={configured_value!r} resolves to {resolved_hooks_dir}, "
            f"which does not point to {expected_hooks_dir}"
        )
    return resolved_hooks_dir


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
    verify_installed_hook(repo_root)
    return commands


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the repo-local post-commit notification hook.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the repository root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the installed hook without mutating git config.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    if args.check:
        print(verify_installed_hook(repo_root))
        return 0

    commands = install_commit_hook(repo_root)
    for command in commands:
        print(" ".join(command))
    print(verify_installed_hook(repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
