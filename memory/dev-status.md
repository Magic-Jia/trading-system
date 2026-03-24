# Dev Status

- Primary development progress signal: repo-local post-commit OpenClaw notifications
- Hook entrypoint: `.githooks/post-commit`
- Installer: `python3 -m trading_system.devtools.install_commit_hook`
- Fallback for long no-commit periods: `HEARTBEAT.md`
- Setup and behavior notes: `docs/openclaw-commit-notifications.md`

## Current active coding task

- Branch/worktree: `codex/p0-2-upstream-stop` / `/home/cn/.openclaw/agents/trade/workspace/.worktrees/codex-p0-2-upstream-stop`
- Objective: land the next P0.2 upstream producer slice so rotation candidates emit real `stop_loss` and `invalidation_source`
- Latest commits in active worktree:
  - `5c91489` — `Emit trend stop metadata upstream`
  - `927f8ef` — `Refuse no-stop candidates before execution`
  - `191af92` — `Add execution net exposure hard block`
- Latest verified commands/results:
  - `uv run --with pytest pytest -q trading_system/tests/test_rotation_engine.py::test_generate_rotation_candidates_emit_explicit_stop_loss_and_invalidation_source trading_system/tests/test_main_v2_cycle.py::test_main_v2_rotation_allocations_propagate_explicit_stop_and_invalidation_source` → `2 passed`
  - `uv run --with pytest pytest -q trading_system/tests/test_rotation_engine.py trading_system/tests/test_trend_engine.py trading_system/tests/test_validator.py trading_system/tests/test_main_v2_cycle.py` → `38 passed`
  - `python3 ...` focused repro of `latest_allocations` → `rotation metadata propagates; any remaining block is downstream signal guardrails, not missing stop metadata`
- Last known full-suite baseline on main:
  - `uv run --with pytest pytest -q`
  - Result: `61 passed`
- Current execution mode:
  - This worktree session is the active executor for the slice
  - Next immediate step is commit the finished rotation upstream-stop slice and hand off the remaining short-engine gap
- Current blocker history:
  - `SOUL.md` is missing in this workspace; startup context fell back to `IDENTITY.md` plus the available workspace files
  - Root cause isolated: `rotation_engine` emits scored long candidates without explicit risk metadata, so execution blocks accepted rotation allocations before order intent creation
- Next action:
  1. commit the rotation upstream-stop slice
  2. report verification and root cause
  3. hand off the next remaining P0.2 gap: short-engine upstream stop/invalidation emission
- Last user update time: 2026-03-24 10:52 Europe/Berlin
