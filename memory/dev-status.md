# Dev Status

- Primary development progress signal: repo-local post-commit OpenClaw notifications
- Hook entrypoint: `.githooks/post-commit`
- Installer: `python3 -m trading_system.devtools.install_commit_hook`
- Fallback for long no-commit periods: `HEARTBEAT.md`
- Setup and behavior notes: `docs/openclaw-commit-notifications.md`

## Current active coding task

- Branch/worktree: `codex/p0-2-next` / `/home/cn/.openclaw/agents/trade/workspace/.worktrees/codex-p0-2-next`
- Objective: land the next P0.2 hard risk gate slice without weakening the existing stricter baseline
- Latest commits in active worktree:
  - `bf868ff` — `Refresh tests for stricter risk baseline`
  - `1133ed9` — `Fail fast on unsupported live mode`
  - `1e6d77a` — `test: cover invalid stop guardrails in main cycle`
- Latest verified commands/results:
  - baseline reference from user: main is green after `1133ed9` and `bf868ff`
  - code reading completed across `validator.py`, `guardrails.py`, `allocator.py`, `main.py`, and the current P0.2 tests
  - identified likely gap: execution-time `validate_signal` does not hard-block net exposure using real planned notional after sizing; allocator only checks a risk-budget proxy
- Last known full-suite baseline on main:
  - `uv run --with pytest pytest -q`
  - Result: `61 passed`
- Current execution mode:
  - This worktree session is the active executor for the slice
  - Next immediate step is TDD red on validator/main-cycle coverage for execution-time net exposure hard blocking
- Current blocker history:
  - `SOUL.md` is missing in this workspace; startup context fell back to the available workspace files
  - No repo-local blocker found yet for the targeted P0.2 slice
- Next action:
  1. add failing focused tests for net-exposure rejection and non-execution
  2. implement the minimal guardrail/config change to make those tests pass
  3. run narrow then broader verification and commit the slice if clean
- Last user update time: 2026-03-24 07:57 Europe/Berlin
