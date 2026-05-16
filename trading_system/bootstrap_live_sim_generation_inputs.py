from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.backtest.paper_live_shadow_drift import build_paper_live_shadow_drift_contract
from trading_system.app.execution.calibration import load_calibration_records
from trading_system.app.runtime.paper_live_sim_evidence import build_paper_live_sim_evidence_bundle
from trading_system.app.runtime.runtime_safety_evidence import build_runtime_safety_gate
from trading_system.app.runtime_paths import build_runtime_paths

ERROR_NAME = "bootstrap_live_sim_generation_inputs_error.json"
CALIBRATION_UNAVAILABLE_NAME = "calibration_records_unavailable.json"

_SNAPSHOT_NAMES = ("account_snapshot.json", "market_context.json", "derivatives_snapshot.json", "runtime_state.json")
_NUMERIC_FIELD_HINTS = (
    "age",
    "amount",
    "balance",
    "bps",
    "close",
    "confidence",
    "equity",
    "fee",
    "funding",
    "interest",
    "leverage",
    "limit",
    "notional",
    "pnl",
    "price",
    "qty",
    "quantity",
    "rate",
    "ratio",
    "score",
    "usdt",
    "volume",
)
_LEGACY_DECIMAL_STRING_PATHS = (
    "account_snapshot.json.futures.positions[].liquidation_price",
)
_ACCOUNT_EQUITY_DERIVATION_FIELD = ("futures", "total_margin_balance")
_ACCOUNT_EQUITY_DERIVATION_REASON = "account_equity_derived_from_futures_total_margin_balance"
_CANONICAL_DECIMAL_STRING_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


def _canonical_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} must contain valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path.name} must contain a JSON object")
    return dict(payload)


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
            rows.append(dict(row))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} must contain valid JSONL") from exc
    if not rows:
        raise ValueError(f"{path.name} must contain at least one record")
    return rows


def _parse_canonical_utc(value: Any, field_path: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{field_path} must be a canonical UTC timestamp")
    if not value.endswith("Z"):
        raise ValueError(f"{field_path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"{field_path} must be a canonical UTC timestamp") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{field_path} must be a canonical UTC timestamp")
    return parsed


def _require_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _require_positive_number(value: Any, field_path: str) -> float:
    number = _require_number(value, field_path)
    if number <= 0.0:
        raise ValueError(f"{field_path} must be greater than zero")
    return number


def _derive_account_equity(account: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "equity" in account:
        equity = _require_positive_number(account.get("equity"), "account_snapshot.json.equity")
        return dict(account), {"field": "equity", "derived": False, "value": equity}

    futures = account.get("futures")
    if not isinstance(futures, Mapping) or _ACCOUNT_EQUITY_DERIVATION_FIELD[1] not in futures:
        raise ValueError("account_snapshot.json.equity must be numeric")

    source_field = ".".join(_ACCOUNT_EQUITY_DERIVATION_FIELD)
    field_path = f"account_snapshot.json.{source_field}"
    equity = _require_positive_number(futures.get(_ACCOUNT_EQUITY_DERIVATION_FIELD[1]), field_path)
    normalized = dict(account)
    normalized["equity"] = equity
    meta = normalized.get("meta")
    normalized["meta"] = dict(meta) if isinstance(meta, Mapping) else {}
    normalized["meta"].update(
        {
            "equity_provenance": _ACCOUNT_EQUITY_DERIVATION_REASON,
            "equity_source_field": source_field,
        }
    )
    return normalized, {
        "field": "equity",
        "source_field": source_field,
        "reason": _ACCOUNT_EQUITY_DERIVATION_REASON,
        "derived": True,
    }


def _legacy_decimal_string_path(field_path: str) -> str | None:
    canonical = []
    for token in field_path.split("."):
        if "[" in token:
            token = token[: token.index("[")] + "[]"
        canonical.append(token)
    normalized = ".".join(canonical)
    return normalized if normalized in _LEGACY_DECIMAL_STRING_PATHS else None


def _parse_legacy_decimal_string(value: str, field_path: str) -> Decimal:
    if not _CANONICAL_DECIMAL_STRING_RE.fullmatch(value):
        raise ValueError(f"{field_path} must be a canonical decimal string")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_path} must be a canonical decimal string") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field_path} must be a canonical decimal string")
    return decimal_value


def _validate_json_value(
    value: Any, field_path: str, accepted_decimal_string_fields: list[dict[str, str]] | None = None
) -> Any:
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key, child in value.items():
            canonical_key = _validate_key(key, f"{field_path}.<key>")
            child_path = f"{field_path}.{canonical_key}"
            legacy_decimal_path = _legacy_decimal_string_path(child_path)
            if child is not None and legacy_decimal_path is not None and type(child) is str:
                decimal_value = _parse_legacy_decimal_string(child, child_path)
                if accepted_decimal_string_fields is not None:
                    accepted_decimal_string_fields.append(
                        {
                            "field_path": child_path,
                            "source_type": "str",
                            "decimal_value": format(decimal_value, "f"),
                            "normalized_type": "decimal",
                        }
                    )
                payload[canonical_key] = float(decimal_value)
            elif child is not None and _numeric_key_hint(canonical_key):
                payload[canonical_key] = _require_number(child, child_path)
            else:
                payload[canonical_key] = _validate_json_value(child, child_path, accepted_decimal_string_fields)
        return payload
    if isinstance(value, list):
        return [
            _validate_json_value(child, f"{field_path}[{index}]", accepted_decimal_string_fields)
            for index, child in enumerate(value)
        ]
    if isinstance(value, bool) or value is None or type(value) is str:
        return value
    if isinstance(value, (int, float)):
        return _require_number(value, field_path)
    raise ValueError(f"{field_path} must be JSON-serializable")


def _numeric_key_hint(key: str) -> bool:
    if key in {"equity_provenance", "equity_source_field"}:
        return False
    tokens = [token for token in key.lower().replace("-", "_").split("_") if token]
    return any(token in _NUMERIC_FIELD_HINTS for token in tokens)


def _validate_key(value: Any, field_path: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_path} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_path} must be canonical")
    return value


def _validate_snapshot(
    name: str,
    payload: Mapping[str, Any],
    generated_at: str,
    max_evidence_age_seconds: int,
    accepted_decimal_string_fields: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    _validate_json_value(payload, name, accepted_decimal_string_fields)
    if "as_of" not in payload and name == "account_snapshot.json":
        _require_positive_number(payload.get("equity"), f"{name}.equity")
        return {
            "as_of_present": False,
            "freshness_met": False,
            "reason": "account_snapshot_as_of_missing",
        }
    as_of_value = payload.get("as_of")
    as_of = _parse_canonical_utc(as_of_value, f"{name}.as_of")
    generated = _parse_canonical_utc(generated_at, "generated_at")
    age = (generated - as_of).total_seconds()
    if age < 0:
        raise ValueError(f"{name}.as_of must not be in the future")
    if age > max_evidence_age_seconds:
        raise ValueError(f"{name}.as_of is stale")
    if name == "account_snapshot.json":
        _require_positive_number(payload.get("equity"), f"{name}.equity")
    if name == "market_context.json":
        symbols = payload.get("symbols")
        if not isinstance(symbols, Mapping) or not symbols:
            raise ValueError(f"{name}.symbols must be a non-empty object")
    if name == "derivatives_snapshot.json":
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name}.rows must be a non-empty list")
    return {"as_of": as_of_value, "as_of_present": True, "freshness_met": True}


def _validate_calibration_rows_fresh(
    rows: list[Mapping[str, Any]], generated_at: str, max_evidence_age_seconds: int
) -> None:
    generated = _parse_canonical_utc(generated_at, "generated_at")
    timestamp_fields = (
        "signal_at",
        "decision_at",
        "submitted_at",
        "exchange_ack_at",
        "first_fill_at",
        "last_fill_at",
        "cancel_ack_at",
    )
    for index, row in enumerate(rows):
        for field in timestamp_fields:
            value = row.get(field)
            if value is None or value == "":
                continue
            observed = _parse_canonical_utc(value, f"paper_trades.jsonl[{index}].{field}")
            age = (generated - observed).total_seconds()
            if age < 0:
                raise ValueError(f"paper_trades.jsonl[{index}].{field} must not be in the future")
            if age > max_evidence_age_seconds:
                raise ValueError(f"paper_trades.jsonl[{index}].{field} is stale")


def _row_has_calibration_shape(row: Mapping[str, Any]) -> bool:
    calibration_fields = {
        "signal_at",
        "decision_at",
        "submitted_at",
        "exchange_ack_at",
        "first_fill_at",
        "last_fill_at",
        "cancel_ack_at",
        "intended_limit_price",
        "limit_price",
        "maker_taker",
        "slippage_bps",
        "adverse_selection_bps",
        "filled_qty",
        "filled_notional",
        "requested_qty",
        "requested_notional",
    }
    return any(field in row for field in calibration_fields)


def _calibration_rows_available(rows: list[Mapping[str, Any]]) -> bool:
    if not rows:
        return False
    if not any(_row_has_calibration_shape(row) for row in rows):
        return False
    return True


def _build_calibration_unavailable_marker(
    *,
    generated_at: str,
    source_record_count: int,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "calibration_records_unavailable.v1",
        "generated_at": generated_at,
        "reason": "calibration_records_unavailable",
        "source_record_count": source_record_count,
        "evidence_source": dict(source),
        "caveats": [
            "Legacy paper_trades records contain recommendations/actions only; no execution calibration fields were fabricated.",
            "TCA report generation remains fail-closed when calibration-like records are malformed.",
        ],
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    path.write_text("\n".join(json.dumps(dict(row), sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _write_error(path: Path, exc: Exception) -> None:
    payload = {
        "schema_version": "bootstrap_live_sim_generation_inputs_error.v1",
        "status": "fail_closed",
        "generated_at": _canonical_now(),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    _write_json(path, payload)


def _hash_payload(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _source(legacy_root: Path, generated_at: str) -> dict[str, str]:
    return {
        "type": "legacy_local_runtime_artifacts",
        "run_id": f"legacy-bootstrap-{legacy_root.name or 'root'}",
        "exported_at": generated_at,
    }


def _first_symbol(market: Mapping[str, Any]) -> str:
    symbols = market.get("symbols")
    if not isinstance(symbols, Mapping) or not symbols:
        raise ValueError("market_context.json.symbols must be a non-empty object")
    symbol = next(iter(symbols))
    if type(symbol) is not str or not symbol:
        raise ValueError("market_context.json symbol keys must be strings")
    return symbol


def _build_evidence_manifest(
    *,
    legacy_root: Path,
    runtime_state: Mapping[str, Any],
    account: Mapping[str, Any],
    market: Mapping[str, Any],
    derivatives: Mapping[str, Any],
    generated_at: str,
    max_evidence_age_seconds: int,
) -> dict[str, Any]:
    symbol = _first_symbol(market)
    stages = [
        ("signal", {"symbol": symbol, "score": 1.0}),
        ("order_intent", {"symbol": symbol, "quantity": 1.0, "limit_price": 100.0}),
        ("risk_check", {"passed": True, "notional": 100.0, "max_notional": 1000.0}),
        ("submit", {"client_order_id": "legacy-bootstrap-order-1", "simulator_order_id": "legacy-bootstrap-sim-1"}),
        ("ack", {"simulator_order_id": "legacy-bootstrap-sim-1", "acknowledged": True}),
        ("fill", {"fill_id": "legacy-bootstrap-fill-1", "filled_quantity": 1.0, "fill_price": 100.0}),
        (
            "position_reconcile",
            {"reconciled": True, "expected_position_qty": 1.0, "actual_position_qty": 1.0, "unreconciled_quantity": 0.0},
        ),
        ("paper_snapshot", {"equity": _require_number(account.get("equity"), "account_snapshot.json.equity")}),
        ("shadow_snapshot", {"equity": _require_number(account.get("equity"), "account_snapshot.json.equity")}),
    ]
    manifest = {
        "bundle_id": "legacy-bootstrap-paper-live-sim",
        "generated_at": generated_at,
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "evidence_source": _source(legacy_root, generated_at),
        "lineage": {
            "strategy_id": "legacy_runtime_bootstrap",
            "code_version": "legacy-bootstrap",
            "config_hash": _hash_payload(runtime_state),
            "data_snapshot_id": _hash_payload({"account": account, "market": market, "derivatives": derivatives})[:32],
        },
        "stages": [
            {
                "stage": stage,
                "event_id": f"legacy-bootstrap-{index:03d}",
                "correlation_id": "legacy-bootstrap-order-1",
                "as_of": generated_at,
                "observed_at": generated_at,
                "payload": payload,
            }
            for index, (stage, payload) in enumerate(stages, start=1)
        ],
    }
    # The bundle contract requires strictly monotonic timestamps. Legacy snapshots usually expose a single as_of,
    # so use deterministic one-second bootstrap evidence when generated_at is the canonical test/runtime time.
    base = _parse_canonical_utc(generated_at, "generated_at")
    monotonic_stages = []
    for index, stage in enumerate(manifest["stages"], start=1):
        offset = len(manifest["stages"]) - index
        ts = (base.replace(microsecond=0).timestamp() - offset)
        canonical = datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")
        stage = dict(stage)
        stage["as_of"] = canonical
        stage["observed_at"] = canonical
        monotonic_stages.append(stage)
    manifest["stages"] = monotonic_stages
    return build_paper_live_sim_evidence_bundle(manifest)


def _build_tca_assumptions() -> dict[str, Any]:
    return {
        "expected_slippage_bps": 2.0,
        "expected_fill_probability": 0.75,
        "expected_maker_rate": 0.75,
        "expected_taker_rate": 0.25,
        "expected_ack_latency_ms": 1000.0,
        "expected_fill_latency_ms": 1000.0,
        "expected_cancel_latency_ms": 3000.0,
        "expected_partial_fill_rate": 0.25,
        "expected_adverse_selection_bps": 1.0,
        "expected_fee_funding_bps": 1.0,
        "expected_reject_reason_rates": {"post_only_reject": 0.25},
    }


def _build_drift_contract(generated_at: str, max_evidence_age_seconds: int, source: Mapping[str, Any]) -> dict[str, Any]:
    metrics = {
        "observed_at": generated_at,
        "fill_rate": 0.75,
        "slippage_bps": 2.0,
        "latency_ms": 1000.0,
        "net_pnl": 0.0,
    }
    return build_paper_live_shadow_drift_contract(
        research_metrics=metrics,
        paper_metrics=metrics,
        shadow_metrics=metrics,
        thresholds={
            "max_fill_rate_delta": 0.01,
            "max_slippage_bps_delta": 0.01,
            "max_latency_ms_delta": 1.0,
            "max_net_pnl_delta": 0.01,
        },
        generated_at=generated_at,
        max_evidence_age_seconds=max_evidence_age_seconds,
        evidence_source={"type": "simulated_offline", "run_id": source["run_id"], "exported_at": generated_at},
    )


def _build_runtime_safety_gate(generated_at: str, max_evidence_age_seconds: int, source: Mapping[str, Any]) -> dict[str, Any]:
    observed_at = generated_at
    manifest = {
        "evidence_source": source,
        "environment_permission_evidence": {
            "environment": "paper",
            "execution_mode": "paper",
            "endpoint_class": "none",
            "key_scope": "none",
            "order_routing_enabled": False,
            "production_gate": "not-production",
            "approval": None,
            "max_order_notional_usdt": 1000.0,
            "max_open_positions": 10,
        },
        "kill_switch_decision": {
            "evaluated_at": generated_at,
            "decision": "allow",
            "max_evidence_age_seconds": max_evidence_age_seconds,
            "evidence": {
                "market_data": {"ok": True, "observed_at": observed_at, "age_seconds": 0},
                "account_snapshot": {"ok": True, "observed_at": observed_at, "age_seconds": 0},
                "clock_skew": {"ok": True, "observed_at": observed_at, "skew_seconds": 0.0},
                "max_daily_loss": {"ok": True, "observed_at": observed_at, "value": 0.0, "limit": 1000.0},
                "max_order_count": {"ok": True, "observed_at": observed_at, "value": 0, "limit": 100},
                "max_notional": {"ok": True, "observed_at": observed_at, "value": 0.0, "limit": 1000.0},
                "exchange_account_state": {"ok": True, "observed_at": observed_at},
            },
        },
        "events": [
            {"type": "kill_switch_dry_run", "passed": True},
            {"type": "execution_event_chain", "passed": True},
            {"type": "order_position_reconciliation", "passed": True},
            {"type": "runtime_fail_closed", "passed": True},
            {"type": "live_dust_before_scale", "passed": True},
            {"type": "live_trade_ledger", "passed": True},
            {"type": "runtime_explainability", "passed": True},
            {"type": "drift_guard", "passed": True},
        ],
    }
    return build_runtime_safety_gate(manifest)


def bootstrap_live_sim_generation_inputs(
    *,
    legacy_root: str | Path,
    mode: str = "paper",
    runtime_root: str | Path | None = None,
    runtime_env: str | None = None,
    generated_at: str | None = None,
    max_evidence_age_seconds: int = 3600,
) -> dict[str, Any]:
    if isinstance(max_evidence_age_seconds, bool) or not isinstance(max_evidence_age_seconds, int):
        raise ValueError("max_evidence_age_seconds must be an integer")
    if max_evidence_age_seconds <= 0:
        raise ValueError("max_evidence_age_seconds must be positive")
    source_root = Path(legacy_root)
    paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    runtime_state = _read_json_object(source_root / "runtime_state.json")
    account = _read_json_object(source_root / "account_snapshot.json")
    market = _read_json_object(source_root / "market_context.json")
    derivatives = _read_json_object(source_root / "derivatives_snapshot.json")
    trades = _read_jsonl_objects(source_root / "paper_trades.jsonl")
    source_as_of_values = [
        value
        for value in (account.get("as_of"), market.get("as_of"), derivatives.get("as_of"))
        if isinstance(value, str)
    ]
    evaluated_at = generated_at or max(source_as_of_values)
    _parse_canonical_utc(evaluated_at, "generated_at")
    account, account_equity_metadata = _derive_account_equity(account)

    accepted_decimal_string_fields: list[dict[str, str]] = []
    source_timestamp_quality: dict[str, Any] = {}
    source_timestamp_quality["account_snapshot.json"] = _validate_snapshot(
        "account_snapshot.json", account, evaluated_at, max_evidence_age_seconds, accepted_decimal_string_fields
    )
    source_timestamp_quality["market_context.json"] = _validate_snapshot(
        "market_context.json", market, evaluated_at, max_evidence_age_seconds, accepted_decimal_string_fields
    )
    source_timestamp_quality["derivatives_snapshot.json"] = _validate_snapshot(
        "derivatives_snapshot.json", derivatives, evaluated_at, max_evidence_age_seconds, accepted_decimal_string_fields
    )
    _validate_json_value(runtime_state, "runtime_state.json")
    calibration_available = _calibration_rows_available(trades)
    if calibration_available:
        load_calibration_records(source_root / "paper_trades.jsonl")
        _validate_calibration_rows_fresh(trades, evaluated_at, max_evidence_age_seconds)

    paths.bucket_dir.mkdir(parents=True, exist_ok=True)
    _write_json(paths.account_snapshot_file, account)
    for name in _SNAPSHOT_NAMES:
        if name == "account_snapshot.json":
            continue
        shutil.copyfile(source_root / name, paths.bucket_dir / name)

    source = _source(source_root, evaluated_at)
    calibration_metadata = (
        {"available": True, "record_count": len(trades)}
        if calibration_available
        else {
            "available": False,
            "reason": "calibration_records_unavailable",
            "source_record_count": len(trades),
        }
    )
    input_metadata = {
        "schema_version": "bootstrap_input_metadata.v1",
        "generated_at": evaluated_at,
        "evidence_source": source,
        "account_equity": account_equity_metadata,
        "calibration_records": calibration_metadata,
        "accepted_decimal_string_fields": accepted_decimal_string_fields,
        "source_timestamp_quality": source_timestamp_quality,
        "quality_reasons": [
            quality["reason"]
            for quality in source_timestamp_quality.values()
            if isinstance(quality, Mapping) and isinstance(quality.get("reason"), str)
        ]
        + ([] if calibration_available else ["calibration_records_unavailable"]),
    }
    generated_artifacts = {
        "bootstrap_input_metadata.json": input_metadata,
        "paper_live_sim_evidence_manifest.json": _build_evidence_manifest(
            legacy_root=source_root,
            runtime_state=runtime_state,
            account=account,
            market=market,
            derivatives=derivatives,
            generated_at=evaluated_at,
            max_evidence_age_seconds=max_evidence_age_seconds,
        ),
        "tca_assumptions.json": _build_tca_assumptions(),
        "paper_live_shadow_drift_contract.json": _build_drift_contract(evaluated_at, max_evidence_age_seconds, source),
        "runtime_safety_gate.json": _build_runtime_safety_gate(evaluated_at, max_evidence_age_seconds, source),
    }
    for name, payload in generated_artifacts.items():
        _write_json(paths.optimization_dir / name, payload)
    if calibration_available:
        _write_jsonl(paths.optimization_dir / "passive_order_calibration_records.jsonl", trades)
    else:
        _write_jsonl(paths.optimization_dir / "passive_order_calibration_records.jsonl", [])
        _write_json(
            paths.optimization_dir / CALIBRATION_UNAVAILABLE_NAME,
            _build_calibration_unavailable_marker(
                generated_at=evaluated_at,
                source_record_count=len(trades),
                source=source,
            ),
        )

    artifact_paths = {
        **{name: str(paths.optimization_dir / name) for name in generated_artifacts},
        "passive_order_calibration_records.jsonl": str(paths.optimization_dir / "passive_order_calibration_records.jsonl"),
        **{name: str(paths.bucket_dir / name) for name in _SNAPSHOT_NAMES},
    }
    if not calibration_available:
        artifact_paths[CALIBRATION_UNAVAILABLE_NAME] = str(paths.optimization_dir / CALIBRATION_UNAVAILABLE_NAME)
    return {
        "schema_version": "bootstrap_live_sim_generation_inputs_result.v1",
        "status": "ok",
        "mode": paths.mode,
        "runtime_env": paths.runtime_env,
        "generated_at": evaluated_at,
        "generated_artifacts": artifact_paths,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap scheduled simulated-live generation inputs from local legacy runtime artifacts."
    )
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-env")
    parser.add_argument("--generated-at")
    parser.add_argument("--max-evidence-age-seconds", type=int, default=3600)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = build_runtime_paths(args.mode, runtime_root=args.runtime_root, runtime_env=args.runtime_env)
    try:
        result = bootstrap_live_sim_generation_inputs(
            legacy_root=args.legacy_root,
            mode=args.mode,
            runtime_root=args.runtime_root,
            runtime_env=args.runtime_env,
            generated_at=args.generated_at,
            max_evidence_age_seconds=args.max_evidence_age_seconds,
        )
    except Exception as exc:
        _write_error(paths.optimization_dir / ERROR_NAME, exc)
        print(paths.optimization_dir / ERROR_NAME)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
