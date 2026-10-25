import sys
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from news import collector  # noqa: E402
import alert_issue  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
RSS = b"""<rss><channel>
<item><title>Bitcoin surge as ECB signals rate cut</title><link>http://a/1</link><pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Apple faces lawsuit over iPhone</title><link>http://a/2</link><pubDate>Sat, 20 Oct 2026 11:00:00 GMT</pubDate></item>
<item><title></title><link>http://a/3</link></item></channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Tariff war: dollar falls</title>
<link href="http://b/1"/><updated>2026-10-25T11:00:00Z</updated></entry></feed>"""


def getter(url):
    if url == "bad":
        raise OSError("boom")
    return ATOM if url == "atom" else RSS


class NewsTest(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        rows = collector.parse(RSS, NOW)
        self.assertEqual([r["link"] for r in rows], ["http://a/1", "http://a/2"])  # empty title skipped
        self.assertEqual(rows[0]["ts"], "2026-10-25T10:00:00Z")
        self.assertEqual(collector.parse(ATOM, NOW)[0]["link"], "http://b/1")

    def test_tags_and_tone(self):
        self.assertEqual(collector.tags("Bitcoin surge as ECB signals rate cut"), ["BTC-USD", "EURUSD=X", "rates"])
        self.assertEqual(collector.tags("Solitary confinement"), [])  # 'sol' inside a word is not SOL
        self.assertGreater(collector.tone("Bitcoin surge to record"), 0)
        self.assertLess(collector.tone("Fraud and lawsuit hit exchange"), 0)

    def test_dead_feed_does_not_stop_others_and_old_items_drop(self):
        doc = collector.collect({"a": "rss", "b": "atom", "c": "bad"}, getter, NOW)
        self.assertEqual(doc["ok_feeds"], 2)
        self.assertFalse([s for s in doc["feeds"] if s["feed"] == "c"][0]["ok"])
        links = [i["link"] for i in doc["items"]]
        self.assertIn("http://a/1", links)
        self.assertNotIn("http://a/2", links)  # 5 days old > 48 h

    def test_previous_items_are_kept_until_they_expire(self):
        first = collector.collect({"a": "rss"}, getter, NOW)
        second = collector.collect({"z": "bad"}, getter, NOW, previous=first)
        self.assertEqual([i["link"] for i in second["items"]], ["http://a/1"])
        self.assertEqual(second["ok_feeds"], 0)

    def test_every_feed_url_is_https(self):
        self.assertTrue(all(u.startswith("https://") for u in collector.FEEDS.values()))


class AlertTest(unittest.TestCase):
    def test_decide(self):
        open_ = {alert_issue.title("Outcome"): 7}
        self.assertEqual(alert_issue.decide({}, "Outcome", "failure"), ("create", None))
        self.assertEqual(alert_issue.decide(open_, "Outcome", "failure"), ("comment", 7))
        self.assertEqual(alert_issue.decide(open_, "Outcome", "success"), ("close", 7))
        self.assertEqual(alert_issue.decide({}, "Outcome", "success"), ("none", None))
        self.assertEqual(alert_issue.decide(open_, "Warmup", "failure"), ("create", None))  # other workflow, own issue
        self.assertEqual(alert_issue.decide(open_, "Outcome", "cancelled"), ("none", None))
        self.assertEqual(alert_issue.decide({}, "Outcome", "timed_out"), ("create", None))
        self.assertEqual(alert_issue.decide(open_, "Outcome", "timed_out"), ("comment", 7))

    def test_other_branch_never_opens_or_closes(self):
        with mock.patch.object(alert_issue, "gh", side_effect=AssertionError("must not call gh")):
            self.assertEqual(alert_issue.main(["x", "Outcome", "success", "http://run", "feature/x"]), 0)
            self.assertEqual(alert_issue.main(["x", "Outcome", "failure", "http://run", "fix/y"]), 0)


if __name__ == "__main__":
    unittest.main()
