from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "paper_live_shadow_drift_contract.v1"
FILENAME = "paper_live_shadow_drift_contract.json"
MODE = "offline_simulated"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_METRIC_FIELDS = ("fill_rate", "slippage_bps", "latency_ms", "net_pnl")
_THRESHOLD_FIELDS = (
    "max_fill_rate_delta",
    "max_slippage_bps_delta",
    "max_latency_ms_delta",
    "max_net_pnl_delta",
)


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_timestamp(value: Any, field_path: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{field_path}_not_string")
    if not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field_path}_noncanonical_timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _require_mapping(value: Any, field_path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_path}_not_object")
    return value


def _safe_identifier(value: Any, field_path: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_path}_not_string")
    if not value:
        raise ValueError(f"{field_path}_blank")
    if value != value.strip():
        raise ValueError(f"{field_path}_noncanonical")
    if _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path}_not_identifier")
    return value


def _number(value: Any, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path}_not_number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_path}_not_finite")
    return parsed


def _non_negative_number(value: Any, field_path: str) -> float:
    parsed = _number(value, field_path)
    if parsed < 0.0:
        raise ValueError(f"{field_path}_negative")
    return parsed


def _metric_snapshot(
    value: Any,
    *,
    stage: str,
    generated_at: datetime,
    max_evidence_age_seconds: float,
) -> dict[str, Any]:
    raw = _require_mapping(value, stage)
    unknown_fields = sorted(set(raw) - {"observed_at", *_METRIC_FIELDS})
    if unknown_fields:
        raise ValueError(f"{stage}.unknown_field: " + ", ".join(unknown_fields))
    observed_at = _parse_timestamp(raw.get("observed_at"), f"{stage}.observed_at")
    if observed_at > generated_at:
        raise ValueError(f"{stage}.evidence_future")
    if (generated_at - observed_at).total_seconds() > max_evidence_age_seconds:
        raise ValueError(f"{stage}.evidence_stale")
    fill_rate = _number(raw.get("fill_rate"), f"{stage}.fill_rate")
    if fill_rate < 0.0 or fill_rate > 1.0:
        raise ValueError(f"{stage}.fill_rate_out_of_range")
    latency_ms = _non_negative_number(raw.get("latency_ms"), f"{stage}.latency_ms")
    return {
        "observed_at": raw["observed_at"],
        "fill_rate": fill_rate,
        "slippage_bps": _number(raw.get("slippage_bps"), f"{stage}.slippage_bps"),
        "latency_ms": latency_ms,
        "net_pnl": _number(raw.get("net_pnl"), f"{stage}.net_pnl"),
    }


def _thresholds(value: Any) -> dict[str, float]:
    raw = _require_mapping(value, "thresholds")
    unknown_fields = sorted(set(raw) - set(_THRESHOLD_FIELDS))
    if unknown_fields:
        raise ValueError("thresholds.unknown_field: " + ", ".join(unknown_fields))
    return {field: _non_negative_number(raw.get(field), f"thresholds.{field}") for field in _THRESHOLD_FIELDS}


def _evidence_source(value: Any) -> dict[str, str]:
    raw = _require_mapping(value, "evidence_source")
    unknown_fields = sorted(set(raw) - {"type", "run_id", "exported_at"})
    if unknown_fields:
        raise ValueError("evidence_source.unknown_field: " + ", ".join(unknown_fields))
    source_type = _safe_identifier(raw.get("type"), "evidence_source.type")
    if source_type != "simulated_offline":
        raise ValueError("drift_evidence_source_not_simulated_offline")
    source = {"type": source_type}
    for optional_field in ("run_id", "exported_at"):
        if optional_field in raw and raw[optional_field] is not None:
            source[optional_field] = _safe_identifier(raw[optional_field], f"evidence_source.{optional_field}")
    return source


def _comparison(candidate: Mapping[str, float], baseline: Mapping[str, float], thresholds: Mapping[str, float]) -> dict[str, Any]:
    fill_rate_delta = candidate["fill_rate"] - baseline["fill_rate"]
    slippage_bps_delta = candidate["slippage_bps"] - baseline["slippage_bps"]
    latency_ms_delta = candidate["latency_ms"] - baseline["latency_ms"]
    net_pnl_delta = candidate["net_pnl"] - baseline["net_pnl"]
    checks = {
        "fill_rate_drift_met": abs(fill_rate_delta) <= thresholds["max_fill_rate_delta"],
        "slippage_drift_met": abs(slippage_bps_delta) <= thresholds["max_slippage_bps_delta"],
        "latency_drift_met": abs(latency_ms_delta) <= thresholds["max_latency_ms_delta"],
        "net_pnl_drift_met": abs(net_pnl_delta) <= thresholds["max_net_pnl_delta"],
    }
    return {
        "fill_rate_delta": fill_rate_delta,
        "slippage_bps_delta": slippage_bps_delta,
        "latency_ms_delta": latency_ms_delta,
        "net_pnl_delta": net_pnl_delta,
        "checks": checks,
        "material_drift": not all(checks.values()),
    }


def build_paper_live_shadow_drift_contract(
    *,
    research_metrics: Mapping[str, Any],
    paper_metrics: Mapping[str, Any],
    shadow_metrics: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    generated_at: str,
    max_evidence_age_seconds: float,
    evidence_source: Mapping[str, Any],
) -> dict[str, Any]:
    generated = _parse_timestamp(generated_at, "drift_generated_at")
    max_age = _non_negative_number(max_evidence_age_seconds, "max_evidence_age_seconds")
    parsed_thresholds = _thresholds(thresholds)
    source = _evidence_source(evidence_source)
    research = _metric_snapshot(
        research_metrics,
        stage="research",
        generated_at=generated,
        max_evidence_age_seconds=max_age,
    )
    paper = _metric_snapshot(
        paper_metrics,
        stage="paper",
        generated_at=generated,
        max_evidence_age_seconds=max_age,
    )
    shadow = _metric_snapshot(
        shadow_metrics,
        stage="shadow",
        generated_at=generated,
        max_evidence_age_seconds=max_age,
    )
    paper_comparison = _comparison(paper, research, parsed_thresholds)
    shadow_comparison = _comparison(shadow, research, parsed_thresholds)
    paper_shadow_comparison = _comparison(paper, shadow, parsed_thresholds)
    material_drift_absent = (
        not paper_comparison["material_drift"]
        and not shadow_comparison["material_drift"]
        and not paper_shadow_comparison["material_drift"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "fail_closed": True,
        "generated_at": generated_at,
        "max_evidence_age_seconds": max_age,
        "evidence_source": source,
        "thresholds": parsed_thresholds,
        "research": research,
        "paper": paper,
        "shadow": shadow,
        "comparisons": {
            "paper_vs_research": paper_comparison,
            "shadow_vs_research": shadow_comparison,
            "paper_vs_shadow": paper_shadow_comparison,
        },
        "checks": {
            "paper_live_shadow_drift_contract_present": True,
            "paper_live_shadow_drift_contract_schema_valid": True,
            "paper_live_shadow_material_drift_absent": material_drift_absent,
            "material_drift_absent": material_drift_absent,
            "offline_simulated_evidence_only": True,
            "fail_closed": True,
        },
        "decision": "drift_within_contract" if material_drift_absent else "reject_for_live_promotion",
        "reasons": [] if material_drift_absent else ["paper_live_shadow_material_drift"],
    }


def validate_paper_live_shadow_drift_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("drift_contract_not_object")
    allowed_fields = {
        "schema_version",
        "mode",
        "fail_closed",
        "generated_at",
        "max_evidence_age_seconds",
        "evidence_source",
        "thresholds",
        "research",
        "paper",
        "shadow",
        "comparisons",
        "checks",
        "decision",
        "reasons",
    }
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError("drift_contract_unknown_field: " + ", ".join(unknown_fields))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("drift_contract_schema_version_invalid")
    if payload.get("mode") != MODE:
        raise ValueError("drift_contract_mode_not_offline_simulated")
    if payload.get("fail_closed") is not True:
        raise ValueError("drift_contract_fail_closed_not_true")
    rebuilt = build_paper_live_shadow_drift_contract(
        research_metrics=_require_mapping(payload.get("research"), "research"),
        paper_metrics=_require_mapping(payload.get("paper"), "paper"),
        shadow_metrics=_require_mapping(payload.get("shadow"), "shadow"),
        thresholds=_require_mapping(payload.get("thresholds"), "thresholds"),
        generated_at=payload.get("generated_at"),
        max_evidence_age_seconds=payload.get("max_evidence_age_seconds"),
        evidence_source=_require_mapping(payload.get("evidence_source"), "evidence_source"),
    )
    for field in ("comparisons", "checks", "decision", "reasons"):
        if payload.get(field) != rebuilt[field]:
            raise ValueError(f"drift_contract_{field}_mismatch")
    return rebuilt


def write_paper_live_shadow_drift_contract(root: str | Path, **kwargs: Any) -> dict[str, Any]:
    contract = build_paper_live_shadow_drift_contract(**kwargs)
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    (root_path / FILENAME).write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract
