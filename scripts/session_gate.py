#!/usr/bin/env python3
"""Run-once-per-session gate for Deliberate, Warmup and Sentiment.

Times live only in config/sessions.json (see scripts/sessions.py). GitHub cron is
best-effort, so each workflow ticks every 10 minutes in the run-up to each open and
this gate runs the job once per session window. Always exits 0 (never fails a
workflow); writes RUN_NOW=1/0 and RUN_REASON to --github-env.

    python scripts/session_gate.py --workflow warmup [--force] [--github-env PATH]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sessions  # noqa: E402
from check_staleness import content_time  # noqa: E402

LATEST = ROOT / "user_data" / "decisions" / "latest.json"
DECISIONS = ROOT / "reports" / "decisions"
WARMUP_LOG = ROOT / "research" / "reports" / "warmup" / "warmup_log.jsonl"
SENTIMENT_DB = ROOT / "sentiment" / "sentiment.sqlite"


def newest_decision_time():
    candidates = []
    if LATEST.is_file():
        try:
            data = json.loads(LATEST.read_text(encoding="utf-8"))
            stamp = data.get("generated_at") or data.get("asof")
            if stamp:
                parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                candidates.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
        except (OSError, ValueError):
            pass
    stamp = content_time(DECISIONS)
    if stamp:
        candidates.append(stamp)
    return max(candidates) if candidates else None


def newest_sentiment_time():
    if not SENTIMENT_DB.is_file():
        return None
    try:
        with sqlite3.connect(SENTIMENT_DB) as db:
            stamps = [db.execute(f"SELECT MAX(ts_utc) FROM {t}").fetchone()[0]
                      for t in ("fng", "gdelt_tone", "reddit_posts")]
        stamps = [datetime.fromisoformat(s) for s in stamps if s]
    except (sqlite3.Error, ValueError):
        return None
    return max(stamps) if stamps else None


LAST_RUN = {
    "deliberate": lambda: newest_decision_time(),
    "warmup": lambda: content_time(WARMUP_LOG),
    "sentiment": lambda: newest_sentiment_time(),
}


def decide(workflow, now=None, force=False, cfg=None):
    return sessions.decide(cfg or sessions.load(), workflow, now or datetime.now(timezone.utc),
                           LAST_RUN[workflow](), force)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/session_gate.py")
    parser.add_argument("--workflow", required=True, choices=sorted(LAST_RUN))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--github-env", default=None, help="path to $GITHUB_ENV; writes RUN_NOW=1/0")
    args = parser.parse_args(argv)

    run, reason = decide(args.workflow, force=args.force)
    print(f"run={int(run)} reason={reason}")
    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as fh:
            fh.write(f"RUN_NOW={1 if run else 0}\nRUN_REASON={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
