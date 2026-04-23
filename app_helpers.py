from __future__ import annotations

import json
import logging
import math
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "douban": {
        "tags": ["推理", "悬疑", "侦探小说", "推理小说"],
        "max_pages": 3,
        "sort": "R",
        "delay": 2,
        "search_max_pages": 1,
        "search_keywords": [],
    },
    "rss": {
        "feeds": [],
        "days": 7,
    },
    "china_sources": {
        "enabled": True,
        "days": 14,
        "timeout": 20,
        "target_year": datetime.now().year,
        "douban_doulists": [],
        "chinawriter_pages": [],
    },
    "ai_filter": {
        "enabled": True,
        "filter_rss": True,
        "filter_douban": True,
        "batch_size": 8,
        "fail_closed": True,
        "providers": [],
    },
    "filter_rules": {
        "target_year": datetime.now().year,
        "min_rating": 0,
        "min_rating_count": 20,
        "exact_target_year_only": True,
        "recent_months_window": 12,
        "exclude_keywords": ["研究", "评论", "论文", "教材", "漫画", "绘本", "纪实", "案例"],
        "rss_include_keywords": [
            "推理",
            "悬疑",
            "侦探",
            "本格",
            "犯罪小说",
            "惊悚",
            "间谍小说",
            "法庭悬疑",
            "mystery",
            "crime fiction",
            "detective",
            "whodunit",
            "noir",
            "thriller",
            "locked room",
            "serial killer",
            "spy fiction",
        ],
        "rss_exclude_keywords": [
            "young adult",
            "ya ",
            "romance",
            "poetry",
            "nonfiction",
            "memoir",
            "cookbook",
            "self-help",
            "science fiction",
            "fantasy",
            "children",
            "picture book",
            "奖项",
            "诗歌",
            "传记",
            "非虚构",
            "散文",
            "科幻",
            "奇幻",
            "童书",
            "绘本",
            "青春",
            "言情",
            "horror",
            "politics",
            "history",
            "essay",
            "poem",
            "习近平",
            "脱贫攻坚",
            "互联网大会",
            "现代化",
            "复兴",
            "经济文选",
            "学习教育",
            "座谈会",
        ],
        "max_books_before_ai": 50,
        "max_rss_before_ai": 30,
        "top_books": 12,
        "top_rss": 10,
        "full_books": 30,
        "full_rss": 20,
    },
    "report": {
        "output_dir": "output",
        "title_prefix": "推理资讯周报",
    },
    "state": {
        "path": "data/state.json",
        "max_entries_per_bucket": 2000,
    },
    "schedule": {
        "day": "friday",
        "time": "18:00",
    },
}

_STATE_META_KEY = "__meta__"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def ensure_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fill missing config fields without overwriting user values."""
    return _deep_merge(DEFAULT_CONFIG, cfg or {})


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    rules = cfg.get("filter_rules", {})
    if int(rules.get("target_year", 0)) < 2000:
        errors.append("filter_rules.target_year 必须是有效年份")

    for key in ("max_books_before_ai", "max_rss_before_ai", "top_books", "top_rss", "full_books", "full_rss"):
        if int(rules.get(key, 0)) <= 0:
            errors.append(f"filter_rules.{key} 必须大于 0")

    rss_cfg = cfg.get("rss", {})
    feeds = rss_cfg.get("feeds", [])
    if not isinstance(feeds, list):
        errors.append("rss.feeds 必须是列表")
    else:
        for index, feed in enumerate(feeds):
            if not isinstance(feed, dict):
                errors.append(f"rss.feeds[{index}] 必须是对象")
                continue
            if not str(feed.get("url", "")).strip():
                errors.append(f"rss.feeds[{index}].url 不能为空")

    china_cfg = cfg.get("china_sources", {})
    for key in ("douban_doulists", "chinawriter_pages"):
        items = china_cfg.get(key, [])
        if not isinstance(items, list):
            errors.append(f"china_sources.{key} 必须是列表")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"china_sources.{key}[{index}] 必须是对象")
                continue
            if not str(item.get("url", "")).strip():
                errors.append(f"china_sources.{key}[{index}].url 不能为空")

    ai_cfg = cfg.get("ai_filter", {})
    if int(ai_cfg.get("batch_size", 1)) <= 0:
        errors.append("ai_filter.batch_size 必须大于 0")

    state_cfg = cfg.get("state", {})
    if not str(state_cfg.get("path", "")).strip():
        errors.append("state.path 不能为空")

    schedule_cfg = cfg.get("schedule", {})
    if not re.match(r"^\d{2}:\d{2}$", str(schedule_cfg.get("time", ""))):
        errors.append("schedule.time 必须是 HH:MM 格式")

    return errors


def _flatten_text(parts: list[Any]) -> str:
    values: list[str] = []
    for part in parts:
        if isinstance(part, list):
            values.extend(str(item) for item in part if item)
        elif part:
            values.append(str(part))
    return " ".join(values).casefold()


def _contains_any(text: str, keywords: list[str]) -> bool:
    normalized = text.casefold()
    return any(keyword.casefold() in normalized for keyword in keywords if keyword)


def _count_matches(text: str, keywords: list[str]) -> int:
    normalized = text.casefold()
    return sum(1 for keyword in keywords if keyword and keyword.casefold() in normalized)


def _extract_year(value: Any) -> int | None:
    match = re.search(r"(20\d{2}|19\d{2})", str(value or ""))
    if not match:
        return None
    return int(match.group(1))


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_books_by_rules(books: list[dict], rules_cfg: dict[str, Any]) -> list[dict]:
    target_year = int(rules_cfg.get("target_year", datetime.now().year))
    min_rating = float(rules_cfg.get("min_rating", 0))
    min_rating_count = int(rules_cfg.get("min_rating_count", 0))
    exact_target_year_only = bool(rules_cfg.get("exact_target_year_only", True))
    recent_months_window = int(rules_cfg.get("recent_months_window", 12))
    exclude_keywords = list(rules_cfg.get("exclude_keywords", []))

    earliest_year = target_year if exact_target_year_only else max(target_year - math.ceil(recent_months_window / 12), 2000)

    filtered: list[dict] = []
    for book in books:
        source = str(book.get("source") or "")
        is_china_book = source.startswith("china_book:")
        title_text = _flatten_text(
            [
                book.get("title"),
                book.get("subtitle"),
                book.get("abstract"),
                book.get("author", []),
                book.get("press", []),
            ]
        )
        if _contains_any(title_text, exclude_keywords):
            continue

        year = _extract_year(book.get("year"))
        if year is None:
            continue
        if exact_target_year_only and year != target_year:
            continue
        if not exact_target_year_only and year < earliest_year:
            continue

        rating = float(book.get("rating") or 0)
        rating_count = int(book.get("rating_count") or 0)
        # 中文辅助图书源主要用于补“2026 新出版 / 待出版”条目，允许暂无评分先入池。
        if not is_china_book:
            if rating < min_rating:
                continue
            if rating_count < min_rating_count:
                continue

        normalized = dict(book)
        normalized["year"] = str(year)
        filtered.append(normalized)

    return filtered


def filter_rss_by_rules(entries: list[dict], rules_cfg: dict[str, Any]) -> list[dict]:
    include_keywords = list(rules_cfg.get("rss_include_keywords", []))
    exclude_keywords = list(rules_cfg.get("rss_exclude_keywords", [])) + list(rules_cfg.get("exclude_keywords", []))

    filtered: list[dict] = []
    for entry in entries:
        text = _flatten_text(
            [
                entry.get("title"),
                entry.get("summary"),
                entry.get("source"),
            ]
        )
        if include_keywords and not _contains_any(text, include_keywords):
            continue
        if exclude_keywords and _contains_any(text, exclude_keywords):
            continue
        filtered.append(dict(entry))

    return filtered


def score_books(books: list[dict], target_year: int) -> list[dict]:
    scored: list[dict] = []
    for book in books:
        source = str(book.get("source") or "")
        is_china_book = source.startswith("china_book:")
        year = _extract_year(book.get("year")) or target_year
        rating = float(book.get("rating") or 0)
        rating_count = int(book.get("rating_count") or 0)

        score = 0.0
        score += rating * 12
        score += min(math.log10(rating_count + 1) * 10, 35)
        score += max(0, 8 - abs(target_year - year) * 4)

        normalized = dict(book)
        normalized["score"] = round(score, 2)
        # 排序优先级：
        # 1. 已上市且有评分的常规豆瓣条目
        # 2. 中文辅助源里的 2026 待出版/无评分条目
        normalized["sort_bucket"] = 0 if is_china_book and rating_count <= 0 else 1
        scored.append(normalized)

    scored.sort(
        key=lambda item: (
            -int(item.get("sort_bucket", 0)),
            float(item.get("score", 0)),
            float(item.get("rating", 0)),
            int(item.get("rating_count", 0)),
            str(item.get("title", "")),
        ),
        reverse=True,
    )
    return scored


def score_rss(entries: list[dict]) -> list[dict]:
    topic_keywords = [
        "推理",
        "悬疑",
        "侦探",
        "犯罪小说",
        "mystery",
        "crime",
        "detective",
        "thriller",
        "noir",
        "whodunit",
        "new book",
        "review",
    ]
    now = datetime.now(timezone.utc)
    scored: list[dict] = []

    for entry in entries:
        text = _flatten_text([entry.get("title"), entry.get("summary")])
        published_dt = _parse_iso_datetime(str(entry.get("published", "")))
        age_days = 999
        if published_dt:
            age_days = max((now - published_dt).days, 0)

        score = 20 + _count_matches(text, topic_keywords) * 8
        score += max(0, 14 - age_days)
        if "review" in text:
            score += 4
        if "new book" in text or "new books" in text:
            score += 4

        normalized = dict(entry)
        normalized["score"] = round(score, 2)
        scored.append(normalized)

    scored.sort(
        key=lambda item: (
            float(item.get("score", 0)),
            str(item.get("published", "")),
            str(item.get("title", "")),
        ),
        reverse=True,
    )
    return scored


def load_state(project_root: str, state_cfg: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(state_cfg.get("path", DEFAULT_CONFIG["state"]["path"]))
    state_path = os.path.join(project_root, rel_path)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    state: dict[str, Any]
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("状态文件损坏，已使用空状态重新开始: %s", state_path)
            state = {}
    else:
        state = {}

    state.setdefault("books", {})
    state.setdefault("rss", {})
    state[_STATE_META_KEY] = {"path": state_path}
    return state


def _stable_item_id(item: dict[str, Any]) -> str:
    for key in ("id", "url", "title"):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return ""


def mark_new_items(items: list[dict], state: dict[str, Any], bucket: str) -> list[dict]:
    bucket_state = state.setdefault(bucket, {})
    today = datetime.now().date().isoformat()
    marked: list[dict] = []

    for item in items:
        item_id = _stable_item_id(item)
        normalized = dict(item)
        if not item_id:
            normalized["is_new"] = False
            marked.append(normalized)
            continue

        existing = bucket_state.get(item_id)
        normalized["is_new"] = existing is None
        bucket_state[item_id] = {
            "title": str(item.get("title", "")),
            "first_seen": existing.get("first_seen", today) if existing else today,
            "last_seen": today,
        }
        marked.append(normalized)

    return marked


def save_state(state: dict[str, Any], max_entries_per_bucket: int) -> None:
    meta = state.get(_STATE_META_KEY, {})
    state_path = meta.get("path")
    if not state_path:
        raise ValueError("state path is missing")

    payload = {key: value for key, value in state.items() if key != _STATE_META_KEY}
    limit = max(int(max_entries_per_bucket), 1)

    for bucket in ("books", "rss"):
        bucket_state = payload.get(bucket, {})
        if not isinstance(bucket_state, dict):
            payload[bucket] = {}
            continue
        sorted_items = sorted(
            bucket_state.items(),
            key=lambda item: (
                item[1].get("last_seen", ""),
                item[1].get("first_seen", ""),
                item[0],
            ),
            reverse=True,
        )
        payload[bucket] = dict(sorted_items[:limit])

    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
