#!/usr/bin/env python3
"""Staleness detector for the self-healing watchdog.

GitHub cron is best-effort and silently drops slots. This script turns that
from "hope" into a fact we can act on: it compares each artifact's age with the
cadence of the workflow that produces it, and prints which workflows must be
re-dispatched.

Cooldown: a dispatched workflow is remembered in
research/reports/health/dispatch_state.json so the watchdog cannot loop.

Usage:
    python scripts/check_staleness.py --github-env "$GITHUB_ENV"
    python scripts/check_staleness.py --json
    python scripts/check_staleness.py --record-dispatched "Deliberate,Warmup"
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HEALTH_DIR = ROOT / "research" / "reports" / "health"
STATE_PATH = HEALTH_DIR / "dispatch_state.json"
COOLDOWN_HOURS = 3.0

# artifact -> (owner workflow, max age hours)
RULES = (
    ("research/reports/warmup/warmup_log.jsonl", "Warmup", 26.0),
    ("reports/decisions", "Deliberate", 14.0),
    ("research/reports/learning/daily_learning.json", "Daily learning", 30.0),
    ("research/reports/quality/data_quality.json", "Daily learning", 30.0),
    ("research/reports/vibe/latest.json", "Vibe-Trading research", 50.0),
    ("research/reports/news/latest.json", "News", 9.0),   # every 3 h; 9 h = two missed runs
    ("research/reports/scan_ic.json", "Scan IC", 192.0),   # weekly measurement
)


def _parse(value):
    for fmt in ("%Y-%m-%d_%H%M",):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def content_time(path):
    """When the artifact was PRODUCED, read from its own timestamp.

    File mtime is useless on a CI runner: checkout sets every file to "now", so
    everything looked fresh and stale workflows were never detected. Directory =
    newest file by name (names start YYYY-MM-DD_HHMM). mtime is only the last
    resort for files that carry no timestamp.
    """
    if path.is_dir():
        files = sorted(p for p in path.glob("*.json") if p.is_file())
        return content_time(files[-1]) if files else None
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text.splitlines()[-1] if path.suffix == ".jsonl" else text)
        if isinstance(data, dict):
            stamp = _parse(data.get("generated_at") or data.get("asof") or data.get("ts"))
            if stamp:
                return stamp
    except (OSError, ValueError, IndexError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _age_hours(path, now):
    stamp = content_time(path)
    if stamp is None:
        return None, None
    return (now - stamp).total_seconds() / 3600.0, str(path)


def _state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _recent(workflow, now):
    last = _state().get(workflow)
    if not last:
        return False
    try:
        stamp = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - stamp).total_seconds() / 3600.0 < COOLDOWN_HOURS


def detect(now=None):
    now = now or datetime.now(timezone.utc)
    stale = {}
    details = []
    for rel, workflow, max_age in RULES:
        age, path = _age_hours(ROOT / rel, now)
        if age is None:
            details.append({"artifact": rel, "workflow": workflow, "age": None, "reason": "missing"})
            if not _recent(workflow, now):
                stale.setdefault(workflow, []).append(f"{rel}: missing")
            continue
        if age > max_age:
            details.append({"artifact": rel, "workflow": workflow, "age": round(age, 1),
                            "reason": f">{max_age:g}h"})
            if not _recent(workflow, now):
                stale.setdefault(workflow, []).append(f"{rel}: {age:.1f}h>{max_age:g}h")
    return {"generated_at": now.isoformat(), "stale": stale, "details": details,
            "workflows": sorted(stale)}


def record_dispatched(workflows, now=None):
    now = now or datetime.now(timezone.utc)
    state = _state()
    for workflow in workflows:
        state[workflow] = now.isoformat()
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/check_staleness.py")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--github-env", default=None)
    parser.add_argument("--record-dispatched", default=None)
    args = parser.parse_args(argv)

    if args.record_dispatched is not None:
        workflows = [w.strip() for w in args.record_dispatched.split(",") if w.strip()]
        record_dispatched(workflows)
        print(f"recorded dispatch: {workflows}")
        return 0

    report = detect()
    workflows = report["workflows"]
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"stale workflows: {workflows or 'none'}")
        for row in report["details"]:
            print(f"  {row['workflow']:24s} {row['artifact']:52s} {row['reason']}")
    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as fh:
            fh.write(f"STALE_WORKFLOWS={','.join(workflows)}\n")
            fh.write(f"STALE_COUNT={len(workflows)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
