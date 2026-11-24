import os
import unittest
from pathlib import Path

from agent.config import FIXTURES_DIR
from agent.sources import fetch_source, load_sources, parse_bestbuy, parse_reddit_listing, parse_rss, run_sources


def fx(name: str) -> str:
    return Path(FIXTURES_DIR, name).read_text(encoding="utf-8")


SRC_RSS = {"id": "sd", "name": "Slickdeals", "type": "rss", "url": "https://example.invalid/rss", "weight": 25}
SRC_REDDIT = {"id": "rd", "name": "r/deals", "type": "reddit", "url": "https://example.invalid/r.json", "fallback_url": "https://example.invalid/r.rss", "weight": 10}
SRC_BB = {"id": "bb", "name": "Best Buy", "type": "bestbuy", "url": "https://example.invalid/bb?apiKey={BESTBUY_API_KEY}", "requires_env": "BESTBUY_API_KEY", "store_hint": "bestbuy"}


class RssParsing(unittest.TestCase):
    def test_items_and_outbound_links(self):
        items = parse_rss(fx("slickdeals_frontpage.xml"), SRC_RSS)
        self.assertEqual(len(items), 12)
        first = items[0]
        self.assertTrue(first["title"].startswith('65" 4K QLED'))
        self.assertEqual(first["url"], "https://www.amazon.com/s?k=65+inch+qled+tv")  # pulled from description, not the slickdeals link
        self.assertEqual(first["source_url"], "https://slickdeals.net/deals/tv/")
        self.assertIn("Was $799.99", first["text"])
        self.assertEqual(first["published"], "2026-11-24T13:05:00Z")
        self.assertEqual(first["source"], "sd")

    def test_atom(self):
        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
        <entry><title>Thing $5</title><link rel="alternate" href="https://www.reddit.com/r/x/1"/><updated>2026-11-24T10:00:00Z</updated>
        <content type="html">&lt;a href="https://www.target.com/p/1"&gt;[link]&lt;/a&gt; &lt;a href="https://www.reddit.com/r/x/1"&gt;[comments]&lt;/a&gt;</content></entry></feed>"""
        items = parse_rss(atom, SRC_RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.target.com/p/1")
        self.assertEqual(items[0]["published"], "2026-11-24T10:00:00Z")


class RedditParsing(unittest.TestCase):
    def test_listing(self):
        items = parse_reddit_listing(fx("reddit_deals.json"), SRC_REDDIT)
        titles = [i["title"] for i in items]
        self.assertFalse(any("sidebar" in t for t in titles), "stickied post must be skipped")
        self.assertFalse(any("Expired" in t for t in titles), "expired flair must be skipped")
        self.assertEqual(len(items), 7)
        monitor = next(i for i in items if "Monitor" in i["title"])
        self.assertEqual(monitor["score"], 412)
        self.assertTrue(monitor["url"].startswith("https://www.amazon.com/"))
        self.assertEqual(monitor["source_url"], "https://www.reddit.com/r/deals/comments/bbb222/monitor/")
        pw = next(i for i in items if "Password" in i["title"])
        self.assertEqual(pw["url"], "https://www.bestbuy.com/site/searchpage.jsp?st=password+manager", "self post link extracted from body")


class BestBuyParsing(unittest.TestCase):
    def test_products(self):
        items = parse_bestbuy(fx("bestbuy.json"), SRC_BB)
        self.assertEqual(len(items), 8)
        tv = items[0]
        self.assertEqual(tv["price"], 549.99)
        self.assertEqual(tv["list_price"], 899.99)
        self.assertEqual(tv["discount_pct"], 39)
        self.assertEqual(tv["store_hint"], "bestbuy")
        self.assertIn("TVs", tv["text"])


class Fetching(unittest.TestCase):
    def test_sample_mode(self):
        deals, report = fetch_source({**SRC_RSS, "sample": "slickdeals_popular.xml"}, sample=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["count"], 6)
        self.assertEqual(len(deals), 6)

    def test_sample_without_fixture_is_skipped(self):
        deals, report = fetch_source(SRC_RSS, sample=True)
        self.assertEqual(deals, [])
        self.assertEqual(report["skipped"], "no sample fixture")

    def test_requires_env(self):
        os.environ.pop("BESTBUY_API_KEY", None)
        deals, report = fetch_source(SRC_BB, getter=lambda url, accept: self.fail("must not fetch"))
        self.assertEqual(report["skipped"], "BESTBUY_API_KEY not set")

    def test_env_substitution(self):
        os.environ["BESTBUY_API_KEY"] = "k123"
        seen = {}
        def getter(url, accept):
            seen["url"] = url
            return fx("bestbuy.json")
        try:
            deals, report = fetch_source(SRC_BB, getter=getter)
        finally:
            os.environ.pop("BESTBUY_API_KEY", None)
        self.assertIn("apiKey=k123", seen["url"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["count"], 8)

    def test_fallback_on_failure(self):
        calls = []
        def getter(url, accept):
            calls.append(url)
            if url.endswith(".json"):
                raise ConnectionError("blocked")
            return """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Thing $5</title><link href="https://www.reddit.com/r/x/1"/><content type="html">&lt;a href="https://www.target.com/p/1"&gt;[link]&lt;/a&gt;</content></entry></feed>"""
        deals, report = fetch_source(SRC_REDDIT, getter=getter)
        self.assertEqual(len(calls), 2)
        self.assertTrue(report["ok"])
        self.assertIn("used fallback", report["error"])
        self.assertEqual(deals[0]["url"], "https://www.target.com/p/1")

    def test_total_failure_is_reported_not_raised(self):
        def getter(url, accept):
            raise TimeoutError("slow")
        deals, report = fetch_source(SRC_RSS, getter=getter)
        self.assertEqual(deals, [])
        self.assertFalse(report["ok"])
        self.assertIn("TimeoutError", report["error"])

    def test_run_sources_sample_from_registry(self):
        sources = load_sources()
        deals, reports = run_sources(sources, sample=True)
        self.assertEqual([r["id"] for r in reports], [s["id"] for s in sources], "reports keep registry order")
        ok = [r for r in reports if r["ok"]]
        self.assertGreaterEqual(len(ok), 5)
        self.assertGreater(len(deals), 30)


if __name__ == "__main__":
    unittest.main()
