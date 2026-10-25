#!/usr/bin/env python3
"""Gate for the twice-daily deliberation schedule (PC off, GitHub only).

Why: GitHub cron is best-effort on public repos -- slots get delayed or dropped
(today's 00:30/08:30/16:30/20:30 runs were silently skipped). Ticking every two
hours and letting this gate decide guarantees exactly two real sessions per day
plus a catch-up if GitHub dropped both.

Slots (UTC):  04:00  = 06:00 Africa/Tripoli (day start)
              10:30  = 12:30 Africa/Tripoli (midday)

Rules, first match wins:
  1. --force (manual dispatch)          -> run
  2. newest decision < 2 h old          -> skip (already ran this slot)
  3. UTC hour in (4, 10)                -> run (tick delayed up to 45 min)
  4. newest decision missing or >= 20 h -> run (catch-up; never strand a day)
  5. otherwise                          -> skip

Always exit 0: this gate must never fail the workflow. The decision is written
to $GITHUB_ENV as RUN_NOW=1/0 when a decision path is requested.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_staleness import content_time  # noqa: E402
LATEST = ROOT / "user_data" / "decisions" / "latest.json"
DECISIONS = ROOT / "reports" / "decisions"
SLOT_HOURS_UTC = (4, 10)
MIN_GAP_HOURS = 2.0
CATCHUP_HOURS = 20.0
SLOT_MINUTE_LIMIT = 45


def newest_decision_time():
    candidates = []
    if LATEST.is_file():
        try:
            data = json.loads(LATEST.read_text(encoding="utf-8"))
            stamp = data.get("generated_at") or data.get("asof")
            if stamp:
                candidates.append(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
        except (OSError, ValueError):
            pass
    stamp = content_time(DECISIONS)
    if stamp:
        candidates.append(stamp)
    return max(candidates) if candidates else None


def decide(now=None, force=False):
    now = now or datetime.now(timezone.utc)
    last = newest_decision_time()
    age = None if last is None else (now - last).total_seconds() / 3600.0
    age_text = "none" if age is None else f"{age:.1f}h"

    if force:
        return True, f"forced by dispatch (last decision {age_text})"
    if age is not None and age < MIN_GAP_HOURS:
        return False, f"already ran {age:.1f}h ago (<{MIN_GAP_HOURS:g}h)"
    if now.hour in SLOT_HOURS_UTC and now.minute <= SLOT_MINUTE_LIMIT:
        return True, f"slot hour {now.hour:02d}:00 UTC (last decision {age_text})"
    if age is None or age >= CATCHUP_HOURS:
        return True, f"catch-up: last decision {age_text} (>= {CATCHUP_HOURS:g}h)"
    return False, f"outside slots; last decision {age_text}"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/should_run_deliberate.py")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--github-env", default=None,
                        help="path to $GITHUB_ENV; writes RUN_NOW=1/0")
    args = parser.parse_args(argv)

    run, reason = decide(force=args.force)
    print(f"run={int(run)} reason={reason}")
    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as fh:
            fh.write(f"RUN_NOW={1 if run else 0}\n")
            fh.write(f"RUN_REASON={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
