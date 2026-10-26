import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preflight


def _hits(text):
    return [label for rx, label in preflight.SECRET_PATTERNS if rx.search(text)]


def test_real_key_is_caught():
    assert "API secret key (sk-...)" in _hits('x = "sk-' + "a1B2" * 10 + '"')


def test_news_slug_is_not_a_key():
    url = "https://www.investing.com/news/stock-market-news/musk-says-spacex-raised-40-billion-in-debt-for-gpus-4971248"
    assert _hits(url) == []
