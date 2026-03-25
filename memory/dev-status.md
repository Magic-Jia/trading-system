# Dev Status

- branch/worktree: `codex/b1-derivatives` @ `/home/cn/.openclaw/agents/trade/workspace/.worktrees/codex-b1-derivatives`
- current objective: added one focused real `main()` runtime proof that short-side crowded-short / squeeze-risk derivatives suppress a short candidate end-to-end, without further regime-layer changes
- last verified command + result: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k suppresses_crowded_short_candidates_from_runtime_state && PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_short_engine.py -k rejects_crowded_short_squeeze_risk` -> passed (`1 passed, 30 deselected`; `1 passed, 5 deselected`)
- last commit: `aca241c` test: prove short squeeze filter in main cycle
- next action: next narrow B1 slice is a similarly focused real `main()` runtime proof that accepted short allocations surface the same squeeze-risk-aware derivatives context in stdout/report output, if that runtime/report gap still matters
- last user update time: 2026-03-25 07:33 GMT+1
