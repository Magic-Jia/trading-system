# Trading Batch Runtime Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前交易系统落成可持续运行的批处理架构：支持按环境分桶状态与快照目录，用统一 runner 执行单轮 cycle，并提供 systemd 定时运行模板与最小监控输出。

**Architecture:** 保持 `trading_system.app.main` 作为单轮主循环，不把它改造成常驻事件引擎。新增 `runtime_paths` 负责环境到路径的统一映射，新增 `run_cycle` 负责单轮编排、结果摘要与失败收口；再用 systemd `oneshot service + timer` 做正式调度。所有状态、快照、ledger、日志都先按 `paper` / `testnet` / `live` 分桶，再进入原有交易逻辑。

**Tech Stack:** Python 3、现有 `trading_system` 模块、`uv run --with pytest`、systemd service/timer、JSON state/log files。

---

## File structure map

### New files

- `trading_system/app/runtime_paths.py` — 解析 `TRADING_RUNTIME_ENV` 与 `TRADING_BASE_DIR`，统一产出 state/snapshot/log 路径。
- `trading_system/run_cycle.py` — 单轮 runner，负责目录准备、数据刷新入口、调用主循环、写 `latest.json`。
- `trading_system/tests/test_runtime_paths.py` — 验证不同环境不会串目录。
- `trading_system/tests/test_run_cycle.py` — 验证 runner 成功 / 失败 / 摘要写出。
- `deploy/systemd/trading-system-paper.service` — paper 单轮执行 service。
- `deploy/systemd/trading-system-paper.timer` — paper 定时器。
- `trading_system/docs/BATCH_RUNTIME_RUNBOOK.md` — 持续运行手册。

### Modified files

- `trading_system/app/config.py` — 新增 runtime env / base dir 配置入口。
- `trading_system/app/main.py` — 用统一路径解析补默认路径，避免手动路径散落。
- `trading_system/README.md` — 增加 batch runtime 与 systemd 用法。
- `trading_system/docs/PAPER_TRADING_RUNBOOK.md` — 从单次运行补充到长期运行。

---

## Chunk 1: Runtime path isolation

### Task 1: Add runtime path model

**Files:**
- Create: `trading_system/app/runtime_paths.py`
- Test: `trading_system/tests/test_runtime_paths.py`

- [ ] **Step 1: Write the failing test for paper path resolution**

```python
def test_runtime_paths_default_to_env_bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "paper")
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))

    paths = build_runtime_paths()

    assert paths.state_dir == tmp_path / "paper" / "state"
    assert paths.snapshot_dir == tmp_path / "paper" / "snapshots"
    assert paths.log_dir == tmp_path / "paper" / "logs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_runtime_paths.py`
Expected: FAIL because `build_runtime_paths` does not exist yet.

- [ ] **Step 3: Write minimal implementation for runtime path builder**

Implement a small dataclass + builder that:
- reads `TRADING_RUNTIME_ENV`
- reads `TRADING_BASE_DIR`
- derives `<base>/<env>/state|snapshots|logs`
- exposes concrete files like `runtime_state.json`, `account_snapshot.json`, `market_context.json`, `derivatives_snapshot.json`, `latest.json`

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Add cross-env isolation regression test**

```python
def test_runtime_paths_do_not_mix_env_buckets(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")
    testnet_paths = build_runtime_paths()

    monkeypatch.setenv("TRADING_RUNTIME_ENV", "live")
    live_paths = build_runtime_paths()

    assert testnet_paths.runtime_state_file != live_paths.runtime_state_file
    assert "/testnet/" in str(testnet_paths.runtime_state_file)
    assert "/live/" in str(live_paths.runtime_state_file)
```

- [ ] **Step 6: Run test to verify both tests pass**

Run the same command.
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add trading_system/app/runtime_paths.py trading_system/tests/test_runtime_paths.py
git commit -m "feat: add runtime environment path isolation"
```

---

## Chunk 2: Wire runtime paths into config and main cycle

### Task 2: Make config understand runtime env defaults

**Files:**
- Modify: `trading_system/app/config.py`
- Modify: `trading_system/app/main.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing test for default path selection**

Add a test proving that when only `TRADING_RUNTIME_ENV` and `TRADING_BASE_DIR` are set, the cycle resolves the correct paper bucket paths without explicitly passing snapshot/state file env vars.

- [ ] **Step 2: Run targeted test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k "runtime_env_default_paths"`
Expected: FAIL because config still expects explicit scattered paths.

- [ ] **Step 3: Add minimal config wiring**

Implement config logic that:
- keeps current explicit env vars working
- but when they are missing, falls back to `runtime_paths`
- ensures `TRADING_RUNTIME_ENV` is preserved into runtime outputs

- [ ] **Step 4: Run targeted test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Add regression test that legacy explicit file env vars still override defaults**

This protects current callers from breakage.

- [ ] **Step 6: Run the targeted config tests again**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add trading_system/app/config.py trading_system/app/main.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: wire runtime environment paths into main cycle"
```

---

## Chunk 3: Add single-run batch runner

### Task 3: Build `run_cycle.py`

**Files:**
- Create: `trading_system/run_cycle.py`
- Test: `trading_system/tests/test_run_cycle.py`

- [ ] **Step 1: Write the failing test for a successful runner cycle**

Test behavior:
- prepares directories
- invokes the cycle entrypoint
- writes `logs/latest.json`
- records `env`, `status`, `started_at`, `finished_at`

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_run_cycle.py -k "successful"`
Expected: FAIL because runner does not exist.

- [ ] **Step 3: Implement minimal successful-path runner**

The runner should:
- call `build_runtime_paths()`
- create required directories
- invoke a narrow execution function
- write `latest.json`
- return exit code 0 on success

- [ ] **Step 4: Run test to verify it passes**

Expected: PASS.

- [ ] **Step 5: Add failing test for runner failure summary**

Test behavior:
- if cycle raises, runner returns non-zero
- `latest.json` still gets written with `status=error`
- summary contains error message and env

- [ ] **Step 6: Run test to verify it fails for the right reason**

Expected: FAIL because error-path summary is missing.

- [ ] **Step 7: Implement minimal failure handling**

- [ ] **Step 8: Run both runner tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_run_cycle.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add trading_system/run_cycle.py trading_system/tests/test_run_cycle.py
git commit -m "feat: add single-run batch cycle runner"
```

---

## Chunk 4: Keep paper replay and runtime summary intact

### Task 4: Prove new runner does not break paper ledger behavior

**Files:**
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_run_cycle.py`

- [ ] **Step 1: Add failing regression test for paper replay compatibility**

Cover that the runner path still preserves:
- `paper_ledger.jsonl`
- replay-from-ledger behavior when `runtime_state.json` is missing
- summary fields under `portfolio.paper_trading`

- [ ] **Step 2: Run the focused replay test and verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k "paper_cycle_replays_from_ledger_when_state_is_missing" trading_system/tests/test_run_cycle.py`
Expected: FAIL because runner path does not preserve or expose expected files yet.

- [ ] **Step 3: Make the minimal integration fix**

Keep existing paper behavior intact; do not redesign executor.

- [ ] **Step 4: Run the replay-focused verification again**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_run_cycle.py trading_system/run_cycle.py
git commit -m "test: preserve paper replay behavior under batch runner"
```

---

## Chunk 5: Add systemd templates and docs

### Task 5: Add deployable timer/service files and runbook

**Files:**
- Create: `deploy/systemd/trading-system-paper.service`
- Create: `deploy/systemd/trading-system-paper.timer`
- Create: `trading_system/docs/BATCH_RUNTIME_RUNBOOK.md`
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/PAPER_TRADING_RUNBOOK.md`

- [ ] **Step 1: Write the failing doc/test check (if lightweight doc test exists) or add a snapshot-style assertion for service template strings**

If no existing test harness fits service files, add a small parser/unit test to confirm the rendered service uses:
- `Type=oneshot`
- `TRADING_RUNTIME_ENV=paper`
- runner entrypoint
- `Persistent=true` in timer

- [ ] **Step 2: Run it to verify it fails**

Expected: FAIL because the templates do not exist.

- [ ] **Step 3: Create minimal service/timer templates**

Service should:
- be `oneshot`
- set runtime env
- run the batch runner
- target the workspace and `uv run`

Timer should:
- use a fixed interval (documented default)
- set `Persistent=true`

- [ ] **Step 4: Create `BATCH_RUNTIME_RUNBOOK.md`**

Include:
- install steps for systemd units
- where state/logs go
- how to check service status
- how to inspect latest summary JSON
- how to disable/enable timer

- [ ] **Step 5: Update README and paper runbook**

Add a short “single run vs scheduled run” section and point to the new runbook.

- [ ] **Step 6: Run the doc/template verification again**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add deploy/systemd/trading-system-paper.service deploy/systemd/trading-system-paper.timer trading_system/docs/BATCH_RUNTIME_RUNBOOK.md trading_system/README.md trading_system/docs/PAPER_TRADING_RUNBOOK.md
git commit -m "docs: add batch runtime deployment templates"
```

---

## Chunk 6: Final focused verification

### Task 6: Run the focused package verification

**Files:**
- No new files required

- [ ] **Step 1: Run runtime path tests**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_runtime_paths.py`
Expected: PASS.

- [ ] **Step 2: Run runner tests**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_run_cycle.py`
Expected: PASS.

- [ ] **Step 3: Run focused main-cycle regression tests**

Run: `PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k "paper_cycle_emits_paper_trading_summary_and_records_ledger or paper_cycle_replays_from_ledger_when_state_is_missing or runtime_env_default_paths"`
Expected: PASS.

- [ ] **Step 4: Run one manual paper cycle through the new runner**

Run: `TRADING_RUNTIME_ENV=paper TRADING_BASE_DIR=/tmp/trading-batch-runtime-smoke PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-batch-runtime uv run python -m trading_system.run_cycle`
Expected: exit 0, `latest.json` written, no path leakage into other env buckets.

- [ ] **Step 5: Inspect the resulting summary file**

Confirm it contains:
- `env=paper`
- `status=ok`
- state / snapshot file paths under `/paper/`

- [ ] **Step 6: Commit any final fixups**

```bash
git add -A
git commit -m "test: verify batch runtime package"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-03-29-trading-batch-runtime-implementation.md`. Ready to execute?
