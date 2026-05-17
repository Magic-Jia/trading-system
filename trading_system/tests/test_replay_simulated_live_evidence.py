from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.replay_simulated_live_evidence import (
    build_replay_simulated_live_evidence_bundle,
    write_replay_simulated_live_evidence_bundle,
)
from trading_system.tests.test_simulated_live_evidence_window import _bundle


def test_builds_replay_bundle_with_explicit_lineage() -> None:
    replay_bundle = build_replay_simulated_live_evidence_bundle(
        _bundle("2026-05-16"),
        replay_source_id="archive-runtime-20260516",
        replay_window_start="2026-05-16T00:00:00Z",
        replay_window_end="2026-05-16T23:59:59Z",
        original_artifact_identities=["runtime-bundle-20260516", "market-data-20260516"],
        generated_at="2026-05-17T00:05:00Z",
    )

    assert replay_bundle["source_mode"] == "replay"
    assert replay_bundle["replay_lineage"] == {
        "replay_source_id": "archive-runtime-20260516",
        "replay_window_start": "2026-05-16T00:00:00Z",
        "replay_window_end": "2026-05-16T23:59:59Z",
        "original_artifact_identities": ["runtime-bundle-20260516", "market-data-20260516"],
        "generated_at": "2026-05-17T00:05:00Z",
    }


def test_replay_bundle_builder_rejects_mislabeled_local_replay_input() -> None:
    source_bundle = _bundle("2026-05-16", replay_lineage={"replay_source_id": "hidden-replay"})

    with pytest.raises(ValueError, match=re.escape("source bundle already contains replay_lineage")):
        build_replay_simulated_live_evidence_bundle(
            source_bundle,
            replay_source_id="archive-runtime-20260516",
            replay_window_start="2026-05-16T00:00:00Z",
            replay_window_end="2026-05-16T23:59:59Z",
            original_artifact_identities=["runtime-bundle-20260516"],
            generated_at="2026-05-17T00:05:00Z",
        )


def test_replay_bundle_cli_writes_offline_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source_bundle.json"
    output = tmp_path / "replay_bundle.json"
    source.write_text(json.dumps(_bundle("2026-05-16")), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_replay_simulated_live_evidence_bundle",
            "--source-bundle",
            str(source),
            "--output",
            str(output),
            "--replay-source-id",
            "archive-runtime-20260516",
            "--replay-window-start",
            "2026-05-16T00:00:00Z",
            "--replay-window-end",
            "2026-05-16T23:59:59Z",
            "--original-artifact-identity",
            "runtime-bundle-20260516",
            "--original-artifact-identity",
            "market-data-20260516",
            "--generated-at",
            "2026-05-17T00:05:00Z",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    replay_bundle = json.loads(output.read_text(encoding="utf-8"))
    assert replay_bundle["source_mode"] == "replay"
    assert replay_bundle["replay_lineage"]["replay_source_id"] == "archive-runtime-20260516"
    assert re.search(r"REPLAY_SIMULATED_LIVE_EVIDENCE_BUNDLE_JSON.*\"source_mode\": \"replay\"", result.stdout)


def test_write_replay_bundle_records_source_file_identity(tmp_path: Path) -> None:
    source = tmp_path / "source_bundle.json"
    output = tmp_path / "replay_bundle.json"
    source.write_text(json.dumps(_bundle("2026-05-16")), encoding="utf-8")

    replay_bundle = write_replay_simulated_live_evidence_bundle(
        output,
        source_bundle=source,
        replay_source_id="archive-runtime-20260516",
        replay_window_start="2026-05-16T00:00:00Z",
        replay_window_end="2026-05-16T23:59:59Z",
        original_artifact_identities=["runtime-bundle-20260516"],
        generated_at="2026-05-17T00:05:00Z",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == replay_bundle
    assert replay_bundle["source"]["path"].endswith("source_bundle.json")
    assert len(replay_bundle["source"]["sha256"]) == 64
