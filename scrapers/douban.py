"""豆瓣标签页抓取模块 — 按标签获取推理类新书信息。

使用豆瓣读书标签页 (book.douban.com/tag/推理) 抓取：
  - type=R 按出版日期排序（最新优先）
  - type=T 综合排序
  - start/20 分页
  - BeautifulSoup 解析 HTML
"""

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
PAGE_SIZE = 20  # 豆瓣标签页每页固定 20 条

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # 重试间隔基数（秒）


def _create_session() -> requests.Session:
    """创建带有重试机制的 Session。"""
    session = requests.Session()
    session.headers.update(HEADERS)

    # 配置重试策略
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
    """
    解析出版信息字符串，格式通常为：
      "作者 / 译者 / 出版社 / 日期 / 价格"
    或  "作者 / 出版社 / 日期 / 价格"

    Returns (authors, year, press, price)
    """
    if not text:
        return [], "", [], ""

    parts = [p.strip() for p in text.split("/")]

    # 先识别日期和价格的位置
    year = ""
    price = ""
    year_idx = -1
    price_idx = -1

    for i, part in enumerate(parts):
        if not part:
            continue
        # 价格：含货币符号，或者在日期之后的纯数字
        if re.search(r"[\d.]+\s*(元|円|\$|￥|EUR|GBP)", part):
            price = part
            price_idx = i
        elif re.match(r"^\d{4}", part):
            year = part
            year_idx = i

    # 日期之后的纯数字也视为价格（如 "45.00" "65"）
    if year_idx >= 0 and price_idx < 0:
        for i in range(year_idx + 1, len(parts)):
            p = parts[i].strip()
            if p and re.match(r"^[\d.]+$", p):
                price = p
                price_idx = i
                break

    # 日期之前的部分依次为：作者 / [译者...] / 出版社
    # 出版社是日期前最近的那一项
    text_parts = []
    for i, part in enumerate(parts):
        if i == year_idx or i == price_idx or not part.strip():
            continue
        text_parts.append(part.strip())

    authors: list[str] = []
    press: list[str] = []

    if len(text_parts) >= 3:
        # 作者 / 译者... / 出版社
        authors = [a.strip() for a in re.split(r"[,、，]", text_parts[0]) if a.strip()]
        press = [text_parts[-1]]
    elif len(text_parts) == 2:
        authors = [a.strip() for a in re.split(r"[,、，]", text_parts[0]) if a.strip()]
        press = [text_parts[1]]
    elif len(text_parts) == 1:
        authors = [a.strip() for a in re.split(r"[,、，]", text_parts[0]) if a.strip()]

    return authors, year, press, price


def _parse_item(item) -> dict | None:
    """从 BeautifulSoup 的 .subject-item 元素解析书籍信息。"""
    try:
        # 标题 + 链接
        title_el = item.select_one(".info h2 a")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        link = title_el.get("href", "")

        # 验证必要字段不为空
        if not title or not link:
            return None

        # 从链接提取 ID
        book_id = ""
        m = re.search(r"/subject/(\d+)", link)
        if m:
            book_id = m.group(1)

        # 出版信息
        pub_el = item.select_one(".info .pub")
        pub_text = pub_el.get_text(strip=True) if pub_el else ""
        authors, year, press, _ = _parse_pub_info(pub_text)

        # 评分
        rating_el = item.select_one(".info .rating_nums")
        rating_text = rating_el.get_text(strip=True) if rating_el else ""
        rating = float(rating_text) if rating_text else 0

        # 评价人数
        rating_count = 0
        pl_el = item.select_one(".info .pl")
        if pl_el:
            pl_text = pl_el.get_text(strip=True)
            m = re.search(r"(\d+)", pl_text)
            if m:
                rating_count = int(m.group(1))

        # 封面
        img_el = item.select_one(".pic img")
        cover_url = img_el.get("src", "") if img_el else ""
        # 替换小图为大图
        cover_url = cover_url.replace("/view/subject/s/", "/view/subject/l/")

        # 简介
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
        logger.debug("解析书籍条目异常", exc_info=True)
        return None


def fetch_douban_books(cfg: dict) -> list[dict]:
    """
    根据配置中的标签列表，逐标签、分页抓取豆瓣标签页。

    Parameters
    ----------
    cfg : dict
        config.yaml 中 ``douban`` 段的内容。

    Returns
    -------
    list[dict]
        去重后的书籍列表（按豆瓣 ID 去重）。
    """
    tags = cfg.get("tags", ["推理"])
    max_pages = cfg.get("max_pages", 3)
    delay = cfg.get("delay", 2)
    sort = cfg.get("sort", "R")  # R=出版时间, T=综合, S=评分

    seen_ids: set[str] = set()
    books: list[dict] = []

    session = _create_session()

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
                logger.debug("豆瓣: 标签「%s」第 %d 页无数据，停止翻页", tag, page)
                break

            page_count = 0
            for item_el in items:
                book = _parse_item(item_el)
                if book and book["id"] and book["id"] not in seen_ids:
                    seen_ids.add(book["id"])
                    books.append(book)
                    page_count += 1

            logger.debug(
                "豆瓣: 标签「%s」第 %d 页新增 %d 条（共 %d 条）",
                tag, page, page_count, len(items),
            )

            if len(items) < PAGE_SIZE:
                break
            time.sleep(delay)

        time.sleep(delay)

    logger.info("豆瓣: 共获取 %d 本书籍（去重后）", len(books))
    return books
