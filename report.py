"""报告生成模块，将抓取结果输出为 Markdown 和 HTML 周报。"""

from __future__ import annotations

import logging
import os
import base64
import json
import re
from hashlib import md5
from datetime import datetime
from html import unescape
from urllib.parse import urlparse
from typing import Any

import requests
from jinja2 import Environment, select_autoescape

logger = logging.getLogger(__name__)

_md_env = Environment(autoescape=False, trim_blocks=False, lstrip_blocks=False)
_html_env = Environment(
    autoescape=select_autoescape(default_for_string=True, enabled_extensions=("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)

MD_TEMPLATE = _md_env.from_string(
    """# {{ title }}

> 生成时间：{{ generated_at }}

---

## 抓取统计

| 项目 | 数量 |
|------|------|
| 豆瓣原始抓取 | {{ stats.raw_books }} |
| 豆瓣规则过滤后 | {{ stats.filtered_books }} |
| 豆瓣最终入选 | {{ stats.final_books }} |
| RSS 原始抓取 | {{ stats.raw_rss }} |
| RSS 规则过滤后 | {{ stats.filtered_rss }} |
| RSS 最终入选 | {{ stats.final_rss }} |

---

## 本周精选新书（共 {{ selected_books|length }} 本）

{% if selected_books %}
{% for book in selected_books %}
### {{ loop.index }}. {{ book.title }}
{% if book.subtitle %}_{{ book.subtitle }}_{% endif %}

| 字段 | 内容 |
|------|------|
| 作者 | {{ book.author_text }} |
| 出版社 | {{ book.press_text }} |
| 出版年 | {{ book.year_text }} |
| 评分 | {{ book.rating_text }} |
| 综合分 | {{ book.score_text }} |
| 链接 | [豆瓣页面]({{ book.url }}) |
| 状态 | {{ "本周新增" if book.is_new else "历史已见" }} |

{% if book.ai_recommend %}**AI 推荐**：{{ book.ai_recommend }}
{% endif %}
{% if book.abstract %}> {{ book.abstract }}
{% endif %}

{% endfor %}
{% else %}
本周没有符合条件的新书。
{% endif %}

---

## 本周资讯精选（共 {{ selected_rss|length }} 条）

{% if selected_rss %}
{% for entry in selected_rss %}
- **[{{ entry.title }}]({{ entry.url }})** — {{ entry.source_text }}{% if entry.published_text %} · {{ entry.published_text }}{% endif %}{% if entry.is_new %} · 本周新增{% endif %}
{% if entry.ai_summary %}  > {{ entry.ai_summary }}
{% endif %}
{% endfor %}
{% else %}
本周没有符合条件的资讯。
{% endif %}

---

## 新书完整清单（前 {{ full_books|length }} 本）

{% for book in full_books %}
- **[{{ book.title }}]({{ book.url }})** · {{ book.year_text }} · 评分 {{ book.rating_text }}{% if book.is_new %} · 本周新增{% endif %}

{% endfor %}

---

## 资讯完整清单（前 {{ full_rss|length }} 条）

{% for entry in full_rss %}
- **[{{ entry.title }}]({{ entry.url }})** — {{ entry.source_text }}{% if entry.published_text %} · {{ entry.published_text }}{% endif %}{% if entry.is_new %} · 本周新增{% endif %}
{% if entry.full_excerpt %}  > 精彩摘录：{{ entry.full_excerpt }}
{% endif %}

{% endfor %}

---

_本周报由推理资讯周报工具自动生成_
"""
)

HTML_TEMPLATE = _html_env.from_string(
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<style>
  :root {
    --bg: #08111f;
    --bg-soft: #111f35;
    --panel: rgba(12, 21, 37, 0.72);
    --panel-strong: rgba(9, 17, 30, 0.9);
    --line: rgba(143, 175, 214, 0.18);
    --ink: #edf4ff;
    --muted: #8fa5c4;
    --accent: #61d0ff;
    --accent-2: #7df0c8;
    --accent-3: #ffc857;
    --shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    color: var(--ink);
    background:
      radial-gradient(circle at 15% 20%, rgba(97, 208, 255, 0.2), transparent 24%),
      radial-gradient(circle at 85% 12%, rgba(125, 240, 200, 0.14), transparent 20%),
      radial-gradient(circle at 50% 100%, rgba(255, 200, 87, 0.08), transparent 28%),
      linear-gradient(160deg, #050b14 0%, #08111f 45%, #0f1c31 100%);
    font-family: "Noto Serif SC", "Source Han Serif SC", "Microsoft YaHei", serif;
    line-height: 1.75;
  }
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background-image:
      linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
    background-size: 32px 32px;
    mask-image: linear-gradient(180deg, rgba(0,0,0,0.5), transparent 85%);
  }
  a {
    color: inherit;
    text-decoration: none;
  }
  .page {
    width: min(1180px, calc(100% - 32px));
    margin: 0 auto;
    padding: 28px 0 72px;
  }
  .hero {
    position: relative;
    overflow: hidden;
    padding: 34px;
    border: 1px solid var(--line);
    border-radius: 32px;
    background:
      linear-gradient(135deg, rgba(97, 208, 255, 0.1), transparent 42%),
      linear-gradient(135deg, rgba(255, 200, 87, 0.1), transparent 65%),
      var(--panel-strong);
    box-shadow: var(--shadow);
    isolation: isolate;
  }
  .hero::after {
    content: "";
    position: absolute;
    width: 360px;
    height: 360px;
    right: -80px;
    top: -110px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(97, 208, 255, 0.28), transparent 68%);
    filter: blur(10px);
    z-index: -1;
  }
  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border-radius: 999px;
    border: 1px solid rgba(125, 240, 200, 0.22);
    background: rgba(125, 240, 200, 0.08);
    color: var(--accent-2);
    font-size: 0.8rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .hero-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(280px, 0.8fr);
    gap: 28px;
    align-items: end;
    margin-top: 22px;
  }
  h1 {
    margin: 0;
    font-size: clamp(2.6rem, 5vw, 4.8rem);
    line-height: 0.98;
    letter-spacing: 0.02em;
  }
  .hero-copy {
    max-width: 760px;
  }
  .hero-side {
    display: grid;
    gap: 14px;
  }
  .hero-card {
    padding: 18px 18px 16px;
    border: 1px solid var(--line);
    border-radius: 22px;
    background: rgba(255, 255, 255, 0.03);
    backdrop-filter: blur(14px);
  }
  .hero-label {
    color: var(--muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .hero-value {
    margin-top: 8px;
    font-size: clamp(1.6rem, 3vw, 2.4rem);
    line-height: 1;
    font-weight: 700;
    color: var(--accent);
  }
  .hero-sub {
    margin-top: 10px;
    color: var(--muted);
    font-size: 0.92rem;
  }
  .stack {
    display: grid;
    gap: 22px;
    margin-top: 24px;
  }
  .section {
    padding: 24px;
    border: 1px solid var(--line);
    border-radius: 28px;
    background: var(--panel);
    backdrop-filter: blur(18px);
    box-shadow: var(--shadow);
  }
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 18px;
  }
  h2 {
    margin: 0;
    font-size: 1.45rem;
    color: var(--ink);
  }
  .section-note {
    color: var(--muted);
    font-size: 0.92rem;
  }
  .book-grid {
    display: grid;
    gap: 16px;
  }
  .book-card {
    position: relative;
    display: grid;
    grid-template-columns: 120px minmax(0, 1fr);
    gap: 20px;
    padding: 20px;
    border-radius: 24px;
    border: 1px solid rgba(143, 175, 214, 0.14);
    background:
      linear-gradient(135deg, rgba(97, 208, 255, 0.06), transparent 48%),
      rgba(255, 255, 255, 0.03);
    transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
  }
  .book-card:hover {
    transform: translateY(-2px);
    border-color: rgba(97, 208, 255, 0.32);
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.24);
  }
  .rank {
    position: absolute;
    top: 14px;
    right: 16px;
    font-size: 0.8rem;
    color: rgba(237, 244, 255, 0.34);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .cover {
    width: 120px;
    height: 168px;
    object-fit: cover;
    border-radius: 18px;
    background:
      linear-gradient(145deg, rgba(97, 208, 255, 0.16), rgba(255, 255, 255, 0.04));
    border: 1px solid rgba(255, 255, 255, 0.08);
  }
  .cover-fallback {
    display: flex;
    align-items: end;
    justify-content: start;
    padding: 14px;
    color: rgba(237, 244, 255, 0.8);
    font-size: 0.82rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .book-title {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin: 0;
    font-size: 1.32rem;
    line-height: 1.2;
  }
  .book-title a:hover,
  .rss-title a:hover,
  .mini-title a:hover {
    color: var(--accent);
  }
  .subtitle {
    margin-top: 6px;
    color: var(--muted);
    font-size: 0.95rem;
  }
  .meta-line {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 14px;
    margin-top: 12px;
    color: var(--muted);
    font-size: 0.94rem;
  }
  .meta-chip {
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid rgba(143, 175, 214, 0.16);
    background: rgba(255, 255, 255, 0.03);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(125, 240, 200, 0.12);
    border: 1px solid rgba(125, 240, 200, 0.24);
    color: var(--accent-2);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.04em;
  }
  .summary,
  .reason {
    margin-top: 14px;
    padding: 14px 16px;
    border-radius: 18px;
    border: 1px solid rgba(97, 208, 255, 0.14);
    background: rgba(97, 208, 255, 0.06);
    color: #dce9ff;
    font-size: 0.95rem;
  }
  .list-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .mini-card, .rss-card {
    padding: 16px 18px;
    border-radius: 20px;
    border: 1px solid rgba(143, 175, 214, 0.14);
    background: rgba(255, 255, 255, 0.03);
  }
  .mini-title, .rss-title {
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.35;
  }
  .mini-meta, .rss-meta {
    margin-top: 8px;
    color: var(--muted);
    font-size: 0.9rem;
  }
  .empty {
    padding: 18px;
    border-radius: 18px;
    background: rgba(255,255,255,0.03);
    color: var(--muted);
  }
  .closing {
    position: relative;
    overflow: hidden;
    padding: 24px;
    border-radius: 26px;
    border: 1px solid rgba(125, 240, 200, 0.18);
    background:
      linear-gradient(135deg, rgba(125, 240, 200, 0.08), transparent 45%),
      linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
  }
  .closing-label {
    color: var(--accent-2);
    font-size: 0.8rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .closing-text {
    margin-top: 10px;
    font-size: 1.1rem;
    color: var(--ink);
  }
  footer {
    padding-top: 10px;
    color: var(--muted);
    font-size: 0.88rem;
    text-align: center;
  }
  @media (max-width: 900px) {
    .hero-grid,
    .list-grid {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 720px) {
    .page {
      width: min(100% - 20px, 1180px);
      padding: 14px 0 40px;
    }
    .hero,
    .section {
      padding: 18px;
      border-radius: 22px;
    }
    .book-card {
      grid-template-columns: 1fr;
      padding: 18px;
    }
    .cover {
      width: 100%;
      max-width: 180px;
      height: 220px;
    }
    .rank {
      position: static;
      margin-bottom: 8px;
      display: inline-block;
    }
  }
</style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">Mystery Weekly Digest</div>
      <div class="hero-grid">
        <div class="hero-copy">
          <h1>{{ title }}</h1>
        </div>
        <div class="hero-side">
          <div class="hero-card">
            <div class="hero-label">本期新书</div>
            <div class="hero-value">{{ selected_books|length }}</div>
            <div class="hero-sub">生成时间：{{ generated_at }}</div>
          </div>
          <div class="hero-card">
            <div class="hero-label">本期资讯</div>
            <div class="hero-value">{{ selected_rss|length }}</div>
            <div class="hero-sub">自动整理后的最终展示结果</div>
          </div>
        </div>
      </div>
    </section>

    <div class="stack">
      <section class="section">
        <div class="section-head">
          <h2>本周精选新书</h2>
          <div class="section-note">共 {{ selected_books|length }} 本</div>
        </div>
        {% if selected_books %}
        <div class="book-grid">
          {% for book in selected_books %}
          <article class="book-card">
            <div class="rank">No.{{ "%02d" % loop.index }}</div>
            {% if book.cover_url %}
            <img class="cover" src="{{ book.cover_url }}" alt="《{{ book.title }}》封面">
            {% else %}
            <div class="cover cover-fallback">Mystery<br>Selection</div>
            {% endif %}
            <div>
              <h3 class="book-title">
                <a href="{{ book.url }}" target="_blank" rel="noopener noreferrer">{{ book.title }}</a>
                {% if book.is_new %}<span class="badge">本周新增</span>{% endif %}
              </h3>
              {% if book.subtitle %}<div class="subtitle">{{ book.subtitle }}</div>{% endif %}
              <div class="meta-line">
                <span class="meta-chip">{{ book.author_text }}</span>
                <span class="meta-chip">{{ book.press_text }}</span>
                <span class="meta-chip">{{ book.year_text }}</span>
                <span class="meta-chip">{{ book.rating_text }}</span>
                <span class="meta-chip">综合分 {{ book.score_text }}</span>
              </div>
              {% if book.ai_recommend %}<div class="reason"><strong>AI 推荐：</strong>{{ book.ai_recommend }}</div>{% endif %}
              {% if book.abstract %}<div class="summary">{{ book.abstract }}</div>{% endif %}
            </div>
          </article>
          {% endfor %}
        </div>
        {% else %}
        <div class="empty">本周没有符合条件的新书。</div>
        {% endif %}
      </section>

      <section class="section">
        <div class="section-head">
          <h2>本周资讯精选</h2>
          <div class="section-note">共 {{ selected_rss|length }} 条</div>
        </div>
        {% if selected_rss %}
        <div class="list-grid">
          {% for entry in selected_rss %}
          <article class="rss-card">
            <div class="rss-title"><a href="{{ entry.url }}" target="_blank" rel="noopener noreferrer">{{ entry.title }}</a></div>
            <div class="rss-meta">{{ entry.source_text }}{% if entry.published_text %} · {{ entry.published_text }}{% endif %}{% if entry.is_new %} · 本周新增{% endif %}</div>
            {% if entry.ai_summary %}<div class="summary">{{ entry.ai_summary }}</div>{% endif %}
          </article>
          {% endfor %}
        </div>
        {% else %}
        <div class="empty">本周没有符合条件的资讯。</div>
        {% endif %}
      </section>

      <section class="section">
        <div class="section-head">
          <h2>新书完整清单</h2>
          <div class="section-note">前 {{ full_books|length }} 本</div>
        </div>
        {% if full_books %}
        <div class="list-grid">
          {% for book in full_books %}
          <article class="mini-card">
            <div class="mini-title"><a href="{{ book.url }}" target="_blank" rel="noopener noreferrer">{{ book.title }}</a></div>
            <div class="mini-meta">{{ book.author_text }} · {{ book.press_text }} · {{ book.year_text }} · {{ book.rating_text }}{% if book.is_new %} · 本周新增{% endif %}</div>
          </article>
          {% endfor %}
        </div>
        {% else %}
        <div class="empty">没有可展示的新书。</div>
        {% endif %}
      </section>

      <section class="section">
        <div class="section-head">
          <h2>资讯完整清单</h2>
          <div class="section-note">前 {{ full_rss|length }} 条</div>
        </div>
        {% if full_rss %}
        <div class="list-grid">
          {% for entry in full_rss %}
          <article class="rss-card">
            <div class="rss-title"><a href="{{ entry.url }}" target="_blank" rel="noopener noreferrer">{{ entry.title }}</a></div>
            <div class="rss-meta">{{ entry.source_text }}{% if entry.published_text %} · {{ entry.published_text }}{% endif %}{% if entry.is_new %} · 本周新增{% endif %}</div>
            {% if entry.full_excerpt %}<div class="summary"><strong>精彩摘录：</strong>{{ entry.full_excerpt }}</div>{% endif %}
          </article>
          {% endfor %}
        </div>
        {% else %}
        <div class="empty">没有可展示的资讯。</div>
        {% endif %}
      </section>

      {% if weekly_summary %}
      <section class="closing">
        <div class="closing-label">Weekly Note</div>
        <div class="closing-text">{{ weekly_summary }}</div>
      </section>
      {% endif %}
    </div>

    <footer>本周报由推理资讯周报工具自动生成</footer>
  </main>
</body>
</html>
"""
)


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


def _file_to_data_uri(file_path: str) -> str:
    if not os.path.exists(file_path):
        return ""
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    content_type = mime_map.get(ext, "image/jpeg")
    with open(file_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


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
    for book in prepared_books:
        local_cover = _cache_cover_image(str(book.get("cover_url") or ""), output_dir, str(book.get("id") or ""))
        if local_cover:
            embedded_cover = _file_to_data_uri(os.path.join(output_dir, local_cover))
            book["cover_url"] = embedded_cover or local_cover
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
