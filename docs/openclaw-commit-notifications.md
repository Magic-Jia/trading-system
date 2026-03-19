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
- Prefers a worktree-local hooks path when the repo uses multiple worktrees
- If needed, enables git's `extensions.worktreeConfig=true` once so `core.hooksPath` can be set with `git config --worktree`
- Falls back to `git config --local core.hooksPath` for single-worktree repos

## Verify

Check the configured hooks path:

```bash
git config --worktree --get core.hooksPath || git config --local --get core.hooksPath
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
