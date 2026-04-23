"""豆瓣图书抓取：标签页 + 搜索补充。"""

from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TAG_PAGE_URL = "https://book.douban.com/tag/{tag}?start={start}&type={sort}"
SEARCH_URL = "https://book.douban.com/subject_search?search_text={keyword}&cat=1001&start={start}"
PAGE_SIZE = 20

MAX_RETRIES = 3
RETRY_BACKOFF = 2


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)

    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _parse_pub_info(text: str) -> tuple[list[str], str, list[str], str]:
    if not text:
        return [], "", [], ""

    parts = [part.strip() for part in text.split("/")]
    year = ""
    price = ""
    year_idx = -1
    price_idx = -1

    for index, part in enumerate(parts):
        if not part:
            continue
        if re.search(r"[\d.]+\s*(元|CNY|\$|EUR|GBP)", part):
            price = part
            price_idx = index
        elif re.match(r"^\d{4}", part):
            year = part
            year_idx = index

    if year_idx >= 0 and price_idx < 0:
        for index in range(year_idx + 1, len(parts)):
            part = parts[index].strip()
            if part and re.match(r"^[\d.]+$", part):
                price = part
                price_idx = index
                break

    text_parts: list[str] = []
    for index, part in enumerate(parts):
        if index == year_idx or index == price_idx or not part.strip():
            continue
        text_parts.append(part.strip())

    authors: list[str] = []
    press: list[str] = []
    if len(text_parts) >= 3:
        authors = [item.strip() for item in re.split(r"[,、，]", text_parts[0]) if item.strip()]
        press = [text_parts[-1]]
    elif len(text_parts) == 2:
        authors = [item.strip() for item in re.split(r"[,、，]", text_parts[0]) if item.strip()]
        press = [text_parts[1]]
    elif len(text_parts) == 1:
        authors = [item.strip() for item in re.split(r"[,、，]", text_parts[0]) if item.strip()]

    return authors, year, press, price


def _normalize_cover_url(url: str) -> str:
    cover_url = (url or "").strip()
    if not cover_url:
        return ""
    return (
        cover_url.replace("/view/subject/s/", "/view/subject/l/")
        .replace("/view/subject/m/", "/view/subject/l/")
    )


def _parse_item(item) -> dict | None:
    try:
        title_el = item.select_one(".info h2 a")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        link = title_el.get("href", "")
        if not title or not link:
            return None

        match = re.search(r"/subject/(\d+)", link)
        if not match:
            return None
        book_id = match.group(1)

        pub_el = item.select_one(".info .pub")
        pub_text = pub_el.get_text(strip=True) if pub_el else ""
        authors, year, press, _ = _parse_pub_info(pub_text)

        rating_el = item.select_one(".info .rating_nums")
        rating_text = rating_el.get_text(strip=True) if rating_el else ""
        rating = float(rating_text) if rating_text else 0

        rating_count = 0
        pl_el = item.select_one(".info .pl")
        if pl_el:
            pl_text = pl_el.get_text(strip=True)
            match = re.search(r"(\d+)", pl_text)
            if match:
                rating_count = int(match.group(1))

        img_el = item.select_one(".pic img")
        cover_url = _normalize_cover_url(img_el.get("src", "") if img_el else "")
        desc_el = item.select_one(".info p")
        abstract = desc_el.get_text(strip=True) if desc_el else ""

        return {
            "source": "douban",
            "id": book_id,
            "title": title,
            "subtitle": "",
            "author": authors,
            "press": press,
            "year": year,
            "rating": rating,
            "rating_count": rating_count,
            "cover_url": cover_url,
            "url": link,
            "abstract": abstract,
        }
    except Exception:
        logger.debug("failed to parse douban tag item", exc_info=True)
        return None


def _extract_search_payload(html: str) -> dict:
    match = re.search(r"window\.__DATA__\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.debug("failed to parse douban search payload", exc_info=True)
        return {}


def _parse_search_item(item: dict) -> dict | None:
    if item.get("tpl_name") != "search_subject":
        return None

    book_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    if not book_id or not title or not url:
        return None

    authors, year, press, _ = _parse_pub_info(str(item.get("abstract") or ""))
    rating_info = item.get("rating") or {}

    return {
        "source": "douban_search",
        "id": book_id,
        "title": title,
        "subtitle": "",
        "author": authors,
        "press": press,
        "year": year,
        "rating": float(rating_info.get("value") or 0),
        "rating_count": int(rating_info.get("count") or 0),
        "cover_url": _normalize_cover_url(str(item.get("cover_url") or "")),
        "url": url,
        "abstract": str(item.get("abstract_2") or item.get("abstract") or "").strip(),
    }


def _fetch_tag_books(cfg: dict, session: requests.Session, seen_ids: set[str]) -> list[dict]:
    tags = cfg.get("tags", ["推理"])
    max_pages = int(cfg.get("max_pages", 3))
    delay = cfg.get("delay", 2)
    sort = cfg.get("sort", "R")
    books: list[dict] = []

    for tag in tags:
        logger.info("豆瓣: 抓取标签「%s」(sort=%s)...", tag, sort)
        for page in range(max_pages):
            start = page * PAGE_SIZE
            url = TAG_PAGE_URL.format(tag=quote(tag), start=start, sort=sort)
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning("豆瓣请求失败 [%s page=%d]: %s", tag, page, exc)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select("#subject_list .subject-item")
            if not items:
                break

            for item_el in items:
                book = _parse_item(item_el)
                if book and book["id"] not in seen_ids:
                    seen_ids.add(book["id"])
                    books.append(book)

            if len(items) < PAGE_SIZE:
                break
            time.sleep(delay)

        time.sleep(delay)

    return books


def _fetch_search_books(cfg: dict, session: requests.Session, seen_ids: set[str]) -> list[dict]:
    keywords = [str(item).strip() for item in cfg.get("search_keywords", []) if str(item).strip()]
    if not keywords:
        return []

    max_pages = max(int(cfg.get("search_max_pages", 1)), 1)
    delay = cfg.get("delay", 2)
    books: list[dict] = []

    for keyword in keywords:
        logger.info("豆瓣: 搜索补充「%s」...", keyword)
        for page in range(max_pages):
            start = page * PAGE_SIZE
            url = SEARCH_URL.format(keyword=quote(keyword), start=start)
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning("豆瓣搜索失败 [%s page=%d]: %s", keyword, page, exc)
                continue

            payload = _extract_search_payload(resp.text)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            if not items:
                break

            page_added = 0
            for raw in items:
                book = _parse_search_item(raw)
                if book and book["id"] not in seen_ids:
                    seen_ids.add(book["id"])
                    books.append(book)
                    page_added += 1

            if len(items) < PAGE_SIZE or page_added == 0:
                break
            time.sleep(delay)

        time.sleep(delay)

    logger.info("豆瓣: 搜索补充共获取 %d 本书籍", len(books))
    return books


def fetch_douban_books(cfg: dict) -> list[dict]:
    """抓取豆瓣标签页，并用搜索结果补齐漏书。"""
    seen_ids: set[str] = set()
    session = _create_session()

    books = _fetch_tag_books(cfg, session, seen_ids)
    books.extend(_fetch_search_books(cfg, session, seen_ids))

    logger.info("豆瓣: 共获取 %d 本书籍（去重后）", len(books))
    return books
