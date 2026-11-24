import unittest

from agent.categorize import canonical_url, classify, detect_store, extract_tags, parse_coupon, parse_prices


class StoreDetection(unittest.TestCase):
    def test_url_host_wins(self):
        self.assertEqual(detect_store("https://www.amazon.com/dp/B000TEST00", "[Best Buy] wrong hint in title"), "amazon")
        self.assertEqual(detect_store("https://m.bestbuy.com/site/x", None), "bestbuy")
        self.assertEqual(detect_store("https://amzn.to/abc", None), "amazon")

    def test_bracket_prefix(self):
        self.assertEqual(detect_store("https://slickdeals.net/f/1", "[Amazon] Thing $5"), "amazon")
        self.assertEqual(detect_store("https://www.reddit.com/r/x/1", "[Best Buy] Thing"), "bestbuy")

    def test_at_store_in_text(self):
        self.assertEqual(detect_store("https://slickdeals.net/f/2", "Robot vacuum $279", "Best Buy has it for $279"), "bestbuy")
        self.assertEqual(detect_store("https://slickdeals.net/f/3", "Jacket $59", "Kohl's has the jacket"), "kohls")

    def test_unknown_direct_link(self):
        self.assertEqual(detect_store("https://shop.example.com/item", "Thing"), "other")

    def test_hint(self):
        self.assertEqual(detect_store("https://slickdeals.net/f/4", "Thing", None, hint="target"), "target")


class CanonicalUrl(unittest.TestCase):
    def test_amazon_asin(self):
        self.assertEqual(canonical_url("https://www.amazon.com/Some-Product-Name/dp/B0ABCDEFGH/ref=sr_1_3?tag=x-20&th=1"), "https://www.amazon.com/dp/B0ABCDEFGH")
        self.assertEqual(canonical_url("https://amazon.com/gp/product/B0ABCDEFGH?psc=1"), "https://www.amazon.com/dp/B0ABCDEFGH")

    def test_tracking_params_stripped_and_sorted(self):
        self.assertEqual(canonical_url("https://www.bestbuy.com/site/p/123?skuId=9&utm_source=sd&irclickid=zz&b=2"), "https://www.bestbuy.com/site/p/123?b=2&skuId=9")

    def test_rejects_non_http(self):
        self.assertEqual(canonical_url("javascript:alert(1)"), "")
        self.assertEqual(canonical_url(None), "")


class Prices(unittest.TestCase):
    def test_was(self):
        self.assertEqual(parse_prices('65" TV $499.99 + Free Shipping', "was $799.99"), (499.99, 799.99, 38))

    def test_reg(self):
        self.assertEqual(parse_prices("Air Fryer $69 (Reg. $139)"), (69.0, 139.0, 50))

    def test_parenthetical_minus(self):
        price, lp, pct = parse_prices("[GPU] Card - $429.99 ($499.99 - $70)")
        self.assertEqual((price, lp, pct), (429.99, 499.99, 14))

    def test_percent_only(self):
        price, lp, pct = parse_prices("String Lights - $9.99 (50% off)")
        self.assertEqual(price, 9.99)
        self.assertEqual(pct, 50)
        self.assertAlmostEqual(lp, 19.98, places=2)

    def test_arrow(self):
        self.assertEqual(parse_prices("Headphones $249 -> $149"), (149.0, 249.0, 40))

    def test_thousands(self):
        self.assertEqual(parse_prices("Gaming Desktop - $1,199.99 (was $1,599.99)"), (1199.99, 1599.99, 25))

    def test_no_price(self):
        self.assertEqual(parse_prices("Free shipping on everything"), (None, None, None))

    def test_list_not_lower_than_price(self):
        price, lp, pct = parse_prices("Thing $50 (was $40)")
        self.assertEqual(price, 50.0)
        self.assertIsNone(lp)
        self.assertIsNone(pct)


class Coupons(unittest.TestCase):
    def test_code_forms(self):
        self.assertEqual(parse_coupon("Headphones $149.99 w/ code SAVE30"), "SAVE30")
        self.assertEqual(parse_coupon("Jacket $59.98", "apply promo code friday20 at checkout"), "FRIDAY20")
        self.assertEqual(parse_coupon("PSU $89.99 w/ code BFPSU20"), "BFPSU20")

    def test_rejects_words(self):
        self.assertIsNone(parse_coupon("No code required"))
        self.assertIsNone(parse_coupon("Clip the coupon on the page"))
        self.assertIsNone(parse_coupon("Robot vacuum $279"))


class Classification(unittest.TestCase):
    def test_title_keywords(self):
        self.assertEqual(classify("Xbox wireless controller $39"), "gaming")
        self.assertEqual(classify('65" 4K QLED Smart TV'), "tv-home-theater")
        self.assertEqual(classify("2TB NVMe SSD $89"), "computers")
        self.assertEqual(classify("Self-Emptying Robot Vacuum"), "smart-home")
        self.assertEqual(classify("Men's Insulated Winter Jacket"), "fashion")
        self.assertEqual(classify("Dash Cam Front & Rear 4K"), "automotive")
        self.assertEqual(classify("Mystery thing with no words"), "other")

    def test_hint_used_when_no_match(self):
        self.assertEqual(classify("Something Unmatchable", hint="computers"), "computers")

    def test_hint_vs_title(self):
        # strong title evidence beats the source hint
        self.assertEqual(classify("Xbox wireless controller", hint="computers"), "gaming")
        self.assertEqual(classify("Laptop 16GB RAM 512GB SSD", hint="gaming"), "computers")
        # weak evidence (one short generic keyword) does not
        self.assertEqual(classify("Cheap 4K cable", hint="computers"), "computers")
        # "other" is never a useful hint
        self.assertEqual(classify("Gaming Desktop PC", hint="other"), "computers")

    def test_specific_keyword_beats_generic(self):
        self.assertEqual(classify("Dash Cam Front & Rear 4K"), "automotive")
        self.assertEqual(classify('27" 1440p 165Hz IPS Gaming Monitor'), "computers")
        self.assertEqual(classify("Smart Speaker with Display (3rd Gen)"), "smart-home")
        self.assertEqual(classify("Handheld Console OLED Model"), "gaming")

    def test_description_fallback(self):
        self.assertEqual(classify("Great deal", "Walmart has the queen memory foam mattress"), "home-kitchen")


class Tags(unittest.TestCase):
    def test_tags(self):
        tags = extract_tags("TV Black Friday deal", "Lightning deal, free shipping", coupon="X1")
        self.assertIn("black-friday", tags)
        self.assertIn("lightning", tags)
        self.assertIn("free-shipping", tags)
        self.assertIn("coupon", tags)
        self.assertNotIn("refurbished", tags)
        self.assertEqual(len(tags), len(set(tags)))


if __name__ == "__main__":
    unittest.main()
