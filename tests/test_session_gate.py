"""Session gate + session config (unit tests). Times are UTC; local = UTC+2 (Africa/Tripoli)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_staleness as stale  # noqa: E402
import session_gate as gate  # noqa: E402
import sessions  # noqa: E402

CFG = sessions.load()


def at(hour, minute=0, day=26):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def decide(wf, now, last, force=False):
    return sessions.decide(CFG, wf, now, last, force)


class SessionConfigTest(unittest.TestCase):
    def test_opens_are_the_three_markets_plus_trade_ready(self):
        self.assertEqual([s["open"] for s in CFG["sessions"]], ["03:00", "09:00", "14:00", "15:00"])

    def test_open_utc_conversion(self):
        self.assertEqual(sessions.open_utc(CFG, CFG["sessions"][0], at(0).date()), at(1))
        self.assertEqual(sessions.open_utc(CFG, CFG["sessions"][2], at(0).date()), at(12))

    def test_workflow_crons_match_config(self):
        self.assertEqual(sessions.check_or_write(CFG, write=False), [], "run: python scripts/sessions.py --write")

    def test_ticks_stay_inside_windows_and_use_fixed_minutes(self):
        for wf in CFG["workflows"]:
            ticks = sessions.tick_times(CFG, wf)
            self.assertTrue(ticks)
            for _, minute in ticks:
                self.assertEqual(minute % 10, CFG["tick_minute_digit"] % 10)

    def test_every_session_has_a_tick_at_least_10_min_before_its_open(self):
        for wf in CFG["workflows"]:
            ticks = {(h, m) for h, m in sessions.tick_times(CFG, wf)}
            for w in sessions.windows(CFG, wf, at(12)):
                if w["open"].date() != at(12).date():
                    continue
                t, ok = w["start"], False
                while t <= w["open"] - timedelta(minutes=10):
                    ok = ok or (t.hour, t.minute) in ticks
                    t += timedelta(minutes=1)
                self.assertTrue(ok, (wf, w["name"], "no tick 10+ min before the open"))

    def test_warmup_ticks_end_before_deliberate_ticks_start(self):
        """Deliberate reads the warmup verdict: warmup's window must start before deliberate's."""
        w, d = CFG["workflows"]["warmup"], CFG["workflows"]["deliberate"]
        self.assertGreater(w["start_minutes_before_open"], d["start_minutes_before_open"])
        self.assertGreaterEqual(w["end_minutes_before_open"], 15, "warmup (2.5 min run) must end early enough")


class SessionDecisionTest(unittest.TestCase):
    old = at(0) - timedelta(hours=8)

    def test_force_always_runs(self):
        run, reason = decide("deliberate", at(5), at(4, 59), force=True)
        self.assertTrue(run)
        self.assertIn("forced", reason)

    def test_runs_in_window_before_open(self):
        run, _ = decide("deliberate", at(0, 17), self.old)          # 02:17 local, 03:00 open
        self.assertTrue(run)

    def test_runs_once_per_window(self):
        done = at(0, 25)
        run, reason = decide("deliberate", at(0, 37), done)
        self.assertFalse(run)
        self.assertIn("already done", reason)

    def test_late_tick_still_runs_until_grace(self):
        self.assertTrue(decide("deliberate", at(1, 30), self.old)[0])    # 03:30 local, 30 min after open
        self.assertFalse(decide("deliberate", at(2, 0), at(0, 30))[0])   # past grace, already done

    def test_dropped_ticks_do_not_matter(self):
        # only the last tick of the window survives
        self.assertTrue(decide("warmup", at(0, 37), self.old)[0])

    def test_outside_every_window_skips(self):
        run, reason = decide("deliberate", at(3, 17), at(2, 0) - timedelta(hours=2))
        self.assertFalse(run)
        self.assertIn("outside", reason)

    def test_catchup_after_20h(self):
        run, reason = decide("deliberate", at(3, 17), at(3, 17) - timedelta(hours=21))
        self.assertTrue(run)
        self.assertIn("catch-up", reason)

    def test_missing_artifact_runs(self):
        self.assertTrue(decide("sentiment", at(3, 17), None)[0])

    def test_trade_ready_refresh_runs_after_afternoon_session(self):
        afternoon_done = at(11, 30)                                      # 13:30 local decision
        run, reason = decide("deliberate", at(12, 37), afternoon_done)   # 14:37 local, TradeReady window
        self.assertTrue(run)
        self.assertIn("TradeReady", reason)

    def test_trade_ready_only_for_deliberate(self):
        names = {w["name"] for w in sessions.windows(CFG, "warmup", at(12))}
        self.assertNotIn("TradeReady", names)
        self.assertIn("TradeReady", {w["name"] for w in sessions.windows(CFG, "deliberate", at(12))})

    def test_all_three_opens_each_get_a_run_from_cold(self):
        for open_utc_hour in (1, 7, 12):
            now = at(open_utc_hour) - timedelta(minutes=23)
            self.assertTrue(decide("deliberate", now, now - timedelta(hours=6))[0], open_utc_hour)


class GateScriptTest(unittest.TestCase):
    def test_gate_uses_artifact_time_per_workflow(self):
        with mock.patch.dict(gate.LAST_RUN, {"warmup": lambda: None}):
            run, _ = gate.decide("warmup", now=at(0, 37))
        self.assertTrue(run)

    def test_writes_github_env(self):
        env = Path(tempfile.mkdtemp()) / "env"
        env.write_text("", encoding="utf-8")
        with mock.patch.dict(gate.LAST_RUN, {"sentiment": lambda: None}):
            self.assertEqual(gate.main(["--workflow", "sentiment", "--github-env", str(env)]), 0)
        text = env.read_text(encoding="utf-8")
        self.assertIn("RUN_NOW=", text)
        self.assertIn("RUN_REASON=", text)

    def test_every_workflow_has_a_reader(self):
        self.assertEqual(set(gate.LAST_RUN), set(CFG["workflows"]))


class ContentTimeTest(unittest.TestCase):
    """CI checkout sets every mtime to "now"; ages must come from file content."""

    def test_age_ignores_mtime(self):
        d = Path(tempfile.mkdtemp())
        (d / "2026-10-22_0400.json").write_text(json.dumps({"generated_at": "2026-10-22T04:30:00+00:00"}), encoding="utf-8")
        (d / "2026-10-23_1900.json").write_text(json.dumps({"generated_at": "2026-10-23T19:35:45+00:00"}), encoding="utf-8")
        now = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
        for p in d.iterdir():
            os.utime(p, (now.timestamp(), now.timestamp()))
        age, _ = stale._age_hours(d, now)
        self.assertAlmostEqual(age, 24.4, delta=0.1)


if __name__ == "__main__":
    sys.exit(unittest.main())
