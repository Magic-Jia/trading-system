from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.reporting.simulated_live_evidence_window import (
    SOURCE_MODE_REPLAY,
    SOURCE_MODE_SIMULATED_LIVE_LOCAL,
    _bundle_payload,
    _parse_canonical_timestamp,
    _require_safe_identifier,
)


def _require_original_artifact_identities(value: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("original_artifact_identities must be a non-empty list")
    return [
        _require_safe_identifier(identity, f"original_artifact_identities[{index}]")
        for index, identity in enumerate(value)
    ]


def build_replay_simulated_live_evidence_bundle(
    source_bundle: Mapping[str, Any] | str | Path,
    *,
    replay_source_id: str,
    replay_window_start: str,
    replay_window_end: str,
    original_artifact_identities: list[str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload, source = _bundle_payload(source_bundle)
    if payload.get("source_mode") != SOURCE_MODE_SIMULATED_LIVE_LOCAL:
        raise ValueError("source bundle source_mode must be simulated_live_local")
    if "replay_lineage" in payload:
        raise ValueError("source bundle already contains replay_lineage")

    start = _parse_canonical_timestamp(replay_window_start, "replay_window_start")
    end = _parse_canonical_timestamp(replay_window_end, "replay_window_end")
    if end <= start:
        raise ValueError("replay_window_end must be after replay_window_start")
    lineage_generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_canonical_timestamp(lineage_generated_at, "generated_at")

    replay_bundle = dict(payload)
    replay_bundle["source_mode"] = SOURCE_MODE_REPLAY
    replay_bundle["source"] = {
        **source,
        "identity": _require_safe_identifier(payload.get("session_id"), "source_bundle.session_id"),
    }
    replay_bundle["replay_lineage"] = {
        "replay_source_id": _require_safe_identifier(replay_source_id, "replay_source_id"),
        "replay_window_start": replay_window_start,
        "replay_window_end": replay_window_end,
        "original_artifact_identities": _require_original_artifact_identities(original_artifact_identities),
        "generated_at": lineage_generated_at,
    }
    return replay_bundle


def write_replay_simulated_live_evidence_bundle(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_replay_simulated_live_evidence_bundle(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate replay-labeled simulated-live evidence from a local bundle")
    parser.add_argument("--source-bundle", required=True, help="Local simulated-live bundle JSON path")
    parser.add_argument("--output", required=True, help="Output replay bundle JSON path")
    parser.add_argument("--replay-source-id", required=True, help="Replay source identity")
    parser.add_argument("--replay-window-start", required=True, help="Canonical UTC replay window start")
    parser.add_argument("--replay-window-end", required=True, help="Canonical UTC replay window end")
    parser.add_argument(
        "--original-artifact-identity",
        action="append",
        required=True,
        help="Original artifact identity used to generate replay evidence",
    )
    parser.add_argument("--generated-at", default=None, help="Canonical UTC lineage generation timestamp")
    args = parser.parse_args()

    payload = write_replay_simulated_live_evidence_bundle(
        args.output,
        source_bundle=Path(args.source_bundle),
        replay_source_id=args.replay_source_id,
        replay_window_start=args.replay_window_start,
        replay_window_end=args.replay_window_end,
        original_artifact_identities=args.original_artifact_identity,
        generated_at=args.generated_at,
    )
    print(
        "REPLAY_SIMULATED_LIVE_EVIDENCE_BUNDLE_JSON",
        json.dumps(
            {
                "output": args.output,
                "source_mode": payload["source_mode"],
                "replay_source_id": payload["replay_lineage"]["replay_source_id"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "build_replay_simulated_live_evidence_bundle",
    "write_replay_simulated_live_evidence_bundle",
]
