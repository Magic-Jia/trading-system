from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


L2_SCHEMA_VERSION = "l2_longitudinal_replay_calibration.v1"
L2_FILENAME = "l2_longitudinal_replay_calibration.json"
L2_DEPTH_SNAPSHOT_FILENAME = "local_l2_order_book_snapshot.json"
EXECUTION_RACE_SCHEMA_VERSION = "execution_race_evidence.v1"
EXECUTION_RACE_FILENAME = "execution_race_evidence.json"
SOURCE_MODE = "simulated_live_local"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ORDERING_STAGES = ("signal", "order_intent", "risk_check", "submit", "ack", "fill", "position_reconcile")
_DEPTH_TOKENS = ("l2", "depth", "book", "bids", "asks")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _generated_at(value: str | None) -> str:
    generated_at = value or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if type(generated_at) is not str or not _is_canonical_utc_timestamp(generated_at):
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return generated_at


def _load_source_bundle(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = Path(path)
    try:
        raw_bytes = source_path.read_bytes()
    except OSError as exc:
        raise ValueError("paper_live_sim_evidence_bundle.json cannot be read") from exc
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("paper_live_sim_evidence_bundle.json is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("paper_live_sim_evidence_bundle.json must be a JSON object")
    return payload, {
        "path": str(source_path),
        "bytes": len(raw_bytes),
        "sha256": _sha256_bytes(raw_bytes),
        "schema_version": payload.get("schema_version") if type(payload.get("schema_version")) is str else None,
    }


def _reason_codes(reasons: list[str]) -> list[str]:
    canonical = sorted(dict.fromkeys(reasons))
    for reason in canonical:
        if _REASON_CODE_RE.fullmatch(reason) is None:
            raise ValueError(f"reason code is not canonical: {reason}")
    return canonical


def _load_l2_depth_snapshot(source_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    snapshot_path = source_path.parent / L2_DEPTH_SNAPSHOT_FILENAME
    if not snapshot_path.exists():
        return None, None
    try:
        raw_bytes = snapshot_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME} is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME} must be a JSON object")
    return payload, {
        "path": str(snapshot_path),
        "bytes": len(raw_bytes),
        "sha256": _sha256_bytes(raw_bytes),
        "schema_version": payload.get("schema_version") if type(payload.get("schema_version")) is str else None,
    }


def _number(value: Any, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a number")
    parsed = float(value)
    if parsed <= 0.0:
        raise ValueError(f"{field_path} must be positive")
    return parsed


def _l2_replay_metrics_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") != "local_l2_order_book_snapshot.v1":
        raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME}.schema_version must be local_l2_order_book_snapshot.v1")
    books = snapshot.get("books")
    if not isinstance(books, list) or not books:
        raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME}.books must be a non-empty list")
    spreads: list[float] = []
    bid_depths: list[float] = []
    ask_depths: list[float] = []
    symbols: list[str] = []
    for index, book in enumerate(books):
        if not isinstance(book, Mapping):
            raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}] must be an object")
        symbol = book.get("symbol")
        if type(symbol) is not str or not symbol:
            raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].symbol must be a string")
        best_bid = _number(book.get("best_bid"), f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].best_bid")
        best_ask = _number(book.get("best_ask"), f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].best_ask")
        if best_ask <= best_bid:
            raise ValueError(f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].best_ask must exceed best_bid")
        spreads.append(_number(book.get("spread_bps"), f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].spread_bps"))
        bid_depths.append(_number(book.get("bid_depth_notional"), f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].bid_depth_notional"))
        ask_depths.append(_number(book.get("ask_depth_notional"), f"{L2_DEPTH_SNAPSHOT_FILENAME}.books[{index}].ask_depth_notional"))
        symbols.append(symbol)
    return {
        "depth_event_count": len(books),
        "source_snapshot": L2_DEPTH_SNAPSHOT_FILENAME,
        "symbols": sorted(symbols),
        "max_spread_bps": max(spreads),
        "min_bid_depth_notional": min(bid_depths),
        "min_ask_depth_notional": min(ask_depths),
        "calibrated": False,
    }


def _stages(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_stages = bundle.get("stages")
    if not isinstance(raw_stages, list):
        return []
    return [dict(stage) for stage in raw_stages if isinstance(stage, Mapping)]


def _stage_text(stage: Mapping[str, Any]) -> str:
    return json.dumps(stage, ensure_ascii=True, sort_keys=True, default=str).lower()


def _has_l2_depth_evidence(stages: list[Mapping[str, Any]]) -> bool:
    for stage in stages:
        stage_name = stage.get("stage")
        payload = stage.get("payload")
        haystack = " ".join(
            value
            for value in (
                stage_name if type(stage_name) is str else "",
                _stage_text(payload) if isinstance(payload, Mapping) else "",
            )
        ).lower()
        if any(token in haystack for token in _DEPTH_TOKENS):
            return True
    return False


def build_l2_longitudinal_replay_calibration(
    paper_live_sim_evidence_bundle_path: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    report_generated_at = _generated_at(generated_at)
    source_path = Path(paper_live_sim_evidence_bundle_path)
    bundle, source = _load_source_bundle(source_path)
    snapshot, snapshot_source = _load_l2_depth_snapshot(source_path)
    if snapshot is not None:
        replay_metrics = _l2_replay_metrics_from_snapshot(snapshot)
        status = "pass"
        decision = "accepted"
        reasons: list[str] = []
        has_depth = True
        source_artifact = L2_DEPTH_SNAPSHOT_FILENAME
        source["l2_depth_snapshot"] = snapshot_source
    else:
        stages = _stages(bundle)
        has_depth = _has_l2_depth_evidence(stages)
        if has_depth:
            status = "review"
            decision = "accepted_with_review"
            reasons = ["l2_depth_replay_metrics_require_review"]
            replay_metrics = {
                "depth_event_count": sum(1 for stage in stages if _has_l2_depth_evidence([stage])),
                "source_stage_count": len(stages),
                "calibrated": False,
            }
        else:
            status = "hold"
            decision = "hold"
            reasons = ["l2_depth_evidence_unavailable"]
            replay_metrics = None
        source_artifact = "paper_live_sim_evidence_bundle.json"

    return {
        "schema_version": L2_SCHEMA_VERSION,
        "artifact_id": "l2-longitudinal-replay-calibration",
        "generated_at": report_generated_at,
        "source_mode": SOURCE_MODE,
        "status": status,
        "decision": decision,
        "reason_codes": _reason_codes(reasons),
        "checks": {
            "l2_depth_evidence_present": has_depth,
            "replay_metrics_evidence_backed": replay_metrics is not None,
            "local_simulated_live_source_loaded": True,
        },
        "replay_metrics": replay_metrics,
        "source": source,
        "provenance": {
            "decision_policy": "fail_closed",
            "source_artifact": source_artifact,
            "source_mode": SOURCE_MODE,
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "exchange_api_calls": "forbidden",
                "credential_use": "forbidden",
            },
        },
    }


def _stage_name(stage: Mapping[str, Any]) -> str | None:
    value = stage.get("stage")
    return value if type(value) is str else None


def _correlation_id(stage: Mapping[str, Any]) -> str | None:
    value = stage.get("correlation_id")
    return value if type(value) is str and value.strip() == value and value else None


def _event_id(stage: Mapping[str, Any]) -> str | None:
    value = stage.get("event_id")
    return value if type(value) is str and value.strip() == value and value else None


def _stage_time(stage: Mapping[str, Any]) -> str | None:
    for field in ("as_of", "observed_at"):
        value = stage.get(field)
        if type(value) is str and _is_canonical_utc_timestamp(value):
            return value
    return None


def _payload(stage: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_payload = stage.get("payload")
    return raw_payload if isinstance(raw_payload, Mapping) else {}


def _event_well_formed(stage: Mapping[str, Any]) -> bool:
    name = _stage_name(stage)
    payload = _payload(stage)
    if name is None or _correlation_id(stage) is None or _event_id(stage) is None or _stage_time(stage) is None:
        return False
    if name == "risk_check" and payload.get("passed") is not True:
        return False
    if name == "ack" and payload.get("acknowledged") is not True:
        return False
    if name == "fill":
        quantity = payload.get("filled_quantity", payload.get("quantity"))
        price = payload.get("fill_price", payload.get("price"))
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or float(quantity) <= 0.0:
            return False
        if isinstance(price, bool) or not isinstance(price, (int, float)) or float(price) <= 0.0:
            return False
    if name == "position_reconcile":
        unreconciled = payload.get("unreconciled_quantity")
        if payload.get("reconciled") is not True:
            return False
        if isinstance(unreconciled, bool) or not isinstance(unreconciled, (int, float)) or float(unreconciled) != 0.0:
            return False
    return True


def _terminal_state(stages: list[Mapping[str, Any]]) -> tuple[str, bool]:
    by_name = {_stage_name(stage): stage for stage in stages}
    fill = by_name.get("fill")
    reconcile = by_name.get("position_reconcile")
    if fill is None and reconcile is None:
        return "missing_terminal_evidence", False
    if fill is None or reconcile is None:
        return "terminal_incomplete", False
    if _event_well_formed(fill) and _event_well_formed(reconcile):
        return "filled_reconciled", True
    return "conflict", False


def _correlation_summary(correlation_id: str, stages: list[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, bool], list[str]]:
    reasons: list[str] = []
    event_ids = [_event_id(stage) for stage in stages if _event_id(stage) is not None]
    event_ids_unique = len(event_ids) == len(set(event_ids))
    if not event_ids_unique:
        reasons.append("duplicate_order_event")

    malformed = any(not _event_well_formed(stage) for stage in stages)
    if malformed:
        reasons.append("malformed_order_event")

    stage_names = [_stage_name(stage) for stage in stages]
    missing = [stage for stage in _ORDERING_STAGES if stage not in stage_names]
    if missing:
        reasons.append("ordering_evidence_incomplete")
    if "ack" in missing:
        reasons.append("missing_order_ack")

    stage_positions = [
        int(_ORDERING_STAGES.index(str(stage_name))) for stage_name in stage_names if stage_name in _ORDERING_STAGES
    ]
    order_stages_monotonic = stage_positions == sorted(stage_positions)
    if not order_stages_monotonic:
        reasons.append("order_stage_out_of_order")

    terminal_state, terminal_coherent = _terminal_state(stages)
    if not terminal_coherent:
        reasons.append("terminal_state_conflict")

    order_stage_names = [name for name in stage_names if name is not None]
    return (
        {
            "correlation_id": correlation_id,
            "first_stage": order_stage_names[0] if order_stage_names else None,
            "last_stage": order_stage_names[-1] if order_stage_names else None,
            "terminal_state": terminal_state,
            "stage_count": len(stages),
        },
        {
            "ordering_evidence_complete": not missing and bool(stages),
            "event_ids_unique": event_ids_unique,
            "order_stages_monotonic": order_stages_monotonic,
            "terminal_state_coherent": terminal_coherent,
        },
        reasons,
    )


def build_execution_race_evidence(
    paper_live_sim_evidence_bundle_path: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    report_generated_at = _generated_at(generated_at)
    bundle, source = _load_source_bundle(paper_live_sim_evidence_bundle_path)
    stages = [stage for stage in _stages(bundle) if _stage_name(stage) in _ORDERING_STAGES]
    reasons: list[str] = []
    missing_correlation = any(_correlation_id(stage) is None for stage in stages)
    if missing_correlation:
        reasons.append("correlation_ordering_incoherent")

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for stage in stages:
        correlation_id = _correlation_id(stage)
        if correlation_id is not None:
            grouped.setdefault(correlation_id, []).append(stage)
    if not grouped:
        reasons.append("ordering_evidence_incomplete")

    correlations: list[dict[str, Any]] = []
    group_checks: list[dict[str, bool]] = []
    for correlation_id in sorted(grouped):
        summary, checks, group_reasons = _correlation_summary(correlation_id, grouped[correlation_id])
        correlations.append(summary)
        group_checks.append(checks)
        reasons.extend(group_reasons)

    ordering_complete = bool(group_checks) and all(check["ordering_evidence_complete"] for check in group_checks)
    event_ids_unique = bool(group_checks) and all(check["event_ids_unique"] for check in group_checks)
    order_stages_monotonic = bool(group_checks) and all(check["order_stages_monotonic"] for check in group_checks)
    terminal_coherent = bool(group_checks) and all(check["terminal_state_coherent"] for check in group_checks)
    correlation_ids_consistent = not missing_correlation and bool(grouped)
    status = "hold" if reasons else "pass"
    decision = "hold" if reasons else "accepted"
    return {
        "schema_version": EXECUTION_RACE_SCHEMA_VERSION,
        "artifact_id": "execution-race-evidence",
        "generated_at": report_generated_at,
        "source_mode": SOURCE_MODE,
        "status": status,
        "decision": decision,
        "reason_codes": _reason_codes(reasons),
        "checks": {
            "ordering_evidence_complete": ordering_complete,
            "correlation_ids_consistent": correlation_ids_consistent,
            "event_ids_unique": event_ids_unique,
            "order_stages_monotonic": order_stages_monotonic,
            "terminal_state_coherent": terminal_coherent,
        },
        "correlations": correlations,
        "source": source,
        "provenance": {
            "decision_policy": "fail_closed",
            "source_artifact": "paper_live_sim_evidence_bundle.json",
            "source_mode": SOURCE_MODE,
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "exchange_api_calls": "forbidden",
                "credential_use": "forbidden",
            },
        },
    }


def write_l2_longitudinal_replay_calibration(
    output_path: str | Path,
    paper_live_sim_evidence_bundle_path: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_l2_longitudinal_replay_calibration(
        paper_live_sim_evidence_bundle_path,
        generated_at=generated_at,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def write_execution_race_evidence(
    output_path: str | Path,
    paper_live_sim_evidence_bundle_path: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_execution_race_evidence(paper_live_sim_evidence_bundle_path, generated_at=generated_at)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def _default_optimization_dir(runtime_root: str | Path, mode: str, runtime_env: str) -> Path:
    return Path(runtime_root) / mode / runtime_env / "optimization"


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--optimization-dir", default=None)
    parser.add_argument("--runtime-root", default="trading_system/data/runtime")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-env", default="paper")
    parser.add_argument("--source", default=None, help="Local paper_live_sim_evidence_bundle.json path")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    return parser


def _paths(args: argparse.Namespace, filename: str) -> tuple[Path, Path]:
    optimization_dir = (
        Path(args.optimization_dir)
        if args.optimization_dir is not None
        else _default_optimization_dir(args.runtime_root, args.mode, args.runtime_env)
    )
    source = Path(args.source) if args.source is not None else optimization_dir / "paper_live_sim_evidence_bundle.json"
    output = Path(args.output) if args.output is not None else optimization_dir / filename
    return source, output


def l2_main() -> None:
    parser = _parser("Generate local L2 longitudinal replay calibration evidence")
    args = parser.parse_args()
    source, output = _paths(args, L2_FILENAME)
    payload = write_l2_longitudinal_replay_calibration(output, source, generated_at=args.generated_at)
    print(
        "L2_LONGITUDINAL_REPLAY_CALIBRATION_JSON",
        json.dumps(
            {
                "output": str(output),
                "status": payload["status"],
                "reason_codes": payload["reason_codes"],
                "source_mode": payload["source_mode"],
            },
            sort_keys=True,
        ),
    )


def execution_race_main() -> None:
    parser = _parser("Generate local execution race evidence from simulated-live stages")
    args = parser.parse_args()
    source, output = _paths(args, EXECUTION_RACE_FILENAME)
    payload = write_execution_race_evidence(output, source, generated_at=args.generated_at)
    print(
        "EXECUTION_RACE_EVIDENCE_JSON",
        json.dumps(
            {
                "output": str(output),
                "status": payload["status"],
                "reason_codes": payload["reason_codes"],
                "source_mode": payload["source_mode"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "EXECUTION_RACE_FILENAME",
    "EXECUTION_RACE_SCHEMA_VERSION",
    "L2_FILENAME",
    "L2_SCHEMA_VERSION",
    "SOURCE_MODE",
    "build_execution_race_evidence",
    "build_l2_longitudinal_replay_calibration",
    "write_execution_race_evidence",
    "write_l2_longitudinal_replay_calibration",
]
