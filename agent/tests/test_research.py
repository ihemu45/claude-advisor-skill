import unittest
from types import SimpleNamespace

from agent import config
from agent.research import parse_research_output, pick_categories, research_once, to_raw_deals


class Rotation(unittest.TestCase):
    def test_covers_everything_over_a_day(self):
        pool = ["a", "b", "c", "d", "e"]
        seen = set()
        for h in range(24):
            picked = pick_categories(2, h, pool)
            self.assertEqual(len(picked), 2)
            seen.update(picked)
        self.assertEqual(seen, set(pool))

    def test_zero(self):
        self.assertEqual(pick_categories(0, 3, ["a"]), [])


class OutputParsing(unittest.TestCase):
    def test_fenced(self):
        text = 'Here you go:\n```json\n[{"title": "A", "url": "https://www.amazon.com/dp/B0TESTASIN", "price": 10}]\n```'
        self.assertEqual(parse_research_output(text)[0]["title"], "A")

    def test_unfenced(self):
        self.assertEqual(len(parse_research_output('[{"title": "A"}, {"title": "B"}]')), 2)

    def test_garbage(self):
        self.assertEqual(parse_research_output("no json here"), [])
        self.assertEqual(parse_research_output(""), [])


class Validation(unittest.TestCase):
    def test_filters(self):
        items = [
            {"title": "Good", "url": "https://www.bestbuy.com/site/p/1", "store": "Best Buy", "price": 99.99, "list_price": 149.99, "coupon": None},
            {"title": "Aggregator", "url": "https://slickdeals.net/f/1", "store": "Amazon", "price": 5},
            {"title": "Unknown store", "url": "https://shop.example.com/x", "store": "Example", "price": 5},
            {"title": "No price", "url": "https://www.target.com/p/1", "store": "Target", "price": None},
            {"title": "Bad list", "url": "https://www.target.com/p/2", "store": "Target", "price": "$20", "list_price": 15, "coupon": "null", "expires": "2026-12-01T00:00:00Z"},
        ]
        out = to_raw_deals(items, "gaming", "2026-11-24T00:00:00Z")
        self.assertEqual([d["title"] for d in out], ["Good", "Bad list"])
        self.assertEqual(out[0]["store_hint"], "bestbuy")
        self.assertEqual(out[0]["category_hint"], "gaming")
        self.assertTrue(out[0]["research"])
        self.assertEqual(out[1]["price"], 20.0)
        self.assertIsNone(out[1]["list_price"])
        self.assertIsNone(out[1]["coupon"])
        self.assertEqual(out[1]["expires"], "2026-12-01")


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def fake_client(responses):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(responses)))


def msg(stop_reason, text=None, content=None):
    blocks = content if content is not None else ([SimpleNamespace(type="text", text=text)] if text else [])
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, server_tool_use=SimpleNamespace(web_search_requests=3))
    return SimpleNamespace(stop_reason=stop_reason, content=blocks, usage=usage, model="claude-opus-5-5", stop_details=None)


class RequestLoop(unittest.TestCase):
    def test_pause_turn_then_answer(self):
        paused = msg("pause_turn", content=[SimpleNamespace(type="server_tool_use", name="web_search")])
        done = msg("end_turn", text='```json\n[{"title": "A", "url": "https://www.amazon.com/dp/B0TESTASIN", "store": "Amazon", "price": 10}]\n```')
        client = fake_client([paused, done])
        items, info = research_once(client, "gaming", model="claude-opus-5-5", effort="medium", max_searches=8, today="Monday 2026-11-24")
        self.assertEqual(len(items), 1)
        self.assertEqual(info["continuations"], 1)
        self.assertEqual(info["web_searches"], 6)
        self.assertEqual(info["input_tokens"], 2000)
        second = client.beta.messages.calls[1]
        self.assertEqual(second["messages"][1]["role"], "assistant", "resume resends the assistant turn, no extra user message")
        self.assertEqual(len(second["messages"]), 2)
        first = client.beta.messages.calls[0]
        self.assertEqual(first["tools"][0]["type"], "web_search_20260209")
        self.assertEqual(first["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", first["betas"])
        self.assertEqual(first["output_config"], {"effort": "medium"})

    def test_refusal(self):
        refused = msg("refusal")
        refused.stop_details = SimpleNamespace(category="general_harms")
        items, info = research_once(fake_client([refused]), None, model="m", effort="low", max_searches=2, today="x")
        self.assertEqual(items, [])
        self.assertIn("refused", info["error"])


if __name__ == "__main__":
    unittest.main()
