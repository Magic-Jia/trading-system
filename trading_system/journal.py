from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "journal.jsonl"
BJ = timezone(timedelta(hours=8))


def append_note(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    note = sub.add_parser("note")
    note.add_argument("--type", required=True)
    note.add_argument("--symbol", required=True)
    note.add_argument("--side", required=True)
    note.add_argument("--action", required=True)
    note.add_argument("--text", required=True)
    args = ap.parse_args()

    payload = {
        "ts_bj": datetime.now(BJ).isoformat(),
        "type": args.type,
        "symbol": args.symbol,
        "side": args.side,
        "action": args.action,
        "text": args.text,
    }
    append_note(payload)
    print("logged")


if __name__ == "__main__":
    main()
