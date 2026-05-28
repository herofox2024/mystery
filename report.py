"""报告生成模块，将抓取结果输出为 Markdown 和 HTML 周报。"""

from __future__ import annotations

import logging
import os
import json
import re
from hashlib import md5
from datetime import datetime
from html import unescape
from urllib.parse import urlparse
from typing import Any

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_md_env = Environment(autoescape=False, trim_blocks=False, lstrip_blocks=False)
_html_env = Environment(
    autoescape=select_autoescape(default_for_string=True, enabled_extensions=("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_md_env.loader = FileSystemLoader(TEMPLATE_DIR, encoding="utf-8")
_html_env.loader = FileSystemLoader(TEMPLATE_DIR, encoding="utf-8")

MD_TEMPLATE = _md_env.get_template("report.md.j2")
HTML_TEMPLATE = _html_env.get_template("report.html.j2")


def _join_list(value: Any, fallback: str = "—") -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items) if items else fallback
    text = str(value or "").strip()
    return text or fallback


def _format_rating(item: dict[str, Any]) -> str:
    rating = float(item.get("rating") or 0)
    rating_count = int(item.get("rating_count") or 0)
    if rating <= 0:
        return "暂无"
    if rating_count > 0:
        return f"{rating:.1f}（{rating_count}人评价）"
    return f"{rating:.1f}"


def _prepare_book(book: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(book)
    normalized["author_text"] = _join_list(book.get("author"))
    normalized["press_text"] = _join_list(book.get("press"))
    normalized["year_text"] = str(book.get("year") or "未知年份")
    normalized["rating_text"] = _format_rating(book)
    normalized["score_text"] = f"{float(book.get('score') or 0):.2f}" if "score" in book else "—"
    normalized["url"] = str(book.get("url") or "#")
    normalized["title"] = str(book.get("title") or "未命名")
    return normalized


def _prepare_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    normalized["source_text"] = str(entry.get("source") or "未知来源")
    normalized["published_text"] = str(entry.get("published") or "")[:10]
    normalized["url"] = str(entry.get("url") or "#")
    normalized["title"] = str(entry.get("title") or "未命名")
    return normalized


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_EN_LETTER_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _pick_highlight(summary_text: str) -> str:
    text = _clean_text(summary_text)
    if not text:
        return ""
    parts = [chunk.strip() for chunk in re.split(r"[。！？.!?\n]+", text) if chunk.strip()]
    if parts:
        parts.sort(key=lambda s: len(s), reverse=True)
        best = parts[0]
        if len(best) <= 120:
            return best
    return text[:120]


def _looks_english(text: str) -> bool:
    if not text:
        return False
    if _CJK_RE.search(text):
        return False
    letters = len(_EN_LETTER_RE.findall(text))
    return letters >= max(12, int(len(text) * 0.35))


def _translate_excerpt_to_cn(text: str, ai_cfg: dict[str, Any]) -> str:
    if not text or not _looks_english(text):
        return text
    if not ai_cfg.get("enabled"):
        return text
    try:
        # Reuse the project's provider fallback chain.
        from scrapers.ai_filter import _chat_with_fallback  # type: ignore

        prompt = (
            "把下面英文句子翻译成自然简洁的中文，保留原意，不要补充信息。"
            "返回 JSON：{\"translation\":\"...\"}。\n"
            f"文本：{json.dumps(text, ensure_ascii=False)}"
        )
        content, _ = _chat_with_fallback(prompt, ai_cfg)
        if not content:
            return text
        data = json.loads(content)
        translated = str(data.get("translation", "")).strip() if isinstance(data, dict) else ""
        return translated or text
    except Exception:
        return text


def _attach_full_rss_excerpts(entries: list[dict[str, Any]], ai_cfg: dict[str, Any]) -> None:
    for entry in entries:
        source_text = str(entry.get("summary") or "")
        excerpt = _pick_highlight(source_text)
        if not excerpt:
            excerpt = str(entry.get("ai_summary") or "").strip()
        excerpt = _translate_excerpt_to_cn(excerpt, ai_cfg)
        if excerpt:
            entry["full_excerpt"] = excerpt


def _is_placeholder_cover(url: str) -> bool:
    text = (url or "").strip().lower()
    return not text or "book-default" in text or text.endswith(".gif")


def _cover_extension(url: str, content_type: str) -> str:
    if "image/png" in content_type:
        return ".png"
    if "image/webp" in content_type:
        return ".webp"
    if "image/gif" in content_type:
        return ".gif"
    path = urlparse(url).path.lower()
    if path.endswith(".png"):
        return ".png"
    if path.endswith(".webp"):
        return ".webp"
    if path.endswith(".gif"):
        return ".gif"
    return ".jpg"


def _cache_cover_image(cover_url: str, output_dir: str, book_id: str) -> str:
    if _is_placeholder_cover(cover_url):
        return ""

    assets_dir = os.path.join(output_dir, "assets", "covers")
    os.makedirs(assets_dir, exist_ok=True)

    raw_id = book_id.strip()
    safe_id = "".join(ch for ch in raw_id if ch.isalnum() or ch in ("-", "_"))[:48]
    if not safe_id:
        safe_id = md5(cover_url.encode("utf-8")).hexdigest()[:12]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://book.douban.com/",
    }

    try:
        response = requests.get(cover_url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("封面下载失败 [%s]: %s", cover_url, exc)
        return ""

    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/"):
        logger.warning("封面响应不是图片 [%s]: %s", cover_url, content_type or "unknown")
        return ""

    ext = _cover_extension(cover_url, content_type)
    file_name = f"{safe_id}{ext}"
    file_path = os.path.join(assets_dir, file_name)
    with open(file_path, "wb") as fh:
        fh.write(response.content)
    return f"assets/covers/{file_name}"


def generate_report(
    books: list[dict],
    rss_entries: list[dict],
    cfg: dict,
    project_root: str,
    stats: dict | None = None,
) -> tuple[str, str]:
    """生成 Markdown 和 HTML 周报文件。"""
    output_dir = os.path.join(project_root, cfg.get("output_dir", "output"))
    os.makedirs(output_dir, exist_ok=True)

    title_prefix = cfg.get("title_prefix", "推理资讯周报")
    date_str = datetime.now().strftime("%Y-%m-%d")
    title = f"{title_prefix}_{date_str}"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prepared_books = [_prepare_book(book) for book in books]
    embed_cover_images = bool(cfg.get("embed_cover_images", False))
    for book in prepared_books:
        local_cover = _cache_cover_image(str(book.get("cover_url") or ""), output_dir, str(book.get("id") or ""))
        if local_cover:
            book["cover_url"] = local_cover
            if embed_cover_images:
                logger.warning("embed_cover_images=true 会显著增大 HTML 体积，当前已忽略该配置")
        elif _is_placeholder_cover(str(book.get("cover_url") or "")):
            book["cover_url"] = ""
    prepared_rss = [_prepare_entry(entry) for entry in rss_entries]
    top_rss = int(cfg.get("top_rss", 10))
    full_rss = prepared_rss[: cfg.get("full_rss", 20)]
    _attach_full_rss_excerpts(full_rss, dict(cfg.get("excerpt_ai", {})))
    ctx = {
        "title": title,
        "generated_at": generated_at,
        "selected_books": prepared_books[: cfg.get("top_books", 12)],
        "selected_rss": prepared_rss[: top_rss],
        "full_books": prepared_books[: cfg.get("full_books", 30)],
        "full_rss": full_rss,
        "weekly_summary": str(cfg.get("weekly_summary", "")).strip(),
        "stats": stats or {},
    }

    md_path = os.path.join(output_dir, f"{title}.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(MD_TEMPLATE.render(**ctx))
    logger.info("已生成 Markdown: %s", md_path)

    html_path = os.path.join(output_dir, f"{title}.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(HTML_TEMPLATE.render(**ctx))
    logger.info("已生成 HTML: %s", html_path)

    return md_path, html_path
