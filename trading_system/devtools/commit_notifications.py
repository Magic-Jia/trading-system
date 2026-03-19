from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_TIMEOUT_MS = 1500
DEFAULT_MAX_SUBJECT_LENGTH = 120
VALID_NOTIFICATION_MODES = {"now", "next-heartbeat"}
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passphrase)\b\s*[:=]\s*([^\s]+)"
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]+\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"),
)


@dataclass(frozen=True)
class CommitNotification:
    repo_name: str
    branch: str | None
    short_sha: str
    subject: str


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _redact_sensitive_fragments(value: str) -> str:
    text = _normalize_text(value)
    text = SENSITIVE_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    for pattern in SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _truncate(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def build_notification_text(
    notification: CommitNotification,
    *,
    max_subject_length: int = DEFAULT_MAX_SUBJECT_LENGTH,
) -> str:
    repo_name = _truncate(_redact_sensitive_fragments(notification.repo_name), 80)
    branch = _truncate(_redact_sensitive_fragments(notification.branch or "detached"), 80)
    short_sha = _truncate(_normalize_text(notification.short_sha), 20)
    subject = _truncate(_redact_sensitive_fragments(notification.subject), max_subject_length)
    return f"Dev commit in {repo_name} [{branch}] {short_sha}: {subject}"


def build_openclaw_command(text: str, *, mode: str = "now", timeout_ms: int = DEFAULT_TIMEOUT_MS) -> list[str]:
    normalized_mode = mode if mode in VALID_NOTIFICATION_MODES else "now"
    normalized_timeout = timeout_ms if timeout_ms > 0 else DEFAULT_TIMEOUT_MS
    return [
        "openclaw",
        "system",
        "event",
        "--text",
        text,
        "--mode",
        normalized_mode,
        "--timeout",
        str(normalized_timeout),
    ]


def _env_flag_is_enabled(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _git_output(repo_root: Path, args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def collect_head_commit(repo_root: Path) -> CommitNotification:
    branch = _git_output(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        branch = None
    return CommitNotification(
        repo_name=repo_root.name,
        branch=branch,
        short_sha=_git_output(repo_root, ["rev-parse", "--short", "HEAD"]),
        subject=_git_output(repo_root, ["show", "-s", "--format=%s", "HEAD"]),
    )


def _resolve_log_path(repo_root: Path) -> Path:
    try:
        git_dir = Path(_git_output(repo_root, ["rev-parse", "--git-dir"]))
    except subprocess.CalledProcessError:
        return repo_root / ".openclaw-post-commit.log"
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()
    return git_dir / "openclaw-post-commit.log"


def _append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def send_commit_notification(repo_root: Path, env: Mapping[str, str] | None = None) -> int:
    runtime_env = env or os.environ
    if not _env_flag_is_enabled(runtime_env.get("OPENCLAW_COMMIT_NOTIFY"), default=True):
        return 0

    notification = collect_head_commit(repo_root)
    mode = runtime_env.get("OPENCLAW_COMMIT_NOTIFY_MODE", "now")
    try:
        timeout_ms = int(runtime_env.get("OPENCLAW_COMMIT_NOTIFY_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS)))
    except ValueError:
        timeout_ms = DEFAULT_TIMEOUT_MS
    try:
        max_subject_length = int(
            runtime_env.get("OPENCLAW_COMMIT_NOTIFY_MAX_SUBJECT", str(DEFAULT_MAX_SUBJECT_LENGTH))
        )
    except ValueError:
        max_subject_length = DEFAULT_MAX_SUBJECT_LENGTH

    command = build_openclaw_command(
        build_notification_text(notification, max_subject_length=max_subject_length),
        mode=mode,
        timeout_ms=timeout_ms,
    )
    log_path = _resolve_log_path(repo_root)

    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(3, timeout_ms / 1000 + 2),
        )
    except FileNotFoundError:
        _append_log(log_path, "openclaw command not found; skipping post-commit notification")
        return 0
    except subprocess.TimeoutExpired:
        _append_log(log_path, "openclaw notification timed out; skipping post-commit notification")
        return 0

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"exit code {completed.returncode}"
        _append_log(log_path, f"openclaw notification failed: {details}")
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send an OpenClaw notification for the latest git commit.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the repository root. Defaults to the current working directory.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return send_commit_notification(Path(args.repo_root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
