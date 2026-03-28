# Binance Futures Testnet Phase 1 Minimal Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Phase 1 Binance Futures testnet mode that can connect with signed credentials, load a real testnet account snapshot, run one strategy cycle, and emit a validated order preview plus runtime summary without real order submission by default.

**Architecture:** Extend the existing one-shot trading cycle with a narrow `testnet` mode that performs signed Futures testnet connectivity checks, strict config/account-mode fail-fast validation, exchange-rule validation, and runtime-state preview output. Phase 1 keeps `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0` by default and does not implement real order submission, partial-fill handling, protective-order execution, or continuous running; it only preserves the explicit config/runtime path so Phase 2 can turn that branch on safely later.

**Tech Stack:** Python 3.12, existing `trading_system.app.*` modules, stdlib HTTP helpers, existing Binance helper code, pytest, JSON runtime-state persistence.

---

## File structure map

### Existing files to modify

- `trading_system/app/config.py`
  - Add `testnet` execution mode and strict Phase 1 env parsing.
  - Reject production Futures endpoints and missing/incomplete testnet credentials in `testnet` mode.
- `trading_system/app/main.py`
  - Route one-shot testnet preflight and validated preview output into runtime state.
  - Preserve candidate/allocation/precheck summary visibility in the runtime output.
- `trading_system/app/execution/executor.py`
  - Add a testnet Phase 1 preview-only branch that never submits real orders when submission is disabled.
- `trading_system/binance_client.py`
  - Add minimal Futures testnet signed/public request helpers needed by preflight, account loading, and metadata validation.
- `trading_system/app/types.py`
  - Only extend if a stable preview structure helper materially improves clarity; otherwise keep dict shapes documented in tests.
- `trading_system/tests/test_main_v2_cycle.py`
  - Add Phase 1 one-shot runtime-state coverage.
- `trading_system/tests/test_executor.py`
  - Add testnet preview-only executor coverage.
- `trading_system/README.md`
  - Document the new Phase 1 mode and its “preview only by default” behavior.

### New files to create

- `trading_system/app/data_sources/testnet_account_loader.py`
  - Fetch and normalize Binance Futures testnet account data into `AccountSnapshot`.
- `trading_system/app/data_sources/testnet_exchange_metadata.py`
  - Fetch and expose exchange rules used by Phase 1 local validation.
- `trading_system/app/execution/testnet_preview.py`
  - Build the stable `validated_order_preview` artifact, including explicit fallback labeling for no-signal runs and fixed Futures payload mapping checks.
- `trading_system/docs/TESTNET_PHASE1_RUNBOOK.md`
  - Document env setup, preflight checks, one-shot command, expected output, and manual recovery flow.
- `trading_system/tests/test_testnet_config.py`
  - Focused testnet config/guardrail tests.
- `trading_system/tests/test_testnet_account_loader.py`
  - Focused account-preflight tests.
- `trading_system/tests/test_testnet_preview.py`
  - Focused payload/validation/preview tests.

### Scope locks for this plan

- Phase 1 keeps `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0` by default.
- No continuous loop, no real protective-order placement, no partial-fill execution logic.
- The explicit submission-enabled path is preserved only as config/runtime metadata and guard behavior, not as a Phase 1 implementation target.
- No-signal runs must still exercise payload construction and validation through a clearly labeled non-signal preview artifact such as `preview_source=no_signal_fallback`.
- The preview artifact must explicitly show whether real submission prerequisites are satisfied, even though Phase 1 does not perform real submission.

## Chunk 1: Config, endpoint safety, signed connectivity, and account-mode preflight

### Task 1: Add failing config tests for testnet mode, endpoint safety, and missing credentials

**Files:**
- Test: `trading_system/tests/test_testnet_config.py`
- Modify: `trading_system/app/config.py`

- [ ] **Step 1: Write failing tests for execution-mode parsing, defaults, endpoint rejection, and credential fail-fast**

```python
import pytest
from trading_system.app import config as config_module


def test_build_config_accepts_testnet_mode_and_phase1_defaults(monkeypatch):
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "250")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "2")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "secret")

    config = config_module.build_config()

    assert config.execution.mode == "testnet"
    assert config.execution.testnet_order_submission_enabled is False
    assert config.execution.testnet_allowed_symbols == ["BTCUSDT", "ETHUSDT"]
```

```python
def test_build_config_rejects_non_testnet_futures_endpoint(monkeypatch):
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://fapi.binance.com")
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "250")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "1")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "secret")

    with pytest.raises(ValueError, match="testnet"):
        config_module.build_config()
```

```python
def test_build_config_rejects_missing_or_half_configured_testnet_credentials(monkeypatch):
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "250")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "1")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)

    with pytest.raises(ValueError, match="credential|secret|apikey"):
        config_module.build_config()
```

- [ ] **Step 2: Run the focused config tests to verify they fail first**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_config.py`
Expected: FAIL because `testnet` mode and strict credential/endpoint guards do not exist yet.

- [ ] **Step 3: Implement minimal config support and endpoint/credential safety checks**

Implementation details:
- Extend `ExecutionMode` to include `"testnet"`.
- Add execution fields for:
  - `testnet_order_submission_enabled: bool`
  - `testnet_allowed_symbols: list[str]`
  - `testnet_max_order_notional_usdt: float`
  - `testnet_max_open_positions: int`
- When `mode == "testnet"`, require:
  - Futures testnet URL
  - complete testnet API credentials
  - explicit allowlist
  - positive notional and position caps
- Reject production Futures endpoints and half-configured credentials before startup.

- [ ] **Step 4: Re-run the focused config tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_config.py`
Expected: PASS.

- [ ] **Step 5: Commit the config slice**

```bash
git add trading_system/app/config.py trading_system/tests/test_testnet_config.py
git commit -m "feat: add phase1 testnet config guardrails"
```

### Task 2: Add failing tests for signed auth connectivity, submission-readiness preflight, and strict account-mode truth sources

**Files:**
- Create: `trading_system/app/data_sources/testnet_account_loader.py`
- Test: `trading_system/tests/test_testnet_account_loader.py`
- Modify: `trading_system/binance_client.py`

- [ ] **Step 1: Write failing tests for signed connectivity, one-way/cross/single-asset checks, and ambiguous responses**

```python
import pytest
from trading_system.app.data_sources.testnet_account_loader import (
    load_testnet_account_snapshot,
    testnet_account_preflight,
)


def test_testnet_account_preflight_reports_signed_connectivity(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_server_time",
        lambda: {"serverTime": 1710000000000},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_local_time_ms",
        lambda: 1710000000100,
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {"assets": [], "positions": [], "multiAssetsMargin": False},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {"dualSidePosition": False},
    )

    result = testnet_account_preflight()

    assert result["mode"] == "testnet"
    assert result["signed_connectivity_ok"] is True
    assert result["position_mode"] == "one-way"
    assert result["timestamp_skew_ok"] is True
```

```python
def test_testnet_account_preflight_reports_timestamp_skew_failure(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_server_time",
        lambda: {"serverTime": 1710000000000},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_local_time_ms",
        lambda: 1710001000000,
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {"assets": [], "positions": [], "multiAssetsMargin": False},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {"dualSidePosition": False},
    )

    result = testnet_account_preflight()

    assert result["timestamp_skew_ok"] is False
```

```python
def test_testnet_account_preflight_requires_submission_readiness_when_toggle_enabled(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_server_time",
        lambda: {"serverTime": 1710000000000},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {"assets": [], "positions": [], "multiAssetsMargin": False},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {"dualSidePosition": False},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_submission_permissions",
        lambda: {"can_trade": False, "reason": "permission_missing"},
    )

    result = testnet_account_preflight(submission_enabled=True)

    assert result["submission_prerequisites_passed"] is False
    assert result["permission_check"]["can_trade"] is False
```

```python
def test_load_testnet_account_snapshot_rejects_multi_assets_mode(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {"assets": [], "positions": [], "multiAssetsMargin": True},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {"dualSidePosition": False},
    )

    with pytest.raises(RuntimeError, match="single-asset|multi-assets"):
        load_testnet_account_snapshot()
```

```python
def test_load_testnet_account_snapshot_rejects_non_cross_margin(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {
            "assets": [{"asset": "USDT", "walletBalance": "1000", "availableBalance": "900"}],
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.01", "marginType": "isolated"}],
            "multiAssetsMargin": False,
        },
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {"dualSidePosition": False},
    )

    with pytest.raises(RuntimeError, match="cross"):
        load_testnet_account_snapshot()
```

```python
def test_load_testnet_account_snapshot_rejects_ambiguous_mode_fields(monkeypatch):
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_account",
        lambda: {"assets": [], "positions": []},
    )
    monkeypatch.setattr(
        "trading_system.app.data_sources.testnet_account_loader.fetch_futures_testnet_position_mode",
        lambda: {},
    )

    with pytest.raises(RuntimeError, match="unsupported|ambiguous|one-way"):
        load_testnet_account_snapshot()
```

- [ ] **Step 2: Run the focused loader tests to confirm they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_account_loader.py`
Expected: FAIL because the loader and explicit preflight helpers do not exist yet.

- [ ] **Step 3: Implement minimal signed connectivity helpers and account-mode preflight**

Implementation details:
- In `trading_system/binance_client.py`, add minimal Futures testnet helpers for:
  - server time
  - local time capture for skew checking
  - signed account fetch
  - position-mode fetch
  - a single concrete submission-readiness truth source used when the toggle is on (for Phase 1, keep this to deterministic signed account usability / trading-enabled inspection instead of vague permission heuristics)
- In `testnet_account_loader.py`, implement explicit truth sources:
  - `dualSidePosition` → one-way check
  - `multiAssetsMargin` → single-asset check
  - `marginType` on account position payload → cross check when positions exist
  - signed account usability / trading-enabled fields from the chosen Phase 1 submission-readiness endpoint → submission readiness check
- For the no-open-position case, lock the behavior now: **fail fast as unsupported/ambiguous in Phase 1** if cross mode cannot be verified from a deterministic truth source.
- Implement `testnet_account_preflight()` returning signed connectivity, `mode=testnet`, timestamp-skew/`recvWindow` status, mode-check results, and submission-readiness/permission-check results when the submission toggle is on.
- Implement `load_testnet_account_snapshot()` normalizing account data into `AccountSnapshot`.
- Fail fast on ambiguous or missing fields needed for one-way + cross + single-asset verification.

- [ ] **Step 4: Re-run the focused loader tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_account_loader.py`
Expected: PASS.

- [ ] **Step 5: Commit the preflight slice**

```bash
git add trading_system/binance_client.py trading_system/app/data_sources/testnet_account_loader.py trading_system/tests/test_testnet_account_loader.py
git commit -m "feat: add futures testnet preflight and account loader"
```

## Chunk 2: Exchange-rule validation, fixed payload mapping, and preview-only execution path

### Task 3: Add failing tests for exchange metadata validation and fixed Futures payload mapping

**Files:**
- Create: `trading_system/app/data_sources/testnet_exchange_metadata.py`
- Create: `trading_system/app/execution/testnet_preview.py`
- Test: `trading_system/tests/test_testnet_preview.py`

- [ ] **Step 1: Write failing tests for symbol rule validation and fixed payload mapping**

```python
from trading_system.app.execution.testnet_preview import build_validated_order_preview
from trading_system.app.types import OrderIntent


def test_validated_order_preview_checks_step_size_and_precision():
    intent = OrderIntent(
        intent_id="intent-btc",
        signal_id="signal-btc",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.00015,
        entry_price=65000.123,
        stop_loss=64000.456,
        take_profit=67000.789,
    )
    metadata = {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100,
            "allowed_order_types": ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        }
    }

    preview = build_validated_order_preview(
        intent,
        exchange_metadata=metadata,
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    assert preview["local_validation_passed"] is False
    assert any("step" in reason.lower() or "precision" in reason.lower() for reason in preview["reasons"])
```

```python
def test_validated_order_preview_exposes_fixed_futures_payload_mapping():
    intent = OrderIntent(
        intent_id="intent-btc",
        signal_id="signal-btc",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=65000,
        stop_loss=64000,
        take_profit=67000,
    )
    metadata = {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100,
            "allowed_order_types": ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        }
    }

    preview = build_validated_order_preview(
        intent,
        exchange_metadata=metadata,
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    assert preview["payloads"]["entry"]["type"] == "MARKET"
    assert preview["payloads"]["stop"]["type"] == "STOP_MARKET"
    assert preview["payloads"]["take_profit"]["type"] == "TAKE_PROFIT_MARKET"
    assert preview["payloads"]["stop"]["closePosition"] == "true"
    assert preview["payloads"]["take_profit"]["workingType"] == "MARK_PRICE"
```

- [ ] **Step 2: Run the focused preview tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_preview.py`
Expected: FAIL because exchange metadata validation and preview helpers do not exist yet.

- [ ] **Step 3: Implement minimal exchange-metadata loading and fixed mapping validation helpers**

Implementation details:
- Add metadata loader functions exposing only the rule fields needed by Phase 1.
- In `testnet_preview.py`, build the preview artifact and validate:
  - allowlist
  - allowed order types
  - min notional
  - quantity step size
  - price tick size
  - fixed Futures payload mapping for entry/stop/take-profit
- Surface field-mapping incompatibilities explicitly in preview reasons.

- [ ] **Step 4: Re-run the focused preview tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_preview.py`
Expected: PASS.

- [ ] **Step 5: Commit the validation-and-mapping slice**

```bash
git add trading_system/app/data_sources/testnet_exchange_metadata.py trading_system/app/execution/testnet_preview.py trading_system/tests/test_testnet_preview.py
git commit -m "feat: add testnet preview validation and payload mapping"
```

### Task 4: Add failing tests for preview readiness fields and preview-only executor behavior

**Files:**
- Modify: `trading_system/app/execution/executor.py`
- Modify: `trading_system/app/types.py`
- Modify: `trading_system/tests/test_executor.py`
- Modify: `trading_system/tests/test_testnet_preview.py`

- [ ] **Step 1: Write failing tests for readiness fields and no-signal fallback previews**

```python
def test_build_validated_order_preview_marks_no_signal_fallback_source():
    preview = build_validated_order_preview(
        intent=fake_order_intent(),
        exchange_metadata=fake_exchange_metadata(),
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="no_signal_fallback",
    )

    assert preview["preview_source"] == "no_signal_fallback"
    assert preview["submission_enabled"] is False
    assert preview["would_submit"] is False
    assert preview["submission_prerequisites_passed"] is True
```

```python
def test_order_executor_testnet_mode_returns_preview_without_submission(tmp_path):
    config = build_testnet_config(tmp_path)
    executor = OrderExecutor(config)

    result = executor.execute(fake_order_intent(), RuntimeState.empty())

    assert result["mode"] == "testnet"
    assert result["would_submit"] is False
    assert result["submission_enabled"] is False
```

- [ ] **Step 2: Run the focused preview/executor tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_preview.py trading_system/tests/test_executor.py -k 'testnet or preview'`
Expected: FAIL because the readiness field and executor branch do not exist yet.

- [ ] **Step 3: Implement the stable preview readiness fields and preview-only executor branch**

Implementation details:
- Extend `validated_order_preview` to include:
  - `submission_enabled`
  - `would_submit`
  - `submission_prerequisites_passed`
  - `preview_source`
- Keep `submission_enabled` visible even though Phase 1 does not perform real order submission.
- In `OrderExecutor`, add a `testnet` branch that returns/logs the preview artifact and does not submit when submission is disabled.
- If adding a typed helper in `types.py` makes this substantially clearer, do so narrowly.

- [ ] **Step 4: Re-run the focused preview/executor tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_preview.py trading_system/tests/test_executor.py -k 'testnet or preview'`
Expected: PASS.

- [ ] **Step 5: Commit the preview/executor slice**

```bash
git add trading_system/app/execution/executor.py trading_system/app/types.py trading_system/tests/test_testnet_preview.py trading_system/tests/test_executor.py
git commit -m "feat: add phase1 testnet preview executor"
```

## Chunk 3: Main-cycle integration, runtime summary visibility, and docs

### Task 5: Add failing integration tests for one-shot Phase 1 runtime-state output and summary visibility

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write failing integration tests for runtime-state preview output and broader summary visibility**

```python
def test_main_testnet_phase1_writes_runtime_summary_and_validated_order_preview(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "500")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "1")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "secret")

    monkeypatch.setattr(main_module, "load_testnet_account_snapshot", lambda: fake_account_snapshot())
    monkeypatch.setattr(main_module, "testnet_account_preflight", lambda: {"mode": "testnet", "signed_connectivity_ok": True, "position_mode": "one-way", "timestamp_skew_ok": True})
    monkeypatch.setattr(main_module, "load_testnet_exchange_metadata", lambda: fake_exchange_metadata())
    monkeypatch.setattr(main_module, "load_market_context", lambda: fake_market_context())
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", lambda: fake_derivatives_snapshot())

    main_module.main()

    state = json.loads(output_path.read_text())
    assert state["execution_mode"] == "testnet"
    assert state["testnet_preflight"]["signed_connectivity_ok"] is True
    assert "latest_candidates" in state
    assert "latest_allocations" in state
    assert state["validated_order_preview"]["symbol"]
    assert state["validated_order_preview"]["side"]
    assert state["validated_order_preview"]["qty"]
    assert state["validated_order_preview"]["local_validation_passed"] in {True, False}
    assert state["validated_order_preview"]["submission_prerequisites_passed"] in {True, False}
    assert "reasons" in state["validated_order_preview"]
    assert state["validated_order_preview"]["order_types"]
    assert "precheck_summary" in state
```

```python
def test_main_testnet_phase1_labels_no_signal_no_execution(monkeypatch, tmp_path):
    ...
    main_module.main()
    state = json.loads(output_path.read_text())
    assert state["validated_order_preview"]["preview_source"] == "no_signal_fallback"
    assert state["precheck_summary"]["status"] == "no_signal_no_execution"
```

```python
def test_main_testnet_phase1_labels_validation_failure(monkeypatch, tmp_path):
    ...
    main_module.main()
    state = json.loads(output_path.read_text())
    assert state["precheck_summary"]["status"] == "validation_failed"
```

```python
def test_main_testnet_phase1_labels_interface_failure(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "500")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "1")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "secret")

    monkeypatch.setattr(main_module, "testnet_account_preflight", lambda: {"mode": "testnet", "signed_connectivity_ok": False, "failure_class": "interface_failed", "account_identifier": "testnet-account-1", "permission_check": {"can_trade": False, "reason": "api_unreachable"}})

    with pytest.raises(RuntimeError):
        main_module.main()

    state = json.loads(output_path.read_text())
    assert state["precheck_summary"]["status"] == "interface_failed"
    assert state["testnet_preflight"]["account_identifier"] == "testnet-account-1"
    assert state["testnet_preflight"]["permission_check"]["can_trade"] is False
```

- [ ] **Step 2: Run the focused integration tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k testnet_phase1`
Expected: FAIL because main-cycle testnet runtime-state support does not exist yet.

- [ ] **Step 3: Implement the minimal one-shot testnet main-cycle integration**

Implementation details:
- Branch on `execution.mode == "testnet"` in `main.py`.
- Run explicit preflight first and persist it into runtime state.
- Load testnet account snapshot and testnet exchange metadata.
- If there is an accepted allocation, build preview from that intent.
- If there is no accepted allocation, build a clearly labeled fallback preview with `preview_source=no_signal_fallback` so payload construction and validation are still exercised.
- Persist `execution_mode`, `testnet_preflight`, `validated_order_preview`, and a compact precheck summary explaining candidate/allocation/non-execution context.
- Make the summary explicitly distinguish `no_signal_no_execution` from `validation_failed` / `interface_failed` style outcomes so user-visible behavior matches the spec.
- Do not add continuous loop behavior.

- [ ] **Step 4: Re-run the focused integration tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k testnet_phase1`
Expected: PASS.

- [ ] **Step 5: Commit the main-cycle slice**

```bash
git add trading_system/app/main.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: wire phase1 testnet runtime preview"
```

### Task 6: Document the Phase 1 operator flow completely

**Files:**
- Create: `trading_system/docs/TESTNET_PHASE1_RUNBOOK.md`
- Modify: `trading_system/README.md`

- [ ] **Step 1: Write the runbook and README updates**

Documentation must include:
- env file path: `/home/cn/.local/secrets/binance-testnet.env`
- required env vars and sample one-shot command
- explicit statement that Phase 1 keeps `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0` by default
- account identifier / permission check result reporting
- server-time check
- timestamp skew / `recvWindow` check result
- account-mode preflight expectations
- production-endpoint assertion passed
- reported Phase 1 guardrails actually implemented in this slice: allowlist, max order notional USDT, max open positions, and any implemented leverage-related guardrail if present
- where to find `validated_order_preview` and `testnet_preflight`
- manual troubleshooting order: endpoint → credentials → position mode → metadata → preview validation
- manual handling note for unsupported / ambiguous account preflight failures
- a short note that continuous-running entry/path is **deliberately deferred to Phase 2 by this approved minimal-delivery scope**, so this runbook covers one-shot Phase 1 only

- [ ] **Step 2: Run focused documentation sanity checks**

Run: `grep -RIn "validated_order_preview\|TRADING_EXECUTION_MODE=testnet\|TRADING_TESTNET_ORDER_SUBMISSION_ENABLED\|binance-testnet.env\|recvWindow\|server-time\|production-endpoint assertion" trading_system/README.md trading_system/docs/TESTNET_PHASE1_RUNBOOK.md`
Expected: matching lines in both files.

- [ ] **Step 3: Commit the docs slice**

```bash
git add trading_system/README.md trading_system/docs/TESTNET_PHASE1_RUNBOOK.md
git commit -m "docs: add phase1 testnet runbook"
```

## Suggested commit order

1. `feat: add phase1 testnet config guardrails`
2. `feat: add futures testnet preflight and account loader`
3. `feat: add testnet preview validation and payload mapping`
4. `feat: add phase1 testnet preview executor`
5. `feat: wire phase1 testnet runtime preview`
6. `docs: add phase1 testnet runbook`

## Final implementation verification before claiming completion

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_testnet_config.py trading_system/tests/test_testnet_account_loader.py trading_system/tests/test_testnet_preview.py trading_system/tests/test_executor.py -k 'testnet or preview' trading_system/tests/test_main_v2_cycle.py -k testnet_phase1
```
Expected: all focused Phase 1 testnet tests pass.

Run:
```bash
grep -RIn "validated_order_preview\|TRADING_EXECUTION_MODE=testnet\|TRADING_TESTNET_ORDER_SUBMISSION_ENABLED\|binance-testnet.env\|recvWindow\|server-time\|production-endpoint assertion" trading_system/README.md trading_system/docs/TESTNET_PHASE1_RUNBOOK.md
```
Expected: docs reference the new Phase 1 flow, preview-only default, env file path, and preflight checks.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-03-28-binance-testnet-phase1-minimal.md`. Ready to execute after plan review approval. Operational worktree / Codex launch steps should be handled separately from this product implementation plan.
