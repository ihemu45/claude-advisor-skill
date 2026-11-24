"""Feed fetchers. Each parser is a pure function over response text so it can
be tested with fixtures; `run_sources` adds the network and concurrency."""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from .categorize import clean_title, first_url_in, is_aggregator, strip_html
from .config import FIXTURES_DIR, SOURCES_FILE

USER_AGENT = "dealgrep/0.1 (+https://github.com/ihemu45/claude-advisor-skill; hourly deal aggregator)"
TIMEOUT = 25


def load_sources(path: Path | str = SOURCES_FILE) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        sources = json.load(f)
    if not isinstance(sources, list):
        raise ValueError("sources.json must be a list")
    return sources


def http_get(url: str, *, accept: str = "*/*") -> str:
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-US,en;q=0.8"}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    try:
        return _iso(parsedate_to_datetime(s))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return _iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, *names: str) -> str | None:
    for child in el:
        if _strip_ns(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return None


def _atom_link(el: ET.Element) -> str | None:
    for child in el:
        if _strip_ns(child.tag) == "link":
            href = child.get("href")
            if href and child.get("rel", "alternate") == "alternate":
                return href
    return None


def _base_raw(src: dict) -> dict:
    return {
        "source": src["id"],
        "source_name": src.get("name", src["id"]),
        "weight": src.get("weight", 5),
        "store_hint": src.get("store_hint"),
        "category_hint": src.get("category_hint"),
    }


def parse_rss(xml_text: str, src: dict) -> list[dict]:
    """RSS 2.0 or Atom. Returns raw deal dicts."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = [el for el in root.iter() if _strip_ns(el.tag) in ("item", "entry")]
    out: list[dict] = []
    for el in items:
        title = clean_title(_child_text(el, "title"))
        if not title:
            continue
        link = _child_text(el, "link") or _atom_link(el) or _child_text(el, "guid")
        desc_raw = _child_text(el, "description", "summary", "content", "encoded") or ""
        text = strip_html(desc_raw)[:1500]
        deal_url = link
        if link and is_aggregator(link):
            # Reddit/Slickdeals feeds: the outbound link lives inside the HTML body.
            for candidate in re.findall(r'href="([^"]+)"', desc_raw):
                candidate = candidate.replace("&amp;", "&")
                if candidate.startswith("http") and not is_aggregator(candidate):
                    deal_url = candidate
                    break
        raw = _base_raw(src)
        raw.update({
            "title": title,
            "url": deal_url,
            "source_url": link,
            "text": text,
            "published": _parse_date(_child_text(el, "pubDate", "published", "updated")),
            "score": None,
        })
        out.append(raw)
    return out


def parse_reddit_listing(json_text: str, src: dict) -> list[dict]:
    data = json.loads(json_text)
    children = (data.get("data") or {}).get("children") or []
    out: list[dict] = []
    for child in children:
        post = child.get("data") or {}
        if post.get("stickied") or post.get("over_18") or post.get("removed_by_category"):
            continue
        title = clean_title(post.get("title"))
        if not title:
            continue
        permalink = post.get("permalink")
        source_url = f"https://www.reddit.com{permalink}" if permalink else None
        url = post.get("url")
        selftext = strip_html(post.get("selftext") or "")
        if post.get("is_self") or (url and is_aggregator(url)):
            url = first_url_in(post.get("selftext") or "")
            if url and is_aggregator(url):
                url = None
        flair = post.get("link_flair_text") or ""
        created = post.get("created_utc")
        raw = _base_raw(src)
        raw.update({
            "title": title,
            "url": url,
            "source_url": source_url,
            "text": f"{flair} {selftext}".strip()[:1500],
            "published": _iso(datetime.fromtimestamp(created, tz=timezone.utc)) if created else None,
            "score": post.get("score"),
        })
        if flair.lower() in ("expired", "sold out", "dead"):
            continue
        out.append(raw)
    return out


def parse_bestbuy(json_text: str, src: dict) -> list[dict]:
    data = json.loads(json_text)
    products = data.get("products") or []
    out: list[dict] = []
    for p in products:
        name = clean_title(p.get("name"))
        url = p.get("url")
        if not name or not url:
            continue
        cats = " > ".join(c.get("name", "") for c in (p.get("categoryPath") or []) if isinstance(c, dict))
        raw = _base_raw(src)
        raw.update({
            "title": name,
            "url": url,
            "source_url": url,
            "text": cats,
            "published": None,
            "score": p.get("customerReviewAverage"),
            "price": p.get("salePrice"),
            "list_price": p.get("regularPrice"),
            "discount_pct": int(round(p["percentSavings"])) if isinstance(p.get("percentSavings"), (int, float)) else None,
            "store_hint": "bestbuy",
        })
        out.append(raw)
    return out


PARSERS = {"rss": parse_rss, "reddit": parse_reddit_listing, "bestbuy": parse_bestbuy}


def _expand_env(url: str) -> str:
    return re.sub(r"\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), url)


def fetch_source(src: dict, *, sample: bool = False, getter=http_get) -> tuple[list[dict], dict]:
    """Fetch and parse one source. Never raises; failures land in the report."""
    report = {"id": src["id"], "name": src.get("name", src["id"]), "ok": False, "count": 0, "ms": 0, "error": None, "skipped": None, "homepage": src.get("homepage")}
    t0 = time.monotonic()
    kind = src.get("type", "rss")
    parser = PARSERS.get(kind)
    if parser is None:
        report["error"] = f"unknown source type {kind!r}"
        return [], report
    try:
        if sample:
            fixture = src.get("sample")
            if not fixture:
                report["skipped"] = "no sample fixture"
                return [], report
            body = Path(FIXTURES_DIR, fixture).read_text(encoding="utf-8")
        else:
            needs = src.get("requires_env")
            if needs and not os.environ.get(needs):
                report["skipped"] = f"{needs} not set"
                return [], report
            accept = "application/json" if kind in ("reddit", "bestbuy") else "application/rss+xml, application/atom+xml, application/xml, text/xml"
            try:
                body = getter(_expand_env(src["url"]), accept=accept)
            except Exception as primary_err:  # noqa: BLE001 - any network/HTTP failure
                fallback = src.get("fallback_url")
                if not fallback:
                    raise
                body = getter(_expand_env(fallback), accept="application/atom+xml, application/xml")
                parser = parse_rss
                report["error"] = f"primary failed ({type(primary_err).__name__}); used fallback"
        deals = parser(body, src)
        report.update(ok=True, count=len(deals))
        return deals, report
    except Exception as err:  # noqa: BLE001
        report["error"] = f"{type(err).__name__}: {str(err)[:160]}"
        return [], report
    finally:
        report["ms"] = int((time.monotonic() - t0) * 1000)


def run_sources(sources: list[dict], *, sample: bool = False, getter=http_get, workers: int = 6) -> tuple[list[dict], list[dict]]:
    deals: list[dict] = []
    reports: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_source, src, sample=sample, getter=getter): src for src in sources}
        for fut in as_completed(futures):
            d, r = fut.result()
            deals.extend(d)
            reports.append(r)
    order = {s["id"]: i for i, s in enumerate(sources)}
    reports.sort(key=lambda r: order.get(r["id"], 999))
    return deals, reports
