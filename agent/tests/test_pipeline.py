import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import config, pipeline
from agent.pipeline import build_meta, finalize, iso, load_previous, merge, normalize, run_summary

NOW = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)


def raw(**over):
    base = {"source": "sd", "source_name": "Slickdeals", "weight": 25, "title": "Air Fryer $69 (Reg. $139)", "url": "https://www.walmart.com/ip/123?irclickid=x", "source_url": "https://slickdeals.net/f/1", "text": "Walmart has it. Black Friday.", "published": "2026-11-24T13:00:00Z", "score": None}
    base.update(over)
    return base


class Normalize(unittest.TestCase):
    def test_fields(self):
        d = normalize(raw(), iso(NOW))
        self.assertEqual(d["store"], "walmart")
        self.assertEqual(d["url"], "https://www.walmart.com/ip/123")
        self.assertEqual(d["source_url"], "https://slickdeals.net/f/1")
        self.assertEqual((d["price"], d["list_price"], d["discount_pct"]), (69.0, 139.0, 50))
        self.assertEqual(d["category"], "home-kitchen")
        self.assertIn("black-friday", d["tags"])
        self.assertEqual(d["first_seen"], "2026-11-24T13:00:00Z")
        self.assertEqual(d["price_history"], [[iso(NOW), 69.0]])

    def test_same_product_two_sources_same_id(self):
        a = normalize(raw(), iso(NOW))
        b = normalize(raw(source="rd", url="https://www.walmart.com/ip/123?utm_source=reddit", text=""), iso(NOW))
        self.assertEqual(a["id"], b["id"])

    def test_no_link_dropped(self):
        self.assertIsNone(normalize(raw(url=None, source_url=None), iso(NOW)))

    def test_structured_prices_from_source(self):
        d = normalize(raw(price=549.99, list_price=899.99, discount_pct=39, store_hint="bestbuy", url="https://www.bestbuy.com/site/p/1"), iso(NOW))
        self.assertEqual(d["discount_pct"], 39)
        self.assertEqual(d["store"], "bestbuy")


class Merge(unittest.TestCase):
    def test_new_then_repeat(self):
        fresh = [normalize(raw(), iso(NOW))]
        deals, new = merge([], fresh, NOW)
        self.assertEqual((len(deals), new), (1, 1))
        later = NOW + timedelta(hours=1)
        fresh2 = [normalize(raw(title="Air Fryer $59 (Reg. $139)", source="rd"), iso(later))]
        deals2, new2 = merge(deals, fresh2, later)
        self.assertEqual((len(deals2), new2), (1, 0))
        d = deals2[0]
        self.assertEqual(d["seen_count"], 2)
        self.assertEqual(d["price"], 59.0)
        self.assertEqual(d["sources"], ["rd", "sd"])
        self.assertEqual(d["first_seen"], "2026-11-24T13:00:00Z")
        self.assertEqual([p for _, p in d["price_history"]], [69.0, 59.0])

    def test_duplicate_within_run_counts_once(self):
        fresh = [normalize(raw(), iso(NOW)), normalize(raw(source="rd", score=300), iso(NOW))]
        deals, new = merge([], fresh, NOW)
        self.assertEqual((len(deals), new), (1, 1))
        self.assertEqual(deals[0]["seen_count"], 1)
        self.assertEqual(deals[0]["score"], 300)

    def test_stale_dropped(self):
        old = normalize(raw(), iso(NOW - timedelta(hours=config.STALE_HOURS + 1)))
        deals, _ = merge([old], [], NOW)
        self.assertEqual(deals, [])
        recent = normalize(raw(), iso(NOW - timedelta(hours=2)))
        deals, _ = merge([recent], [], NOW)
        self.assertEqual(len(deals), 1)

    def test_expired_dropped(self):
        d = normalize(raw(expires="2026-11-20"), iso(NOW))
        deals, _ = merge([], [d], NOW)
        self.assertEqual(deals, [])


class Finalize(unittest.TestCase):
    def test_heat_and_tags(self):
        hot = normalize(raw(), iso(NOW))
        meh = normalize(raw(title="Socks $9", url="https://www.target.com/p/9", text="", published="2026-11-20T00:00:00Z", weight=5), iso(NOW))
        deals = finalize([meh, hot], NOW)
        self.assertEqual(deals[0]["title"], hot["title"])
        self.assertGreater(deals[0]["heat"], deals[1]["heat"])
        self.assertIn("new", deals[0]["tags"])
        self.assertNotIn("new", deals[1]["tags"])
        for d in deals:
            self.assertTrue(0 <= d["heat"] <= 100)

    def test_price_drop_tag(self):
        d = normalize(raw(), iso(NOW))
        d["price_history"] = [["2026-11-24T10:00:00Z", 79.0], [iso(NOW), 69.0]]
        self.assertIn("price-drop", finalize([d], NOW)[0]["tags"])


class Meta(unittest.TestCase):
    def test_counts_and_summary(self):
        deals = finalize([normalize(raw(), iso(NOW))], NOW)
        reports = [{"id": "sd", "ok": True, "count": 1, "error": None}, {"id": "rd", "ok": False, "count": 0, "error": "boom"}, {"id": "bb", "ok": False, "skipped": "no key", "count": 0, "error": None}]
        research = {"enabled": True, "categories": ["hot", "gaming"], "found": 0, "errors": ["gaming: refused"], "web_searches": 4, "input_tokens": 10, "output_tokens": 2, "model": "m"}
        meta = build_meta(deals, now=NOW, sample=False, source_reports=reports, research_report=research, new_count=1, duration_ms=5)
        self.assertEqual(meta["total"], 1)
        self.assertEqual(meta["by_category"]["home-kitchen"], 1)
        self.assertEqual(meta["by_store"], {"walmart": 1})
        self.assertEqual([c["slug"] for c in meta["categories"]][:2], ["electronics", "computers"])
        s = run_summary(meta)
        self.assertEqual((s["sources_ok"], s["sources_total"]), (1, 2))
        self.assertEqual(s["source_errors"], ["rd: boom"])
        self.assertEqual(s["research"]["errors"], ["gaming: refused"])


class Previous(unittest.TestCase):
    def test_local_sample_ignored_real_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "deals.json").write_text(json.dumps({"sample": True, "deals": [{"id": "x"}]}))
            deals, runs, origin = load_previous(out, None)
            self.assertEqual((deals, origin), ([], "none"))
            (out / "deals.json").write_text(json.dumps({"sample": False, "deals": [{"id": "x"}]}))
            (out / "runs.json").write_text(json.dumps([{"at": "t", "sample": True}, {"at": "u"}]))
            deals, runs, origin = load_previous(out, None)
            self.assertEqual((len(deals), origin), (1, "local-file"))
            self.assertEqual(runs, [{"at": "u"}])


if __name__ == "__main__":
    unittest.main()
