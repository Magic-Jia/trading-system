from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "longitudinal_promotion_decision_archive.v1"
FILENAME = "longitudinal_promotion_decision_archive.json"
PROMOTION_GATE_DECISION_SCHEMA_VERSION = "promotion_gate_decision.v1"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_VALID_DECISIONS = {"candidate_for_paper_promotion", "hold", "reject"}
_REQUIRED_SOURCE_MODE = {
    "mode": "simulated_live",
    "side_effect_boundary": "offline_local_filesystem_only",
    "real_orders": "forbidden",
    "testnet_orders": "forbidden",
    "exchange_api_calls": "forbidden",
    "credential_use": "forbidden",
}
_REQUIRED_PROVENANCE = {
    "decision_policy": "fail_closed",
    "promotion_scope": "paper_promotion_candidate_only",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_timestamp(value, "generated_at")
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    return parsed.astimezone(UTC)


def _load_decision(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
    if isinstance(value, (str, Path)):
        path = Path(value)
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            return None, {"path": str(path)}, "missing_artifact"
        source = {"path": str(path), "bytes": len(raw_bytes), "sha256": _sha256_bytes(raw_bytes)}
        try:
            payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, source, "malformed_artifact"
        if not isinstance(payload, dict):
            return None, source, "malformed_artifact"
        return payload, source, None
    if not isinstance(value, Mapping):
        return None, {}, "malformed_artifact"
    payload = dict(value)
    return payload, {"sha256": _sha256_bytes(_canonical_json_bytes(payload))}, None


def _string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field}_invalid")
        return []
    reasons: list[str] = []
    for item in value:
        if type(item) is not str or not item:
            errors.append(f"{field}_invalid")
            continue
        reasons.append(item)
    return reasons


def _mapping_field(payload: Mapping[str, Any], field: str, errors: list[str]) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        errors.append(f"{field}_invalid")
        return {}
    return value


def _validate_source_mode(payload: Mapping[str, Any], errors: list[str]) -> None:
    source_mode = _mapping_field(payload, "source_mode", errors)
    if dict(source_mode) != _REQUIRED_SOURCE_MODE:
        errors.append("source_mode_invalid")


def _validate_provenance(payload: Mapping[str, Any], errors: list[str]) -> None:
    provenance = _mapping_field(payload, "provenance", errors)
    for key, expected in _REQUIRED_PROVENANCE.items():
        if provenance.get(key) != expected:
            errors.append("provenance_invalid")
            break
    if type(provenance.get("input_artifact_count")) is not int or provenance.get("input_artifact_count", 0) < 2:
        errors.append("provenance_invalid")


def _decision_identity(payload: Mapping[str, Any]) -> str:
    identity_payload = {
        "schema_version": payload.get("schema_version"),
        "generated_at": payload.get("generated_at"),
        "decision": payload.get("decision"),
        "blocking_reasons": payload.get("blocking_reasons"),
        "included_artifact_identities": payload.get("included_artifact_identities"),
        "source_mode": payload.get("source_mode"),
        "provenance": payload.get("provenance"),
    }
    return _sha256_bytes(_canonical_json_bytes(identity_payload))


def _source_artifact_identities(payload: Mapping[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    identities = payload.get("included_artifact_identities")
    if not isinstance(identities, list) or not identities:
        errors.append("included_artifact_identities_invalid")
        return []
    normalized: list[dict[str, Any]] = []
    for item in identities:
        if not isinstance(item, Mapping):
            errors.append("included_artifact_identities_invalid")
            continue
        source = item.get("source")
        if not isinstance(source, Mapping) or type(source.get("sha256")) is not str or not source.get("sha256"):
            errors.append("included_artifact_identities_invalid")
        normalized.append(dict(item))
    return normalized


def _normalize_decision(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    payload, source, load_error = _load_decision(value)
    if load_error is not None:
        return None, {"source": source, "reason_codes": [load_error]}
    assert payload is not None

    errors: list[str] = []
    if payload.get("schema_version") != PROMOTION_GATE_DECISION_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    try:
        parsed_generated_at = _parse_timestamp(payload.get("generated_at"), "generated_at")
    except ValueError:
        errors.append("generated_at_invalid")
        parsed_generated_at = None
    decision = payload.get("decision")
    if decision not in _VALID_DECISIONS:
        errors.append("decision_invalid")
    blocking_reasons = _string_list(payload.get("blocking_reasons"), "blocking_reasons", errors)
    _validate_source_mode(payload, errors)
    _validate_provenance(payload, errors)
    source_identities = _source_artifact_identities(payload, errors)

    if errors:
        return None, {"source": source, "reason_codes": sorted(dict.fromkeys(errors))}

    assert type(decision) is str
    assert type(payload["generated_at"]) is str
    identity = _decision_identity(payload)
    return {
        "identity": identity,
        "generated_at": payload["generated_at"],
        "decision": decision,
        "blocking_reasons": blocking_reasons,
        "human_review_required": bool(payload.get("human_review_required")),
        "source_sha256": source["sha256"],
        "source_path": source.get("path"),
        "source_artifact_identities": source_identities,
        "_sort_at": parsed_generated_at,
    }, None


def _source_artifacts(decisions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for decision in decisions:
        artifact: dict[str, Any] = {
            "identity": decision["identity"],
            "generated_at": decision["generated_at"],
            "decision": decision["decision"],
            "sha256": decision["source_sha256"],
        }
        if decision.get("source_path") is not None:
            artifact["path"] = decision["source_path"]
        artifacts.append(artifact)
    return artifacts


def _repeated_blockers(decisions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, list[str]] = {}
    for decision in decisions:
        for reason in decision["blocking_reasons"]:
            seen.setdefault(reason, []).append(decision["generated_at"])
    summary: list[dict[str, Any]] = []
    for reason in sorted(seen):
        timestamps = seen[reason]
        if len(timestamps) < 2:
            continue
        summary.append(
            {
                "reason": reason,
                "count": len(timestamps),
                "first_seen_at": min(timestamps),
                "latest_seen_at": max(timestamps),
            }
        )
    return summary


def _public_decision_row(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": decision["identity"],
        "generated_at": decision["generated_at"],
        "decision": decision["decision"],
        "blocking_reasons": decision["blocking_reasons"],
        "human_review_required": decision["human_review_required"],
        "source_sha256": decision["source_sha256"],
        "source_path": decision.get("source_path"),
        "source_artifact_identities": decision["source_artifact_identities"],
    }


def _latest_decision(decision: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "identity": decision["identity"],
        "generated_at": decision["generated_at"],
        "decision": decision["decision"],
        "blocking_reasons": decision["blocking_reasons"],
        "source_sha256": decision["source_sha256"],
        "source_path": decision.get("source_path"),
    }


def build_longitudinal_promotion_decision_archive(
    decisions: Sequence[Mapping[str, Any] | str | Path],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    evaluated_at = _generated_at(generated_at)
    normalized: list[dict[str, Any]] = []
    rejected_sources: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    for decision in decisions:
        accepted, rejected = _normalize_decision(decision)
        if rejected is not None:
            rejected_sources.append(
                {
                    **rejected["source"],
                    "reason_codes": rejected["reason_codes"],
                }
            )
            reason_codes.extend(rejected["reason_codes"])
            continue
        assert accepted is not None
        normalized.append(accepted)

    duplicate_identities = [identity for identity, count in Counter(row["identity"] for row in normalized).items() if count > 1]
    if duplicate_identities:
        reason_codes.append("duplicate_decision_identity")

    if reason_codes:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": evaluated_at,
            "decision": "reject",
            "reason_codes": sorted(dict.fromkeys(reason_codes)),
            "decisions": [],
            "latest_decision": None,
            "counts_by_decision": {},
            "repeated_blockers": [],
            "first_decision_at": None,
            "latest_decision_at": None,
            "source_artifacts": [],
            "rejected_sources": rejected_sources,
        }

    normalized.sort(key=lambda row: (row["_sort_at"], row["identity"]))
    if not normalized:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": evaluated_at,
            "decision": "hold",
            "reason_codes": ["empty_decision_archive"],
            "decisions": [],
            "latest_decision": None,
            "counts_by_decision": {},
            "repeated_blockers": [],
            "first_decision_at": None,
            "latest_decision_at": None,
            "source_artifacts": [],
            "rejected_sources": [],
        }

    latest = normalized[-1]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at,
        "decision": latest["decision"],
        "reason_codes": list(latest["blocking_reasons"]),
        "decisions": [_public_decision_row(row) for row in normalized],
        "latest_decision": _latest_decision(latest),
        "counts_by_decision": dict(Counter(row["decision"] for row in normalized)),
        "repeated_blockers": _repeated_blockers(normalized),
        "first_decision_at": normalized[0]["generated_at"],
        "latest_decision_at": latest["generated_at"],
        "source_artifacts": _source_artifacts(normalized),
        "rejected_sources": [],
    }


def _decision_paths(*, decision_paths: Sequence[str | Path] = (), input_dir: str | Path | None = None) -> list[Path]:
    paths = [Path(path) for path in decision_paths]
    if input_dir is not None:
        paths.extend(sorted(Path(input_dir).glob("*.json")))
    return sorted(paths, key=lambda path: str(path))


def write_longitudinal_promotion_decision_archive(
    output_path: str | Path,
    *,
    decision_paths: Sequence[str | Path] = (),
    input_dir: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_longitudinal_promotion_decision_archive(
        _decision_paths(decision_paths=decision_paths, input_dir=input_dir),
        generated_at=generated_at,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a longitudinal simulated-live promotion decision archive")
    parser.add_argument("--decision", action="append", default=[], help="Promotion gate decision JSON path")
    parser.add_argument("--input-dir", default=None, help="Directory containing promotion gate decision JSON files")
    parser.add_argument("--output", required=True, help="Output JSON archive path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    args = parser.parse_args()

    payload = write_longitudinal_promotion_decision_archive(
        args.output,
        decision_paths=[Path(path) for path in args.decision],
        input_dir=args.input_dir,
        generated_at=args.generated_at,
    )
    print(
        "LONGITUDINAL_PROMOTION_DECISION_ARCHIVE_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "reason_codes": payload["reason_codes"],
                "decision_count": len(payload["decisions"]),
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "FILENAME",
    "SCHEMA_VERSION",
    "build_longitudinal_promotion_decision_archive",
    "write_longitudinal_promotion_decision_archive",
]
