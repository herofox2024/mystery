"""中文推理资讯抓取器：豆瓣豆列 + 中国作家网页面列表。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

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


def _extract_subject_id(url: str) -> str:
    match = re.search(r"/subject/(\d+)", url or "")
    return match.group(1) if match else ""


def _request_html(url: str, timeout: int) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_datetime_string(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return ""


def _parse_chinawriter_date(url: str) -> str:
    match = re.search(r"/n1/(\d{4})/(\d{2})(\d{2})/", url)
    if not match:
        return ""
    year, month, day = match.groups()
    try:
        dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return ""
    return dt.isoformat()


def _extract_book_year(text: str) -> int | None:
    match = re.search(r"出版年[:：]?\s*(20\d{2}|19\d{2})", text)
    if not match:
        return None
    return int(match.group(1))


def _extract_field_list(text: str, label: str) -> list[str]:
    match = re.search(rf"{label}[:：]?\s*([^；]+?)(?=\s*(?:作者|出版社|出版年)[:：]|$)", text)
    if not match:
        return []
    value = _normalize_text(match.group(1))
    return [value] if value else []


def _is_recent(published: str, cutoff: datetime) -> bool:
    if not published:
        return True
    try:
        dt = datetime.fromisoformat(published)
    except ValueError:
        return True
    return dt >= cutoff


def _fetch_douban_doulist_books(cfg: dict, cutoff: datetime, timeout: int, target_year: int | None) -> list[dict]:
    name = str(cfg.get("name") or "豆瓣豆列").strip()
    base_url = str(cfg.get("url") or "").strip()
    max_pages = max(int(cfg.get("max_pages", 1)), 1)
    if not base_url:
        return []

    logger.info("China sources: fetching doulist %s", name)
    books: list[dict] = []

    for page in range(max_pages):
        page_url = base_url if page == 0 else f"{base_url}?start={page * 25}"
        try:
            html = _request_html(page_url, timeout)
        except requests.RequestException as exc:
            logger.warning("China sources doulist failed [%s]: %s", name, exc)
            break

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".doulist-item")
        if not items:
            break

        page_kept = 0
        for item in items:
            link = item.select_one(".title a[href]")
            if not link:
                continue

            title = _normalize_text(link.get_text(" ", strip=True))
            href = urljoin(page_url, link.get("href", "").strip())
            if not title or not href:
                continue
            img = item.select_one(".post img")
            cover_url = ""
            if img:
                cover_url = (img.get("src") or "").strip()
                cover_url = cover_url.replace("/view/subject/s/", "/view/subject/l/")

            abstract = _normalize_text(item.select_one(".abstract").get_text(" ", strip=True) if item.select_one(".abstract") else "")
            comment = _normalize_text(item.select_one(".comment").get_text(" ", strip=True) if item.select_one(".comment") else "")
            time_text = _normalize_text(item.select_one("time.time").get_text(" ", strip=True) if item.select_one("time.time") else "")
            published = _parse_datetime_string(time_text)
            if not _is_recent(published, cutoff):
                continue

            if target_year is not None:
                book_year = _extract_book_year(abstract)
                if book_year != target_year:
                    continue

            summary_parts = [part for part in (abstract, comment) if part]
            author = _extract_field_list(abstract, "作者")
            press = _extract_field_list(abstract, "出版社")
            books.append(
                {
                    "source": f"china_book:{name}",
                    "id": _extract_subject_id(href) or href,
                    "title": title,
                    "subtitle": "",
                    "author": author,
                    "press": press,
                    "year": str(book_year) if book_year is not None else "",
                    "rating": 0,
                    "rating_count": 0,
                    "cover_url": cover_url,
                    "url": href,
                    "published": published,
                    "abstract": "；".join(summary_parts),
                }
            )
            page_kept += 1

        if page_kept == 0:
            break

    return books


def _fetch_chinawriter_page(cfg: dict, cutoff: datetime, timeout: int) -> list[dict]:
    name = str(cfg.get("name") or "中国作家网").strip()
    page_url = str(cfg.get("url") or "").strip()
    max_items = max(int(cfg.get("max_items", 20)), 1)
    if not page_url:
        return []

    logger.info("China sources: fetching page %s", name)
    try:
        html = _request_html(page_url, timeout)
    except requests.RequestException as exc:
        logger.warning("China sources page failed [%s]: %s", name, exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    seen_links: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, (anchor.get("href") or "").strip())
        title = _normalize_text(anchor.get_text(" ", strip=True))
        if not href or not title or "/n1/" not in href:
            continue
        if href in seen_links:
            continue
        seen_links.add(href)

        published = _parse_chinawriter_date(href)
        if not _is_recent(published, cutoff):
            continue

        entries.append(
            {
                "source": f"china:{name}",
                "id": href,
                "title": title,
                "url": href,
                "published": published,
                "summary": "",
            }
        )
        if len(entries) >= max_items:
            break

    return entries


def fetch_china_book_entries(cfg: dict) -> list[dict]:
    """抓取中文图书源，输出统一 book 结构。"""
    if not cfg or not cfg.get("enabled", True):
        return []

    days = max(int(cfg.get("days", 14)), 1)
    timeout = max(int(cfg.get("timeout", 20)), 5)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    target_year = cfg.get("target_year")
    target_year = int(target_year) if str(target_year or "").strip() else None

    all_books: list[dict] = []
    seen_ids: set[str] = set()

    for item in cfg.get("douban_doulists", []):
        for book in _fetch_douban_doulist_books(item or {}, cutoff, timeout, target_year):
            if book["id"] in seen_ids:
                continue
            seen_ids.add(book["id"])
            all_books.append(book)

    all_books.sort(key=lambda item: item.get("published", ""), reverse=True)
    logger.info("China sources: fetched %d book items after dedupe", len(all_books))
    return all_books


def fetch_china_entries(cfg: dict) -> list[dict]:
    """抓取中文资讯源，输出统一 entry 结构。"""
    if not cfg or not cfg.get("enabled", True):
        return []

    days = max(int(cfg.get("days", 14)), 1)
    timeout = max(int(cfg.get("timeout", 20)), 5)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    all_entries: list[dict] = []
    seen_ids: set[str] = set()

    for item in cfg.get("chinawriter_pages", []):
        for entry in _fetch_chinawriter_page(item or {}, cutoff, timeout):
            if entry["id"] in seen_ids:
                continue
            seen_ids.add(entry["id"])
            all_entries.append(entry)

    all_entries.sort(key=lambda item: item.get("published", ""), reverse=True)
    logger.info("China sources: fetched %d entries after dedupe", len(all_entries))
    return all_entries
