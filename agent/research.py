"""Optional research step: ask Claude to search the web for live deals.

Runs only when ANTHROPIC_API_KEY is set. Each hourly run researches a rotating
slice of categories (RESEARCH_PER_RUN) plus, optionally, one cross-category
"hottest deals right now" query (RESEARCH_HOT). The call uses the server-side
web_search tool, so Claude does the searching and we only parse the JSON it
returns. The whole step is best-effort: any failure is logged in the run
report and the feeds-only data still ships.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .categorize import canonical_url, detect_store, is_aggregator
from .config import CATEGORY_BY_SLUG, FIXTURES_DIR, RESEARCHABLE_CATEGORIES

MAX_CONTINUATIONS = 4
SOURCE_ID = "claude-research"

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.S)


def pick_categories(per_run: int, hour_index: int, pool: list[str] | None = None) -> list[str]:
    """Rotate through the categories so each one is refreshed a few times a day."""
    pool = pool or RESEARCHABLE_CATEGORIES
    if per_run <= 0 or not pool:
        return []
    per_run = min(per_run, len(pool))
    start = (hour_index * per_run) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(per_run)]


def build_prompt(category_slug: str | None, today: str) -> tuple[str, str]:
    stores = ", ".join(config.RESEARCH_STORES)
    if category_slug:
        cat = CATEGORY_BY_SLUG[category_slug]
        scope = f'the "{cat["name"]}" category ({cat["blurb"].rstrip(".")})'
    else:
        scope = "any category: the single hottest, most-discounted deals across the whole store"
    system = (
        "You are the research agent for dealgrep, a public deals site. You find deals that are live right now "
        "at large US retailers and report them as structured data. You only report deals you actually saw on a "
        "retailer page or a reputable deal listing during this session; you never invent prices, URLs or codes. "
        "Precision matters more than quantity: a short list of real deals beats a long list with guesses."
    )
    user = f"""Today is {today}. Find deals that are live today in {scope}.

Retailers to cover: {stores}. Prefer official retailer product pages. Prioritise Black Friday, Cyber Monday and
other event sales when they are running, then the largest percentage discounts.

Use web_search as needed, then answer with ONE fenced ```json block containing a JSON array of 6 to 15 deals.
Each element must have exactly these keys:
- "title": product name as the retailer lists it (string)
- "url": direct https URL on the retailer's own domain, not an aggregator or search page (string)
- "store": retailer name (string)
- "price": current sale price in USD as a number, no symbols (number)
- "list_price": regular/list price in USD as a number, or null if unknown (number|null)
- "coupon": promo code required at checkout, or null (string|null)
- "expires": ISO date the deal ends if stated, else null (string|null)
- "note": one short sentence on why it is a good deal (string)

Rules: skip anything whose current price you could not confirm; skip expired or sold-out items; no duplicate
products; no text outside the JSON block except a one-line preface if you must."""
    return system, user


def parse_research_output(text: str) -> list[dict]:
    """Pull the JSON array out of Claude's reply. Tolerates a missing fence."""
    if not text:
        return []
    candidates = _JSON_FENCE_RE.findall(text)
    if not candidates:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            candidates = [text[start:end + 1]]
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"\d[\d,]*(?:\.\d{1,2})?", v)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def to_raw_deals(items: list[dict], category_slug: str | None, found_at: str) -> list[dict]:
    """Validate Claude's items and shape them like feed items for the pipeline."""
    out: list[dict] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        url = canonical_url(str(item.get("url") or ""))
        price = _num(item.get("price"))
        if not title or not url or price is None or price <= 0:
            continue
        if is_aggregator(url):
            continue
        store = detect_store(url, title, str(item.get("store") or ""))
        if store == "other":
            # A research result we cannot attribute to a known retailer is too risky to show.
            continue
        list_price = _num(item.get("list_price"))
        if list_price is not None and list_price <= price:
            list_price = None
        coupon = item.get("coupon")
        coupon = str(coupon).strip().upper() if coupon and str(coupon).strip().lower() not in ("null", "none", "n/a") else None
        expires = item.get("expires")
        expires = str(expires)[:10] if expires and re.match(r"^\d{4}-\d{2}-\d{2}", str(expires)) else None
        out.append({
            "source": SOURCE_ID,
            "source_name": "Claude research",
            "weight": 20,
            "title": title[:220],
            "url": url,
            "source_url": None,
            "text": str(item.get("note") or "")[:300],
            "published": found_at,
            "score": None,
            "price": round(price, 2),
            "list_price": round(list_price, 2) if list_price else None,
            "coupon": coupon,
            "expires": expires,
            "store_hint": store,
            "category_hint": category_slug,
            "research": True,
        })
    return out


def _usage_dict(resp) -> dict:
    usage = getattr(resp, "usage", None)
    out = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}
    if usage is None:
        return out
    out["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
    out["output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0)
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        out["web_searches"] = int(getattr(stu, "web_search_requests", 0) or 0)
    return out


def research_once(client, category_slug: str | None, *, model: str, effort: str, max_searches: int, today: str) -> tuple[list[dict], dict]:
    """One research request (plus pause_turn continuations). Returns (items, info)."""
    system, user = build_prompt(category_slug, today)
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    messages = [{"role": "user", "content": user}]
    info = {"category": category_slug or "hot", "stop_reason": None, "continuations": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0, "error": None, "model": model}
    text = ""
    for attempt in range(MAX_CONTINUATIONS + 1):
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        u = _usage_dict(resp)
        for k in ("input_tokens", "output_tokens", "web_searches"):
            info[k] += u[k]
        info["stop_reason"] = resp.stop_reason
        info["model"] = getattr(resp, "model", model)
        if resp.stop_reason == "pause_turn":
            # Server-side tool loop hit its iteration cap: resend as-is to resume.
            messages = [{"role": "user", "content": user}, {"role": "assistant", "content": resp.content}]
            info["continuations"] = attempt + 1
            continue
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            info["error"] = f"refused: {getattr(details, 'category', None)}"
            return [], info
        text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        break
    items = parse_research_output(text)
    if not items and not info["error"]:
        info["error"] = "no JSON array in reply" if text else f"empty reply (stop_reason={info['stop_reason']})"
    return items, info


def run_research(*, sample: bool = False, hour_index: int | None = None, now: datetime | None = None, client=None) -> tuple[list[dict], dict]:
    now = now or datetime.now(timezone.utc)
    hour_index = now.hour if hour_index is None else hour_index
    today = now.strftime("%A %Y-%m-%d")
    found_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {
        "enabled": False,
        "model": config.RESEARCH_MODEL,
        "effort": config.RESEARCH_EFFORT,
        "categories": [],
        "found": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "web_searches": 0,
        "ms": 0,
        "errors": [],
        "skipped": None,
        "calls": [],
    }
    categories: list[str | None] = pick_categories(config.RESEARCH_PER_RUN, hour_index)
    if config.RESEARCH_HOT:
        categories = [None] + categories
    report["categories"] = [c or "hot" for c in categories]

    if sample:
        fixture = Path(FIXTURES_DIR, "research_sample.json")
        if not fixture.exists():
            report["skipped"] = "no research fixture"
            return [], report
        items = json.loads(fixture.read_text(encoding="utf-8"))
        deals: list[dict] = []
        for cat, group in items.items():
            deals.extend(to_raw_deals(group, None if cat == "hot" else cat, found_at))
        report.update(enabled=True, found=len(deals), model="sample", categories=list(items.keys()))
        return deals, report

    if not os.environ.get("ANTHROPIC_API_KEY") and client is None:
        report["skipped"] = "ANTHROPIC_API_KEY not set"
        return [], report
    if not categories:
        report["skipped"] = "RESEARCH_PER_RUN=0 and RESEARCH_HOT off"
        return [], report

    if client is None:
        import anthropic  # imported lazily so the feeds-only path has no SDK dependency at runtime
        client = anthropic.Anthropic(max_retries=2, timeout=600.0)
    else:
        import anthropic  # noqa: F401

    report["enabled"] = True
    t0 = time.monotonic()
    deals = []
    for cat in categories:
        try:
            items, info = research_once(client, cat, model=config.RESEARCH_MODEL, effort=config.RESEARCH_EFFORT, max_searches=config.RESEARCH_MAX_SEARCHES, today=today)
        except Exception as err:  # noqa: BLE001 - the SDK's typed errors all end up here; we log and move on
            items, info = [], {"category": cat or "hot", "error": f"{type(err).__name__}: {str(err)[:200]}", "input_tokens": 0, "output_tokens": 0, "web_searches": 0}
        report["calls"].append(info)
        for k in ("input_tokens", "output_tokens", "web_searches"):
            report[k] += info.get(k, 0)
        if info.get("error"):
            report["errors"].append(f"{info['category']}: {info['error']}")
        converted = to_raw_deals(items, cat, found_at)
        deals.extend(converted)
    report["found"] = len(deals)
    report["ms"] = int((time.monotonic() - t0) * 1000)
    return deals, report
