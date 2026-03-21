# Dev Status

- Primary development progress signal: repo-local post-commit OpenClaw notifications
- Hook entrypoint: `.githooks/post-commit`
- Installer: `python3 -m trading_system.devtools.install_commit_hook`
- Fallback for long no-commit periods: `HEARTBEAT.md`
- Setup and behavior notes: `docs/openclaw-commit-notifications.md`

## Current active coding task

- Branch/worktree: `codex/continue-dev` / `/tmp/openclaw-worktrees/trading-system-continue-dev`
- Objective: continue automatic trading program development from the isolated worktree, now switching long-running execution orchestration to ACP/session-based delivery so main-session reporting is no longer coupled to `exec background`
- Latest commits in active worktree:
  - `6263d37` — `fix: bound active-order replay window`
  - `f86264c` — `fix: tighten stale fingerprint replay gating`
  - `5f16b43` — `fix: expire stale idempotency fingerprints`
  - `35b327f` — `fix: scope allocator replay bypass to matching signal fingerprint`
  - `1f190d3` — `fix: reject unmanaged runtime symbol conflicts in allocator`
- Latest verified commands:
  - `git -C /tmp/openclaw-worktrees/trading-system-continue-dev log --oneline --decorate -n 3 && git -C /tmp/openclaw-worktrees/trading-system-continue-dev status --short --branch`
  - Result: latest worktree commit is `6263d37`; branch clean except untracked runtime artifact `trading_system/data/execution_log.jsonl`
- Last known full-suite baseline on main:
  - `uv run --with pytest pytest -q`
  - Result: `61 passed`
- Current execution mode:
  - Main session reports status; Claw decides the bounded implementation slice; execution is migrating to an ACP/session-based Codex path rather than `exec background`
- Current blocker history:
  - Prior `codex exec --full-auto` attempts in isolated `/tmp` worktrees hit sandbox/bootstrap failures (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`)
  - `exec background` repeatedly failed the user-facing exit-reporting expectation even when coding itself succeeded
  - Local host lacks `rg`; use `grep` / Python fallbacks for repo inspection
- Next action:
  1. persist the orchestration-rule change to workspace memory
  2. spawn an ACP Codex session for the next explicit bounded slice chosen by Claw
  3. continue hardening idempotency / order-state cleanup edge cases without letting Codex choose scope
  4. report immediate status from the main session
- Last user update time: 2026-03-21 10:58 Europe/Berlin
