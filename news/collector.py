"""Headline collector for CI (stdlib only). Reads primary/mainstream RSS feeds, tags each headline by symbol/topic, keeps the last 48 h
in research/reports/news/latest.json. Tone is a crude word list: a hint for a human, not a signal until the ledger scores it.
Never raises: one dead feed is recorded in feeds[] and the others still run.

    python -m news.collector
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "reports" / "news" / "latest.json"
KEEP_HOURS, MAX_ITEMS = 48, 400
ATOM = "{http://www.w3.org/2005/Atom}"

FEEDS = {
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ecb": "https://www.ecb.europa.eu/rss/press.xml",
    "sec": "https://www.sec.gov/news/pressreleases.rss",
    "bbc-business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bbc-world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "nyt-business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "investing": "https://www.investing.com/rss/news.rss",
    "cointelegraph": "https://cointelegraph.com/rss",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "guardian-business": "https://www.theguardian.com/business/rss",
    "gnews-bitcoin": "https://news.google.com/rss/search?q=bitcoin+when:1d&hl=en-US&gl=US&ceid=US:en",
    "gnews-fx": "https://news.google.com/rss/search?q=forex+OR+ECB+OR+%22Federal+Reserve%22+when:1d&hl=en-US&gl=US&ceid=US:en",
}
TAGS = {
    "BTC-USD": r"\b(bitcoin|btc)\b", "ETH-USD": r"\b(ethereum|ether|eth)\b", "SOL-USD": r"\b(solana|sol)\b",
    "AAPL": r"\b(apple|aapl|iphone)\b", "MSFT": r"\b(microsoft|msft)\b",
    "EURUSD=X": r"\b(euro|eur|ecb|eurozone|lagarde)\b", "USD": r"\b(dollar|fed|federal reserve|fomc|powell|treasury)\b",
    "rates": r"\b(interest rate|rate cut|rate hike|inflation|cpi|yields?)\b",
    "politics": r"\b(election|tariff|sanction|war|ceasefire|parliament|congress|trade deal)\b",
    "regulation": r"\b(sec|regulat\w+|ban|lawsuit|etf approval)\b",
}
POS = set("surge rally gain gains rise rises record beat beats approve approved approval growth cut cuts boost strong upgrade soar jump deal".split())
NEG = set("fall falls drop drops plunge crash slump loss losses ban lawsuit fraud hack default recession war sanction downgrade weak fear selloff slide".split())


def tags(title):
    return [k for k, rx in TAGS.items() if re.search(rx, title, re.I)]


def tone(title):
    words = re.findall(r"[a-z]+", title.lower())
    p, n = sum(w in POS for w in words), sum(w in NEG for w in words)
    return round((p - n) / max(1, p + n), 2)


def _when(raw, now):
    try:
        if "," in raw or raw[:1].isalpha():
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return now


def parse(xml_bytes, now=None):
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.findall(".//item") or root.findall(f".//{ATOM}entry"):
        title = " ".join((it.findtext("title") or it.findtext(ATOM + "title") or "").split())
        link_el = it.find(ATOM + "link")
        link = (it.findtext("link") or (link_el.get("href") if link_el is not None else "") or "").strip()
        raw = it.findtext("pubDate") or it.findtext(ATOM + "updated") or it.findtext(ATOM + "published") or ""
        if title and link:
            out.append({"ts": _when(raw, now).strftime("%Y-%m-%dT%H:%M:%SZ"), "title": title, "link": link})
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 alphacode-news"})
    return urllib.request.urlopen(req, timeout=25).read()


def collect(feeds=None, getter=fetch, now=None, previous=None):
    """Return the document to write. `previous` (an older latest.json) keeps headlines a feed no longer lists."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=KEEP_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = {i["link"]: i for i in (previous or {}).get("items", [])}
    status = []
    for name, url in (feeds or FEEDS).items():
        try:
            rows = parse(getter(url), now)
            for r in rows:
                items[r["link"]] = {**r, "source": name, "tags": tags(r["title"]), "tone": tone(r["title"])}
            status.append({"feed": name, "ok": True, "items": len(rows)})
        except Exception as exc:  # one dead feed must not stop the others
            status.append({"feed": name, "ok": False, "error": str(exc)[:80]})
    kept = sorted((i for i in items.values() if i["ts"] >= cutoff), key=lambda i: i["ts"], reverse=True)[:MAX_ITEMS]
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "keep_hours": KEEP_HOURS, "feeds": status,
            "ok_feeds": sum(s["ok"] for s in status), "items": kept}


def main():
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    doc = collect(previous=previous)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"news: {doc['ok_feeds']}/{len(doc['feeds'])} feeds ok, {len(doc['items'])} headlines kept")
    for s in doc["feeds"]:
        if not s["ok"]:
            print(f"::warning::feed {s['feed']} failed: {s['error']}")
    return 0 if doc["ok_feeds"] else 1  # all feeds dead = red run; a partial outage stays green with warnings


if __name__ == "__main__":
    sys.exit(main())
