import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agents import evidence_gate

VIBE_LOG = """noise
{
  "status": "ok",
  "zoo": "academic",
  "universe": "sp500",
  "period": "2024-2025",
  "n_alphas_tested": 2,
  "n_skipped": 0,
  "top": [
    {"id": "academic_carhart_mom", "theme": ["momentum"], "ic_mean": 0.0284,
     "ir": 0.1217, "ic_positive_ratio": 0.5944, "ic_count": 249}
  ]
}
"""


class EvidenceGateTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.decisions = self.dir / "decisions"
        self.decisions.mkdir()
        self.vibe = self.dir / "vibe"
        self.vibe.mkdir()
        self.scan_path = self.dir / "asset_scan.json"
        self.now = datetime.now(timezone.utc)  # vibe_evidence ages files by the real clock; a fixed date made this test fail 48h after it was written

    # ---- fixtures -------------------------------------------------------
    def _decision(self, age_hours=1.0, pairs=None, generated_at=None):
        if generated_at is None:
            ts = (self.now - timedelta(hours=age_hours)).isoformat()
        else:
            ts = generated_at
        pairs = pairs if pairs is not None else {
            "BTC/USDT": {"action": "allow", "size": 0.1, "confidence": 0.7, "rationale": "ok"}
        }
        record = {"generated_at": ts, "asof": ts, "decision": {"pairs": pairs}}
        (self.decisions / "2026-10-23_1700.json").write_text(json.dumps(record), encoding="utf-8")
        return record

    def _vibe(self, age_hours=1.0, text=VIBE_LOG):
        path = self.vibe / "2026-10-23_1722_academic_sp500.txt"
        path.write_text(text, encoding="utf-8")
        mtime = (self.now - timedelta(hours=age_hours)).timestamp()
        os.utime(path, (mtime, mtime))
        return path

    def _scan(self, rows=None):
        rows = rows if rows is not None else [{"pair": "BTC/USD", "profit_score": 0.5}]
        self.scan_path.write_text(json.dumps(rows), encoding="utf-8")

    def _check(self, symbol="BTC/USDT", **kw):
        return evidence_gate.check_entry(
            symbol,
            decisions_dir=self.decisions,
            scan_path=self.scan_path,
            vibe_dir=self.vibe,
            now=self.now,
            **kw,
        )

    # ---- pass -----------------------------------------------------------
    def test_fresh_evidence_allows(self):
        self._decision()
        self._vibe()
        self._scan()
        result = self._check()
        self.assertTrue(result["allowed"], result["reasons"])
        self.assertEqual(result["evidence"]["vibe"]["best_alpha"], "academic_carhart_mom")

    def test_symbol_normalisation(self):
        self._decision()
        self._vibe()
        self._scan()
        self.assertTrue(self._check("BTC")["allowed"])
        self.assertTrue(self._check("BTCUSD")["allowed"])

    # ---- fail closed ----------------------------------------------------
    def test_missing_decision_blocks(self):
        self._vibe()
        self._scan()
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertIn("no deliberation decision record", result["reasons"][0])

    def test_stale_decision_blocks(self):
        self._decision(age_hours=40)
        self._vibe()
        self._scan()
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("stale decision" in r for r in result["reasons"]))

    def test_missing_vibe_blocks(self):
        self._decision()
        self._scan()
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("alpha evidence" in r for r in result["reasons"]))

    def test_stale_vibe_blocks(self):
        self._decision()
        self._vibe(age_hours=72)
        self._scan()
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("stale alpha evidence" in r for r in result["reasons"]))

    def test_negative_scan_blocks(self):
        self._decision()
        self._vibe()
        self._scan([{"pair": "BTC/USD", "profit_score": -0.4}])
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("asset scan ranks" in r for r in result["reasons"]))

    def test_symbol_absent_from_decision_blocks(self):
        self._decision(pairs={})
        self._vibe()
        self._scan()
        result = self._check()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("not in deliberation" in r for r in result["reasons"]))

    def test_scan_miss_is_warning_not_block(self):
        self._decision()
        self._vibe()
        self._scan([{"pair": "ETH/USD", "profit_score": 0.9}])
        result = self._check()
        self.assertTrue(result["allowed"], result["reasons"])
        self.assertIn("not in scanned universe", result["evidence"]["scan"]["reason"])

    # ---- env overrides --------------------------------------------------
    def test_vibe_requirement_can_be_disabled(self):
        self._decision()
        self._scan()
        with mock.patch.object(evidence_gate, "REQUIRE_VIBE", False):
            result = self._check()
        self.assertTrue(result["allowed"], result["reasons"])

    def test_negative_scan_can_be_allowed(self):
        self._decision()
        self._vibe()
        self._scan([{"pair": "BTC/USD", "profit_score": -0.4}])
        with mock.patch.object(evidence_gate, "REQUIRE_POSITIVE_SCAN", False):
            result = self._check()
        self.assertTrue(result["allowed"], result["reasons"])

    def test_unreachable_remote_falls_back_to_local(self):
        self._decision()
        self._vibe()
        self._scan()
        result = self._check(remote_base="http://127.0.0.1:1/raw")
        self.assertTrue(result["allowed"], result["reasons"])
        self.assertEqual(result["evidence"]["source"], "local")


class VibeCheckTest(unittest.TestCase):
    def test_check_vibe_missing_artifact(self):
        empty = Path(tempfile.mkdtemp())
        result = evidence_gate.check_vibe(vibe_dir=empty)
        self.assertFalse(result["allowed"])

    def test_check_vibe_fresh_artifact(self):
        vibe = Path(tempfile.mkdtemp())
        (vibe / "2026-10-23_1722_academic_sp500.txt").write_text(VIBE_LOG, encoding="utf-8")
        result = evidence_gate.check_vibe(vibe_dir=vibe)
        self.assertTrue(result["allowed"], result["reasons"])
        self.assertEqual(result["evidence"]["vibe"]["best_alpha"], "academic_carhart_mom")


if __name__ == "__main__":
    sys.exit(unittest.main())
