# OpenClaw Commit Notifications

This workspace can notify OpenClaw automatically after each new local git commit.

## Mechanism

- `.githooks/post-commit` is the repo-local hook entrypoint.
- The hook calls `python3 -m trading_system.devtools.commit_notifications`.
- The hook runs the notifier in the background so `git commit` is not held open while OpenClaw processes the event.
- The Python notifier sends `openclaw system event --mode now` with a short, sanitized summary:
  - worktree/repo name
  - branch name
  - short commit hash
  - commit subject only
- The notifier never includes the commit body or diff.
- Common secret-like fragments such as `token=...`, `api_key=...`, `secret=...` are redacted before sending.
- Hook failures do not block commits. Errors are appended to the worktree git dir log:
  `$(git rev-parse --git-dir)/openclaw-post-commit.log`

## Install

Run from the repo root:

```bash
python3 -m trading_system.devtools.install_commit_hook
```

What the installer does:

- Ensures `.githooks/post-commit` is executable
- Sets shared git config `core.hooksPath=.githooks`
- Verifies that the effective hooks path in the current worktree resolves back to `repo_root/.githooks`

Why the shared relative path matters:

- `.githooks/post-commit` is tracked in git, so each linked worktree has the same hook entrypoint
- Fresh worktrees inherit the shared `core.hooksPath=.githooks` setting automatically
- This avoids the old failure mode where a newly created worktree had no `core.hooksPath` set and silently skipped notifications

## Verify

Check the configured hooks path:

```bash
git config --get core.hooksPath
python3 -m trading_system.devtools.install_commit_hook --check
```

Trigger a test notification with an empty commit:

```bash
git commit --allow-empty -m "chore: verify commit notifications"
```

## Runtime Controls

- `OPENCLAW_COMMIT_NOTIFY=0` disables the hook
- `OPENCLAW_COMMIT_NOTIFY_MODE=now|next-heartbeat` changes delivery mode
- `OPENCLAW_COMMIT_NOTIFY_TIMEOUT_MS=30000` overrides the CLI timeout
- `OPENCLAW_COMMIT_NOTIFY_MAX_SUBJECT=120` truncates the commit subject more aggressively

## Heartbeat Fallback

Commit-trigger notifications are the primary progress signal during active development.
Heartbeat remains the fallback path for long no-commit periods, so the hook intentionally does not replace heartbeat-based check-ins.
