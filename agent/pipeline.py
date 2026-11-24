"""Normalise raw feed items into deals, merge with the previous run, score,
and write the JSON files the site reads."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import __version__, config
from .categorize import canonical_url, classify, detect_store, extract_tags, parse_coupon, parse_prices
from .config import CATEGORIES, STORES

ISO = "%Y-%m-%dT%H:%M:%SZ"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None


def _norm_title_key(title: str) -> str:
    t = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", "", title.lower())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if len(w) > 2][:10]
    return " ".join(words)


def deal_id(url: str, store: str, title: str) -> str:
    key = url if url else f"{store}|{_norm_title_key(title)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def normalize(raw: dict, now_iso: str) -> dict | None:
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    url = canonical_url(raw.get("url"))
    source_url = raw.get("source_url") or None
    text = raw.get("text") or ""
    store = detect_store(url or source_url, title, text, raw.get("store_hint"))
    if url and store == "other" and raw.get("store_hint") in STORES:
        store = raw["store_hint"]

    if raw.get("price") is not None:
        price = raw.get("price")
        list_price = raw.get("list_price")
        pct = raw.get("discount_pct")
        if price is not None and list_price and list_price > price:
            pct = int(round((1 - price / list_price) * 100))
        elif list_price is not None and price is not None and list_price <= price:
            list_price = None
    else:
        price, list_price, pct = parse_prices(title, text)

    coupon = raw.get("coupon") or parse_coupon(title, text)
    category = classify(title, text, raw.get("category_hint"))
    tags = extract_tags(title, text, coupon)
    if raw.get("research"):
        tags.append("researched")

    link = url or source_url
    if not link:
        return None
    return {
        "id": deal_id(url, store, title),
        "title": title[:220],
        "url": link,
        "source_url": source_url if source_url and source_url != link else None,
        "store": store,
        "store_name": STORES.get(store, STORES["other"])["name"],
        "category": category,
        "price": price,
        "list_price": list_price,
        "discount_pct": pct,
        "coupon": coupon,
        "expires": raw.get("expires"),
        "note": (text[:200] if raw.get("research") else None),
        "tags": tags,
        "sources": [raw.get("source", "unknown")],
        "score": raw.get("score") if isinstance(raw.get("score"), (int, float)) else None,
        "weight": raw.get("weight", 5),
        "published": raw.get("published"),
        "first_seen": raw.get("published") or now_iso,
        "last_seen": now_iso,
        "seen_count": 1,
        "price_history": [[now_iso, price]] if price is not None else [],
        "heat": 0,
    }


def _merge_fields(old: dict, new: dict, now_iso: str) -> dict:
    merged = dict(old)
    for k in ("title", "url", "store", "store_name", "category", "coupon", "expires", "note", "source_url"):
        if new.get(k) not in (None, "", []):
            merged[k] = new[k]
    if new.get("price") is not None:
        merged["price"] = new["price"]
        merged["list_price"] = new.get("list_price") if new.get("list_price") else merged.get("list_price")
        if merged.get("list_price") and merged["list_price"] <= merged["price"]:
            merged["list_price"] = None
        if merged.get("list_price"):
            merged["discount_pct"] = int(round((1 - merged["price"] / merged["list_price"]) * 100))
        elif new.get("discount_pct") is not None:
            merged["discount_pct"] = new["discount_pct"]
    merged["tags"] = sorted(set(old.get("tags", [])) | set(new.get("tags", [])))
    merged["sources"] = sorted(set(old.get("sources", [])) | set(new.get("sources", [])))
    merged["score"] = max([s for s in (old.get("score"), new.get("score")) if isinstance(s, (int, float))], default=None)
    merged["weight"] = max(old.get("weight", 5), new.get("weight", 5))
    first_candidates = [d for d in (parse_iso(old.get("first_seen")), parse_iso(new.get("first_seen"))) if d]
    merged["first_seen"] = iso(min(first_candidates)) if first_candidates else now_iso
    merged["last_seen"] = now_iso
    merged["seen_count"] = int(old.get("seen_count", 1)) + 1
    hist = list(old.get("price_history") or [])
    if new.get("price") is not None:
        last = hist[-1] if hist else None
        if not last or last[1] != new["price"]:
            hist.append([now_iso, new["price"]])
        elif last and (parse_iso(last[0]) or datetime.min.replace(tzinfo=timezone.utc)) < (parse_iso(now_iso) or datetime.now(timezone.utc)) - timedelta(hours=6):
            hist.append([now_iso, new["price"]])
    merged["price_history"] = hist[-config.PRICE_HISTORY_LIMIT:]
    return merged


def merge(previous: list[dict], fresh: list[dict], now: datetime) -> tuple[list[dict], int]:
    """Combine the previous run's deals with freshly fetched ones.

    Returns (deals, new_count). Deals unseen for STALE_HOURS are dropped."""
    now_iso = iso(now)
    by_id: dict[str, dict] = {d["id"]: d for d in previous if isinstance(d, dict) and d.get("id")}
    seen_this_run: set[str] = set()
    new_count = 0
    for d in fresh:
        did = d["id"]
        if did in seen_this_run:
            # Same deal from two sources in one run: merge without bumping seen_count twice.
            existing = by_id[did]
            merged = _merge_fields(existing, d, now_iso)
            merged["seen_count"] = existing["seen_count"]
            by_id[did] = merged
            continue
        seen_this_run.add(did)
        if did in by_id:
            by_id[did] = _merge_fields(by_id[did], d, now_iso)
        else:
            by_id[did] = d
            new_count += 1
    cutoff = now - timedelta(hours=config.STALE_HOURS)
    kept = []
    for d in by_id.values():
        last = parse_iso(d.get("last_seen"))
        if last and last < cutoff:
            continue
        exp = d.get("expires")
        if exp:
            try:
                if datetime.strptime(exp[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1) < now:
                    continue
            except ValueError:
                pass
        kept.append(d)
    return kept, new_count


def compute_heat(deal: dict, now: datetime) -> int:
    heat = 0.0
    pct = deal.get("discount_pct") or 0
    heat += min(pct, 70) * 0.8
    heat += min(deal.get("weight", 5), 25)
    score = deal.get("score")
    if isinstance(score, (int, float)) and score > 5:
        heat += min(score / 20.0, 20)
    heat += min(len(deal.get("sources", [])) - 1, 3) * 6
    tags = set(deal.get("tags", []))
    if "coupon" in tags:
        heat += 4
    if tags & {"black-friday", "cyber-monday"}:
        heat += 8
    if "today-only" in tags or "lightning" in tags:
        heat += 5
    if "refurbished" in tags:
        heat -= 6
    first = parse_iso(deal.get("first_seen"))
    if first:
        age_h = (now - first).total_seconds() / 3600
        if age_h <= 6:
            heat += 10
        elif age_h <= 24:
            heat += 5
        elif age_h > 72:
            heat -= 5
    hist = deal.get("price_history") or []
    if len(hist) >= 2 and hist[-1][1] is not None and hist[-2][1] is not None and hist[-1][1] < hist[-2][1]:
        heat += 6
    return int(max(0, min(100, round(heat))))


def finalize(deals: list[dict], now: datetime) -> list[dict]:
    for d in deals:
        d["heat"] = compute_heat(d, now)
        tags = set(d.get("tags", []))
        first = parse_iso(d.get("first_seen"))
        if first and (now - first) <= timedelta(hours=6):
            tags.add("new")
        else:
            tags.discard("new")
        hist = d.get("price_history") or []
        if len(hist) >= 2 and hist[-1][1] is not None and hist[-2][1] is not None and hist[-1][1] < hist[-2][1]:
            tags.add("price-drop")
        d["tags"] = sorted(tags)
    deals.sort(key=lambda d: (-d["heat"], -(d.get("discount_pct") or 0), d.get("title", "")))
    return deals[: config.MAX_DEALS]


def build_meta(deals: list[dict], *, now: datetime, sample: bool, source_reports: list[dict], research_report: dict, new_count: int, duration_ms: int) -> dict:
    by_cat: dict[str, int] = {c["slug"]: 0 for c in CATEGORIES}
    by_store: dict[str, int] = {}
    for d in deals:
        by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
        by_store[d["store"]] = by_store.get(d["store"], 0) + 1
    return {
        "site": config.SITE_NAME,
        "agent_version": __version__,
        "generated_at": iso(now),
        "sample": sample,
        "total": len(deals),
        "new_this_run": new_count,
        "with_coupon": sum(1 for d in deals if d.get("coupon")),
        "black_friday": sum(1 for d in deals if "black-friday" in d.get("tags", []) or "cyber-monday" in d.get("tags", [])),
        "by_category": by_cat,
        "by_store": dict(sorted(by_store.items(), key=lambda kv: -kv[1])),
        "categories": [{"slug": c["slug"], "name": c["name"], "blurb": c["blurb"]} for c in CATEGORIES],
        "stores": {slug: {"name": s["name"], "homepage": ("https://" + s["hosts"][0]) if s["hosts"] else None} for slug, s in STORES.items()},
        "sources": source_reports,
        "research": research_report,
        "duration_ms": duration_ms,
        "stale_hours": config.STALE_HOURS,
    }


def run_summary(meta: dict) -> dict:
    src = meta.get("sources", [])
    r = meta.get("research", {}) or {}
    return {
        "at": meta["generated_at"],
        "total": meta["total"],
        "new": meta["new_this_run"],
        "sources_ok": sum(1 for s in src if s.get("ok")),
        "sources_total": sum(1 for s in src if not s.get("skipped")),
        "source_errors": [f"{s['id']}: {s['error']}" for s in src if s.get("error") and not s.get("ok")],
        "research": {"enabled": r.get("enabled", False), "categories": r.get("categories", []), "found": r.get("found", 0), "web_searches": r.get("web_searches", 0), "input_tokens": r.get("input_tokens", 0), "output_tokens": r.get("output_tokens", 0), "errors": r.get("errors", []), "model": r.get("model")},
        "duration_ms": meta.get("duration_ms", 0),
        "sample": meta.get("sample", False),
    }


def _read_json(path: Path) -> dict | list | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_previous(out_dir: Path, previous_url: str | None) -> tuple[list[dict], list[dict], str]:
    """Previous deals and run log. Prefers the live site (`previous_url`), then
    the local files; sample data is never used as history."""
    deals: list[dict] = []
    runs: list[dict] = []
    origin = "none"
    if previous_url:
        base = previous_url.rstrip("/")
        try:
            headers = {"User-Agent": "dealgrep-agent/" + __version__, "Cache-Control": "no-cache"}
            bust = f"?t={int(datetime.now(timezone.utc).timestamp())}"  # GitHub Pages sits behind a CDN cache
            r = requests.get(f"{base}/deals.json{bust}", headers=headers, timeout=30)
            if r.ok:
                data = r.json()
                if isinstance(data, dict) and not data.get("sample") and isinstance(data.get("deals"), list):
                    deals = data["deals"]
                    origin = "live-site"
            r2 = requests.get(f"{base}/runs.json{bust}", headers=headers, timeout=30)
            if r2.ok and isinstance(r2.json(), list):
                runs = [x for x in r2.json() if isinstance(x, dict) and not x.get("sample")]
        except (requests.RequestException, ValueError):
            pass
    if not deals:
        local = _read_json(out_dir / "deals.json")
        if isinstance(local, dict) and not local.get("sample") and isinstance(local.get("deals"), list):
            deals = local["deals"]
            origin = "local-file"
        if not runs:
            local_runs = _read_json(out_dir / "runs.json")
            if isinstance(local_runs, list):
                runs = [x for x in local_runs if isinstance(x, dict) and not x.get("sample")]
    return deals, runs, origin


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    tmp.replace(path)
