"""Pure functions that turn messy feed text into structured deal fields.

Everything here is deterministic and has no network access so it can be unit
tested with fixtures.
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import AGGREGATOR_HOSTS, CATEGORIES, STORES, TRACKING_PARAMS

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_BRACKET_PREFIX_RE = re.compile(r"^\s*[\[\(]([^\]\)]{1,40})[\]\)]\s*")
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_PCT_RE = re.compile(r"(\d{1,2})\s?%\s?(?:off|discount|savings?)", re.I)
_LIST_MARKER_RE = re.compile(
    r"(?:was|reg\.?|regularly|regular price|list(?: price)?|orig\.?|originally|msrp|normally|retail|compare at|valued at)"
    r"\s*[:\-]?\s*\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
    re.I,
)
_ARROW_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:->|→|=>|to)\s*\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)
_COUPON_RE = re.compile(
    r"(?:promo\s*code|coupon\s*code|discount\s*code|w/\s*code|with\s*code|use\s*code|code)\s*[:\-]?\s*[\"'“]?([A-Z0-9][A-Z0-9\-]{3,24})[\"'”]?",
    re.I,
)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)")

_CATEGORY_MATCHERS: list[tuple[int, str, re.Pattern]] = []
for _cat in CATEGORIES:
    if not _cat["keywords"]:
        continue
    _alts = sorted((re.escape(k) for k in _cat["keywords"]), key=len, reverse=True)
    _CATEGORY_MATCHERS.append((_cat["priority"], _cat["slug"], re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(_alts), re.I)))
_CATEGORY_MATCHERS.sort(key=lambda t: t[0])
CATEGORY_PRIORITY = {c["slug"]: c["priority"] for c in CATEGORIES}
HINT_BONUS = 12

_STORE_ALIAS_MATCHERS: list[tuple[str, re.Pattern]] = []
for _slug, _store in STORES.items():
    for _alias in sorted(_store["aliases"], key=len, reverse=True):
        _STORE_ALIAS_MATCHERS.append((_slug, re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(_alias), re.I)))


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def clean_title(title: str | None) -> str:
    """Remove feed noise: leading [Store] tags, trailing ' | Slickdeals', etc."""
    t = strip_html(title)
    t = re.sub(r"\s*[\|\-–]\s*(slickdeals|reddit|dealnews)\s*$", "", t, flags=re.I)
    t = _WS_RE.sub(" ", t).strip()
    return t[:220]


def to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return round(float(s.replace(",", "")), 2)
    except ValueError:
        return None


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        h = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return h.lower().removeprefix("www.").removeprefix("m.").removeprefix("smile.")


def is_aggregator(url: str | None) -> bool:
    h = host_of(url)
    return any(h == a or h.endswith("." + a) for a in AGGREGATOR_HOSTS)


def store_from_url(url: str | None) -> str | None:
    h = host_of(url)
    if not h:
        return None
    for slug, store in STORES.items():
        for sh in store["hosts"]:
            if h == sh or h.endswith("." + sh):
                return slug
    return None


def store_from_text(*texts: str | None) -> str | None:
    """Find a store mention such as '[Amazon]', 'at Best Buy' or 'Target.com'."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    m = _BRACKET_PREFIX_RE.match(blob)
    if m:
        for slug, rx in _STORE_ALIAS_MATCHERS:
            if rx.search(m.group(1)):
                return slug
    best: tuple[int, str] | None = None
    for slug, rx in _STORE_ALIAS_MATCHERS:
        hit = rx.search(blob)
        if hit and (best is None or hit.start() < best[0]):
            best = (hit.start(), slug)
    return best[1] if best else None


def detect_store(url: str | None, title: str | None = None, text: str | None = None, hint: str | None = None) -> str:
    """Store slug for a deal. URL host wins; aggregator links fall back to text."""
    if hint in STORES and hint != "other":
        return hint
    from_url = store_from_url(url)
    if from_url:
        return from_url
    if url and not is_aggregator(url):
        # Direct retailer link we do not know: still try the text, else "other".
        return store_from_text(title, text) or "other"
    return store_from_text(title, text) or "other"


def canonical_url(url: str | None) -> str:
    """Strip tracking params; collapse Amazon product URLs to /dp/ASIN."""
    if not url:
        return ""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if host.endswith("amazon.com"):
        m = _ASIN_RE.search(path)
        if m:
            return f"https://www.amazon.com/dp/{m.group(1)}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    query.sort()
    scheme = "https" if parts.scheme in ("http", "https") else "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        pass
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def first_url_in(text: str | None) -> str | None:
    if not text:
        return None
    m = _URL_IN_TEXT_RE.search(html.unescape(text))
    return m.group(0).rstrip(".,;") if m else None


def parse_prices(title: str | None, text: str | None = None) -> tuple[float | None, float | None, int | None]:
    """Return (price, list_price, discount_pct). Any may be None."""
    title = title or ""
    text = text or ""
    blob = f"{title} {text}"
    price: float | None = None
    list_price: float | None = None
    pct: int | None = None

    m = _ARROW_RE.search(blob)
    if m:
        a, b = to_float(m.group(1)), to_float(m.group(2))
        if a and b:
            list_price, price = (a, b) if a > b else (b, a)

    if list_price is None:
        m = _LIST_MARKER_RE.search(blob)
        if m:
            list_price = to_float(m.group(1))

    if price is None:
        title_prices = [to_float(x) for x in _MONEY_RE.findall(title)]
        title_prices = [p for p in title_prices if p and p > 0]
        if list_price is not None:
            title_prices = [p for p in title_prices if abs(p - list_price) > 0.001]
        if title_prices:
            price = title_prices[0]
        else:
            text_prices = [to_float(x) for x in _MONEY_RE.findall(text)]
            text_prices = [p for p in text_prices if p and p > 0 and (list_price is None or abs(p - list_price) > 0.001)]
            if text_prices:
                price = min(text_prices)

    # "($599 - $100)" style: price is given, parenthetical shows base and coupon.
    if price is not None and list_price is None:
        pm = re.search(r"\(\s*\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*-\s*\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*\)", blob)
        if pm:
            base = to_float(pm.group(1))
            if base and base > price:
                list_price = base

    pm = _PCT_RE.search(blob)
    if pm:
        try:
            pct = int(pm.group(1))
        except ValueError:
            pct = None
        if pct is not None and not (1 <= pct <= 95):
            pct = None

    if price is not None and (price <= 0 or price > 250000):
        price = None
    if list_price is not None and (price is None or list_price <= price):
        if price is not None and list_price is not None and list_price <= price:
            list_price = None
    if price is not None and list_price is not None:
        pct = int(round((1 - price / list_price) * 100))
        if pct < 1:
            pct = None
            list_price = None
    elif price is not None and pct is not None and list_price is None:
        list_price = round(price / (1 - pct / 100), 2)
    return price, list_price, pct


def parse_coupon(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    for m in _COUPON_RE.finditer(blob):
        code = m.group(1)
        # Reject plain words ("code required", "CODE SALE") that are not codes.
        if code.isalpha() and code.lower() in {"required", "sale", "needed", "free", "deal", "promo", "offer", "coupon", "stack", "stacks", "only", "online", "store", "below", "above", "checkout", "applied", "auto"}:
            continue
        if not re.search(r"[A-Z]", code) and not re.search(r"\d", code):
            continue
        if re.search(r"\d", code) or code.isupper() or len(code) >= 6:
            return code.upper()
    return None


def classify(title: str | None, text: str | None = None, hint: str | None = None) -> str:
    """Category slug.

    Every category is scored by the distinct keywords it matches: longer (more
    specific) keywords weigh more, and matches in the title count double. A
    source hint (e.g. r/buildapcsales -> computers) adds a flat bonus so it
    wins against weak evidence but loses to a clearly different title.
    Ties fall back to the category priority."""
    title_only = title or ""
    body = text or ""
    scores: dict[str, int] = {}
    for _prio, slug, rx in _CATEGORY_MATCHERS:
        title_hits = {m.group(0).lower() for m in rx.finditer(title_only)}
        text_hits = {m.group(0).lower() for m in rx.finditer(body)} - title_hits
        score = 2 * sum(len(k) for k in title_hits) + sum(len(k) for k in text_hits)
        if score:
            scores[slug] = score
    if hint and hint in CATEGORY_PRIORITY and hint != "other":
        scores[hint] = scores.get(hint, 0) + HINT_BONUS
    if not scores:
        return "other"
    return max(scores, key=lambda s: (scores[s], -CATEGORY_PRIORITY[s]))


def extract_tags(title: str | None, text: str | None = None, coupon: str | None = None) -> list[str]:
    blob = f"{title or ''} {text or ''}".lower()
    tags: list[str] = []
    if "black friday" in blob or "blackfriday" in blob:
        tags.append("black-friday")
    if "cyber monday" in blob or "cybermonday" in blob:
        tags.append("cyber-monday")
    if "lightning deal" in blob:
        tags.append("lightning")
    if "deal of the day" in blob or "today only" in blob or "daily deal" in blob:
        tags.append("today-only")
    if "clearance" in blob:
        tags.append("clearance")
    if "free shipping" in blob or "free ship" in blob or "ships free" in blob:
        tags.append("free-shipping")
    if "refurb" in blob or "renewed" in blob or "open box" in blob or "open-box" in blob:
        tags.append("refurbished")
    if "bundle" in blob:
        tags.append("bundle")
    if "gift card" in blob and "w/" in blob or "with gift card" in blob or "+ gift card" in blob or "+ $" in blob and "gift card" in blob:
        tags.append("gift-card-bonus")
    if "clip coupon" in blob or ("clip" in blob and "coupon" in blob):
        tags.append("clip-coupon")
    if "prime" in blob and ("prime member" in blob or "prime exclusive" in blob or "prime only" in blob):
        tags.append("prime-only")
    if "member" in blob and ("members only" in blob or "member price" in blob or "plus member" in blob or "my best buy" in blob):
        tags.append("members-only")
    if "pre-order" in blob or "preorder" in blob:
        tags.append("pre-order")
    if coupon:
        tags.append("coupon")
    # de-dup, keep order
    seen = set()
    return [t for t in tags if not (t in seen or seen.add(t))]
