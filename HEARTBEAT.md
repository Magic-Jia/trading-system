# HEARTBEAT.md

Commit-trigger notifications are the primary progress signal in this worktree.

- If there has been no commit-trigger notification for more than 90 minutes during active implementation, send one concise status update.
- Do not duplicate a recent post-commit notification.
- If the hook appears broken, check `docs/openclaw-commit-notifications.md` and the git-dir log `openclaw-post-commit.log`.
