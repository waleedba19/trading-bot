#!/usr/bin/env python3
"""Session times: one config (config/sessions.json), used by the gates, the cron generator, the PC tasks and the tests.

GitHub cron is best-effort (measured 2026-10-25: a 00:30 UTC run started at 05:03; the hourly watchdog had a 5 h gap).
So a session does not depend on one tick: ticks fire every 10 minutes in the run-up to each open, and a gate runs the
job once per session window. A dropped tick changes nothing; a late one is still accepted until `grace_minutes` after the open.

    python scripts/sessions.py --print            window table (UTC and local)
    python scripts/sessions.py --write            rewrite the generated cron blocks in the workflows
    python scripts/sessions.py --check            exit 1 if a workflow's cron block differs from the config
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sessions.json"
WORKFLOW_FILES = {"sentiment": "sentiment.yml", "warmup": "warmup.yml", "deliberate": "deliberate.yml"}
BEGIN, END = "# BEGIN GENERATED SESSION CRONS", "# END GENERATED SESSION CRONS"


def load(path=CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _hm(text):
    h, m = text.split(":")
    return int(h), int(m)


def open_utc(cfg, session, day):
    """UTC datetime of this session's open on the local calendar `day` (a date)."""
    h, m = _hm(session["open"])
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone(timedelta(hours=cfg["utc_offset_hours"])))
    return local.astimezone(timezone.utc)


def windows(cfg, workflow, now):
    """Windows around `now` for a workflow: dicts with name, open, start, tick_end, end (all UTC)."""
    base = cfg["workflows"][workflow]
    off = timezone(timedelta(hours=cfg["utc_offset_hours"]))
    out = []
    for offset in (-1, 0, 1):
        day = (now + timedelta(days=offset)).astimezone(off).date()
        for s in cfg["sessions"]:
            if workflow not in s.get("only", cfg["workflows"]):
                continue
            w = {**base, **s.get("window", {})}
            op = open_utc(cfg, s, day)
            out.append({"name": s["name"], "open": op,
                        "start": op - timedelta(minutes=w["start_minutes_before_open"]),
                        "tick_end": op - timedelta(minutes=w["end_minutes_before_open"]),
                        "end": op + timedelta(minutes=cfg["grace_minutes"] if "window" not in s else -w["end_minutes_before_open"])})
    return sorted(out, key=lambda x: x["open"])


def active_window(cfg, workflow, now):
    hits = [w for w in windows(cfg, workflow, now) if w["start"] <= now <= w["end"]]
    return max(hits, key=lambda w: w["start"]) if hits else None   # overlapping windows: the newest one wins


def decide(cfg, workflow, now, last, force=False):
    """-> (run, reason). One run per session window; a dropped or late tick changes nothing.
    last = when the workflow's artifact was last produced (UTC) or None."""
    age = None if last is None else (now - last).total_seconds() / 3600.0
    age_text = "none" if age is None else f"{age:.1f}h"
    if force:
        return True, f"forced by dispatch (last {age_text})"
    w = active_window(cfg, workflow, now)
    if w and (last is None or last < w["start"]):
        return True, f"{w['name']} session window (open {w['open']:%H:%M} UTC), nothing produced in it yet (last {age_text})"
    if w:
        return False, f"{w['name']} session already done in this window (last {age_text})"
    if age is None or age >= cfg["catchup_hours"]:
        return True, f"catch-up: last {age_text} (>= {cfg['catchup_hours']}h)"
    return False, f"outside every session window; last {age_text}"


def tick_times(cfg, workflow):
    """Sorted (hour, minute) UTC ticks: every `tick_every_minutes`, minute digit fixed, inside start..tick_end of each session."""
    step, digit = cfg["tick_every_minutes"], cfg["tick_minute_digit"]
    ref = datetime(2026, 1, 15, tzinfo=timezone.utc)
    found = set()
    for w in windows(cfg, workflow, ref):
        t = w["start"].replace(second=0, microsecond=0)
        while t <= w["tick_end"]:
            if t.minute % step == digit % step:
                found.add((t.hour, t.minute))
            t += timedelta(minutes=1)
    return sorted(found)


def cron_lines(cfg, workflow):
    by_hour = {}
    for h, m in tick_times(cfg, workflow):
        by_hour.setdefault(h, []).append(m)
    return [f'    - cron: "{",".join(str(m) for m in sorted(ms))} {h} * * *"   # {workflow} session ticks (generated)'
            for h, ms in sorted(by_hour.items())]


def render_block(cfg, workflow):
    return [f"    {BEGIN} (scripts/sessions.py --write; edit config/sessions.json)"] + cron_lines(cfg, workflow) + [f"    {END}"]


def _apply(text, block):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    idx_b = next((i for i, l in enumerate(lines) if BEGIN in l), None)
    idx_e = next((i for i, l in enumerate(lines) if END in l), None)
    if idx_b is None or idx_e is None:
        raise SystemExit("markers missing")
    lines[idx_b:idx_e + 1] = block
    return nl.join(lines)


def check_or_write(cfg, write):
    bad = []
    for wf, fname in WORKFLOW_FILES.items():
        p = ROOT / ".github" / "workflows" / fname
        text = p.read_text(encoding="utf-8", newline="")
        new = _apply(text, render_block(cfg, wf))
        if new != text:
            bad.append(fname)
            if write:
                p.write_text(new, encoding="utf-8", newline="")
    return bad


def table(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    off = timezone(timedelta(hours=cfg["utc_offset_hours"]))
    rows = []
    for wf in cfg["workflows"]:
        for w in windows(cfg, wf, now):
            if w["open"].astimezone(off).date() == now.astimezone(off).date():
                f = lambda t: t.astimezone(off).strftime("%H:%M")
                rows.append(f"{wf:11s} {w['name']:10s} ticks {f(w['start'])}-{f(w['tick_end'])} local, gate open until {f(w['end'])}, market open {f(w['open'])}  (UTC {w['start']:%H:%M}-{w['end']:%H:%M})")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    cfg = load()
    if a.print:
        print(table(cfg))
    if a.write or a.check:
        bad = check_or_write(cfg, write=a.write)
        print(("rewrote: " if a.write else "DIFFERS: ") + (", ".join(bad) or "none"))
        return 1 if (bad and a.check) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
