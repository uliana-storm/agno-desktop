#!/usr/bin/env python3
"""Reproduce fetch_digest behaviour across hours windows (720 vs 168 vs 24).

Usage (from repo root, with SLACK_BOT_TOKEN set):
    AGENT_DEBUG=1 .venv/bin/python scripts/repro_fetch_digest.py
    AGENT_DEBUG=1 .venv/bin/python scripts/repro_fetch_digest.py --channel dev
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env if present
_env = _ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

from tools.slack_fetch_tools import SlackFetchToolkit, _fetch_channel
from tools.slack_helpers import bot_client, resolve_channel


def print_oldest_windows(hours_list: list[int]) -> None:
    print("=== oldest timestamps ===")
    now = time.time()
    for h in hours_list:
        oldest = now - (h * 3600)
        iso = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat()
        print(f"  hours={h:>4}  oldest={oldest:.3f}  ({iso})")
    print()


def direct_fetch_channel(channel: str, hours_list: list[int], pause_s: float) -> None:
    print(f"=== direct _fetch_channel ({channel}) ===")
    client = bot_client()
    channel_id, channel_name = resolve_channel(client, channel)
    print(f"  resolved: id={channel_id} name={channel_name!r}")
    if pause_s > 0:
        print(f"  (pausing {pause_s}s between calls to reduce Slack rate-limit noise)\n")
    else:
        print()

    for i, h in enumerate(hours_list):
        if i > 0 and pause_s > 0:
            time.sleep(pause_s)
        oldest = time.time() - (h * 3600)
        msgs, err = _fetch_channel(
            client, channel_id, channel_name, oldest, min_reply_count=1
        )
        print(f"  hours={h:>4}  msgs={len(msgs):>4}  error={err!r}")


def fetch_digest_tool(
    channel: str,
    hours_list: list[int],
    date: str = "",
) -> None:
    print(f"=== fetch_digest tool ({channel}) ===")
    toolkit = SlackFetchToolkit()

    if date:
        print(f"\n--- date={date} ---")
        result = toolkit.fetch_digest(channel, date=date)
        print(f"result_len={len(result)}")
        first_line = result.split("\n", 1)[0] if result else ""
        print(first_line)
        return

    for h in hours_list:
        print(f"\n--- hours={h} ---")
        result = toolkit.fetch_digest(channel, hours=h)
        print(f"result_len={len(result)}")
        if result.startswith("Issues:"):
            print(result[:500])
        else:
            first_line = result.split("\n", 1)[0] if result else ""
            print(first_line)
            print(f"(preview {min(200, len(result))} chars)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repro fetch_digest hours windows")
    parser.add_argument("--channel", default="dev", help="Channel name or ID (default: dev)")
    parser.add_argument(
        "--hours",
        default="720,168,24",
        help="Comma-separated hours values (default: 720,168,24)",
    )
    parser.add_argument(
        "--date",
        default="",
        help='Specific day YYYY-MM-DD (Melbourne); overrides --hours when set',
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.0,
        help="Seconds to pause between direct _fetch_channel calls (default: 2)",
    )
    parser.add_argument(
        "--skip-direct",
        action="store_true",
        help="Skip direct _fetch_channel section",
    )
    parser.add_argument(
        "--skip-tool",
        action="store_true",
        help="Skip fetch_digest tool section",
    )
    args = parser.parse_args()
    hours_list = [int(x.strip()) for x in args.hours.split(",") if x.strip()]

    if not os.environ.get("SLACK_BOT_TOKEN"):
        print("ERROR: SLACK_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    debug = os.environ.get("AGENT_DEBUG") == "1"
    if not debug:
        print("Tip: run with AGENT_DEBUG=1 for _fetch_channel exit-path logs\n")

    if args.date:
        print(f"=== date mode: {args.date} (Melbourne) ===\n")
        if not args.skip_tool:
            fetch_digest_tool(args.channel, hours_list, date=args.date)
        return

    print_oldest_windows(hours_list)
    if not args.skip_direct:
        direct_fetch_channel(args.channel, hours_list, args.sleep)
        print()
    if not args.skip_tool:
        fetch_digest_tool(args.channel, hours_list)


if __name__ == "__main__":
    main()
