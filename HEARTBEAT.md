# HEARTBEAT.md

Commit-trigger notifications are the primary progress signal in this worktree, but they are not the only one.

- If a coding task is blocked, a coding agent exits early, a background Codex session exits, or no active executor is actually running, send a concise status update immediately instead of waiting for a commit-trigger notification.
- During active implementation, if there has been no commit-trigger notification for more than 45 minutes, send one concise status update.
- That fallback status update must say explicitly: `Codex: running / not running`, a status type of `started / start_failed / stopped`, whether there is a new commit, the latest verified command/result, the current blocker (if any), and the next action. If Codex is not running, say whether it exited, failed to start, crashed, or there is no active executor.
- If a background Codex task has already exited, first check for a new commit and then send the fixed exit template before doing any further analysis or retries.
- Do not duplicate a recent post-commit notification.
- If the hook appears broken, check `docs/openclaw-commit-notifications.md` and the git-dir log `openclaw-post-commit.log`.
