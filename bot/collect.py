"""Zber správ z RSS feedov a stiahnutie plného textu článkov."""
from __future__ import annotations

import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura

from .common import log, now_utc

UA = "Mozilla/5.0 (compatible; GamingNewsBot/1.0; +https://github.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/html;q=0.9, */*;q=0.8"}


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _entry_image(entry) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key) or []:
            if m.get("url"):
                return m["url"]
    for enc in entry.get("enclosures") or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]
    return None


def fetch_feed(feed: dict) -> list[dict]:
    try:
        r = requests.get(feed["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001
        log.warning("Feed %s zlyhal: %s", feed["name"], e)
        return []
    items = []
    for e in parsed.entries[:40]:
        link = e.get("link")
        title = _clean(e.get("title", ""), 200)
        if not link or not title:
            continue
        items.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:8],
            "source": feed["name"],
            "tier": feed.get("tier", "trusted"),
            "group": feed.get("group") or feed["name"],
            "title": title,
            "summary": _clean(e.get("summary", ""), 400),
            # plný text z feedu (content alebo dlhé summary) – záloha, keď web sťahovanie článku zablokuje
            "rss_text": _clean(max(((e.get("content") or [{}])[0].get("value", ""), e.get("summary", "")), key=len), 7000),
            "link": link,
            "published": (_published(e) or now_utc()).isoformat(),
            "image": _entry_image(e),
        })
    return items


def collect(feeds: list[dict], lookback_hours: int, used_links: set[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_feed, feeds))
    cutoff = now_utc() - timedelta(hours=lookback_hours)
    items, seen = [], set()
    for batch in results:
        for it in batch:
            if it["link"] in used_links or it["link"] in seen:
                continue
            if datetime.fromisoformat(it["published"]) < cutoff:
                continue
            seen.add(it["link"])
            items.append(it)
    items.sort(key=lambda x: x["published"], reverse=True)
    log.info("Zozbieraných %d čerstvých článkov z %d feedov", len(items), len(feeds))
    return items


def fetch_article(item: dict) -> dict:
    """Doplní plný text článku a og:image."""
    text, og_image = "", None
    try:
        r = requests.get(item["link"], headers=HEADERS, timeout=25)
        r.raise_for_status()
        page = r.text
        text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
        if m:
            og_image = html.unescape(m.group(1))
    except Exception as e:  # noqa: BLE001
        log.warning("Článok %s sa nepodarilo stiahnuť: %s", item["link"], e)
    rss_text = item.get("rss_text") or item.get("summary", "")
    if len(text) < len(rss_text):  # stránka zablokovaná alebo z nej trafilatura vytiahla menej ako feed
        text = rss_text
    return {**item, "text": text[:7000], "image": og_image or item.get("image")}
