"""Gate for the twice-daily deliberation schedule (unit tests)."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import should_run_deliberate as gate  # noqa: E402


def _at(hour, minute=0, day=23):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


class DeliberateGateTest(unittest.TestCase):
    def test_force_always_runs(self):
        with mock.patch.object(gate, "newest_decision_time", return_value=_at(4, 0) - timedelta(minutes=5)):
            run, reason = gate.decide(now=_at(9), force=True)
        self.assertTrue(run)
        self.assertIn("forced", reason)

    def test_slot_hours_run(self):
        old = _at(4) - timedelta(hours=9)
        for hour in (4, 10):
            with mock.patch.object(gate, "newest_decision_time", return_value=old):
                run, reason = gate.decide(now=_at(hour, 5))
            self.assertTrue(run, (hour, reason))
            self.assertIn("slot hour", reason)

    def test_no_double_run_within_two_hours(self):
        last = _at(10, 5) - timedelta(minutes=20)
        with mock.patch.object(gate, "newest_decision_time", return_value=last):
            run, reason = gate.decide(now=_at(10, 30))
        self.assertFalse(run)
        self.assertIn("already ran", reason)

    def test_outside_slots_skips(self):
        old = _at(4) - timedelta(hours=4)
        with mock.patch.object(gate, "newest_decision_time", return_value=old):
            run, reason = gate.decide(now=_at(7, 15))
        self.assertFalse(run)
        self.assertIn("outside slots", reason)

    def test_catchup_after_20h(self):
        last = _at(4) - timedelta(hours=21)
        with mock.patch.object(gate, "newest_decision_time", return_value=last):
            run, reason = gate.decide(now=_at(18))
        self.assertTrue(run)
        self.assertIn("catch-up", reason)

    def test_missing_decision_runs(self):
        with mock.patch.object(gate, "newest_decision_time", return_value=None):
            run, reason = gate.decide(now=_at(7))
        self.assertTrue(run)
        self.assertIn("catch-up", reason)

    def test_writes_github_env(self):
        import tempfile

        env_path = Path(tempfile.mkdtemp()) / "env"
        env_path.write_text("", encoding="utf-8")
        with mock.patch.object(gate, "newest_decision_time", return_value=None):
            gate.decide(now=_at(7))
            gate.main(["--github-env", str(env_path)])
        content = env_path.read_text(encoding="utf-8")
        self.assertIn("RUN_NOW=1", content)
        self.assertIn("RUN_REASON=", content)


class ContentTimeTest(unittest.TestCase):
    """CI checkout sets every mtime to "now"; ages must come from file content."""

    def test_age_ignores_mtime(self):
        import json
        import os
        import tempfile

        import check_staleness as stale

        d = Path(tempfile.mkdtemp())
        (d / "2026-10-22_0400.json").write_text(
            json.dumps({"generated_at": "2026-10-22T04:30:00+00:00"}), encoding="utf-8")
        (d / "2026-10-23_1900.json").write_text(
            json.dumps({"generated_at": "2026-10-23T19:35:45+00:00"}), encoding="utf-8")
        now = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
        for p in d.iterdir():
            os.utime(p, (now.timestamp(), now.timestamp()))  # what actions/checkout does
        age, _ = stale._age_hours(d, now)
        self.assertAlmostEqual(age, 24.4, delta=0.1)

    def test_gate_catches_up_when_content_is_old(self):
        stamp = datetime(2026, 10, 23, 19, 35, tzinfo=timezone.utc)
        with mock.patch.object(gate, "newest_decision_time", return_value=stamp):
            run, reason = gate.decide(now=_at(20, day=24))
        self.assertTrue(run)
        self.assertIn("catch-up", reason)


if __name__ == "__main__":
    sys.exit(unittest.main())
