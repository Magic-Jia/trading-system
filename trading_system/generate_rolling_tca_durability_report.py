from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from trading_system.app.reporting.rolling_tca_durability_report import (
    DEFAULT_BUCKET_DIMENSIONS,
    DEFAULT_WINDOWS,
    write_rolling_tca_durability_report,
)


def _thresholds_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "max_p95_slippage_bps": args.max_p95_slippage_bps,
        "max_p99_slippage_bps": args.max_p99_slippage_bps,
        "max_p95_latency_ms": args.max_p95_latency_ms,
        "max_p99_latency_ms": args.max_p99_latency_ms,
        "max_reject_cancel_rate": args.max_reject_cancel_rate,
        "max_maker_taker_mix_shift": args.max_maker_taker_mix_shift,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate rolling-window and bucketed TCA durability evidence for simulated live calibration records"
    )
    parser.add_argument("--input", action="append", dest="inputs", required=True, help="Calibration JSONL file or directory")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument("--start-date", required=True, help="Canonical start date, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Canonical end date, YYYY-MM-DD")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    parser.add_argument("--window", action="append", dest="windows", default=None, help="Rolling window such as 1d, 3d, or 7d")
    parser.add_argument(
        "--bucket",
        action="append",
        dest="buckets",
        default=None,
        help="Bucket dimension: global, symbol, setup_type, maker_taker, session_utc_hour",
    )
    parser.add_argument("--min-samples-per-bucket", type=int, default=30)
    parser.add_argument("--max-p95-slippage-bps", type=float, default=5.0)
    parser.add_argument("--max-p99-slippage-bps", type=float, default=None)
    parser.add_argument("--max-p95-latency-ms", type=float, default=1000.0)
    parser.add_argument("--max-p99-latency-ms", type=float, default=None)
    parser.add_argument("--max-reject-cancel-rate", type=float, default=None)
    parser.add_argument("--max-maker-taker-mix-shift", type=float, default=None)
    args = parser.parse_args()

    payload = write_rolling_tca_durability_report(
        Path(args.output),
        input_paths=[Path(value) for value in args.inputs],
        start_date=args.start_date,
        end_date=args.end_date,
        generated_at=args.generated_at,
        windows=tuple(args.windows or DEFAULT_WINDOWS),
        min_samples_per_bucket=args.min_samples_per_bucket,
        bucket_dimensions=tuple(args.buckets or DEFAULT_BUCKET_DIMENSIONS),
        thresholds=_thresholds_from_args(args),
    )
    print(
        "ROLLING_TCA_DURABILITY_REPORT_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "reasons": payload["reasons"],
                "window_count": len(payload["windows"]),
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
