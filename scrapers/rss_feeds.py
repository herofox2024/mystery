"""RSS aggregation with feed parsing and lightweight HTML fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def _parse_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _parse_entry(entry, feed_name: str) -> dict | None:
    """Normalize a feedparser entry."""
    title = entry.get("title", "").strip()
    if not title:
        return None

    link = entry.get("link", "")
    published = ""
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                published = datetime(*tp[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                published = ""
            break

    if not published:
        published = _parse_datetime(entry.get("published", "") or entry.get("updated", ""))

    summary = entry.get("summary", "") or entry.get("description", "")

    return {
        "source": f"rss:{feed_name}",
        "id": link or title,
        "title": title,
        "url": link,
        "published": published,
        "summary": summary,
    }


def _parse_html_links(url: str, feed_name: str) -> list[dict]:
    """Fallback for pages that are no longer valid RSS feeds."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("RSS HTML fallback failed [%s]: %s", feed_name, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    entries: list[dict] = []
    seen_links: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = (anchor.get("href") or "").strip()
        title = anchor.get_text(" ", strip=True)
        if not href or not title or len(title) < 8:
            continue
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        if href in seen_links:
            continue
        seen_links.add(href)
        entries.append(
            {
                "source": f"html:{feed_name}",
                "id": href,
                "title": title,
                "url": href,
                "published": "",
                "summary": "",
            }
        )
        if len(entries) >= 50:
            break

    return entries


def fetch_rss_entries(cfg: dict) -> list[dict]:
    """Fetch configured RSS feeds and keep recent entries."""
    feeds = cfg.get("feeds", [])
    days = cfg.get("days", 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    entries: list[dict] = []
    seen: set[str] = set()

    for feed_cfg in feeds:
        name = feed_cfg.get("name", feed_cfg["url"])
        url = feed_cfg["url"]
        logger.info("RSS: fetching %s", name)

        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("RSS parse failed [%s]: %s", name, exc)
            parsed = None

        raw_entries = list(parsed.entries) if parsed and parsed.entries else []
        if parsed and parsed.bozo and not raw_entries:
            logger.warning("RSS source invalid [%s]: %s", name, parsed.bozo_exception)

        if not raw_entries and feed_cfg.get("allow_html_fallback", False):
            logger.info("RSS: using HTML fallback for %s", name)
            fallback_entries = _parse_html_links(url, name)
            for entry in fallback_entries:
                if entry["id"] not in seen:
                    seen.add(entry["id"])
                    entries.append(entry)
            continue

        feed_entries_count = 0
        for raw in raw_entries:
            entry = _parse_entry(raw, name)
            if not entry:
                continue

            if entry["published"]:
                try:
                    pub_dt = datetime.fromisoformat(entry["published"])
                    if pub_dt < cutoff:
                        continue
                except ValueError:
                    pass

            if entry["id"] not in seen:
                seen.add(entry["id"])
                entries.append(entry)
                feed_entries_count += 1

        logger.debug("RSS: %s kept %d entries from %d", name, feed_entries_count, len(raw_entries))

    entries.sort(key=lambda e: e.get("published", ""), reverse=True)
    logger.info("RSS: fetched %d entries after dedupe", len(entries))
    return entries
