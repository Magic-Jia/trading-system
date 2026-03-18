from pathlib import Path
import json


def test_v2_fixture_files_exist():
    base = Path("trading_system/tests/fixtures")
    assert json.loads((base / "account_snapshot_v2.json").read_text())
    assert json.loads((base / "market_context_v2.json").read_text())
    assert json.loads((base / "derivatives_snapshot_v2.json").read_text())
    assert (base / "FIXTURE_PROVENANCE.md").exists()
