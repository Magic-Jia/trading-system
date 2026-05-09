#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


FORBIDDEN_FILES = {"memory/dev-status.md"}


def git_lines(command: list[str]) -> list[str]:
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def commit_changed_files(commit: str) -> list[str]:
    resolved = git_lines(["git", "rev-parse", "--verify", commit])[0]
    parents = git_lines(["git", "rev-list", "--parents", "-n", "1", resolved])[0].split()
    if len(parents) == 1:
        return git_lines(["git", "show", "--pretty=", "--name-only", resolved])
    return git_lines(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", resolved])


def verification_plan(changed_files: list[str]) -> dict[str, object]:
    command = [sys.executable, "scripts/verify.py", "--dry-run", "--json"]
    for path in changed_files:
        command.extend(["--changed", path])
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "verification plan failed")
    return json.loads(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controller-side audit for isolated worker commits")
    parser.add_argument("--commit", default=None, help="commit to audit, e.g. HEAD or a worker hash")
    parser.add_argument("--changed-file", action="append", default=[], help="explicit changed file for tests/fixtures")
    args = parser.parse_args(argv)

    try:
        changed_files = list(args.changed_file)
        commit = args.commit or ""
        if args.commit:
            commit = git_lines(["git", "rev-parse", "--verify", args.commit])[0]
            changed_files.extend(commit_changed_files(commit))
        changed_files = list(dict.fromkeys(changed_files))
        if not changed_files:
            print("no changed files to audit", file=sys.stderr)
            return 2
        forbidden = sorted(set(changed_files) & FORBIDDEN_FILES)
        if forbidden:
            print(f"forbidden worker changed files: {', '.join(forbidden)}", file=sys.stderr)
            return 2
        plan = verification_plan(changed_files)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "audit_kind": "worker_commit_preflight",
                "final_merge_proof": False,
                "commit": commit,
                "changed_files": changed_files,
                "verification_plan": plan,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
