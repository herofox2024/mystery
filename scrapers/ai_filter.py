"""AI filtering with sequential provider fallback and batched prompts."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_client_cache: dict[tuple[str, str, str, float, int], Any] = {}

API_DELAY = 1.0
SUPPORTED_PROVIDERS = {"openrouter", "openai", "qwen", "deepseek", "doubao"}


def _default_base_url(provider: str) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "qwen":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider == "doubao":
        return "https://ark.cn-beijing.volces.com/api/v3"
    raise ValueError(f"Unsupported AI provider: {provider}")


def _default_model(provider: str) -> str:
    if provider == "openrouter":
        return "openrouter/auto"
    if provider == "openai":
        return "gpt-4.1-mini"
    if provider == "qwen":
        return "qwen-plus"
    if provider == "deepseek":
        return "deepseek-chat"
    if provider == "doubao":
        return "doubao-seed-1-6-250615"
    raise ValueError(f"Unsupported AI provider: {provider}")


def _resolve_api_key(provider: str, explicit_key: str) -> str:
    env_map = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "doubao": "DOUBAO_API_KEY",
    }
    env_key = env_map[provider]
    return os.environ.get(env_key) or explicit_key


def _provider_headers(provider: str) -> dict[str, str]:
    if provider == "openrouter":
        return {
            "HTTP-Referer": "https://github.com/mystery-weekly-report",
            "X-Title": "mystery-weekly-report",
        }
    return {}


def _normalize_provider_entry(item: dict[str, Any]) -> dict[str, Any]:
    provider = str(item.get("provider") or item.get("name") or "openrouter").lower().strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported AI provider: {provider}")

    api_key = _resolve_api_key(provider, str(item.get("api_key", "")).strip())
    base_url = str(item.get("base_url", "")).strip() or _default_base_url(provider)
    model = str(item.get("model", "")).strip() or _default_model(provider)

    entry = {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "headers": _provider_headers(provider),
        "timeout": float(item.get("timeout", 30)),
        "max_retries": int(item.get("max_retries", 1)),
    }

    if provider == "doubao":
        endpoint_id = os.environ.get("DOUBAO_ENDPOINT_ID_TEXT") or str(item.get("endpoint_id", "")).strip()
        if endpoint_id:
            entry["model"] = endpoint_id

    return entry


def _resolve_provider_pool(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    providers = cfg.get("providers")
    if isinstance(providers, list) and providers:
        return [_normalize_provider_entry(item or {}) for item in providers]

    legacy_item = {
        "provider": cfg.get("provider", "openrouter"),
        "api_key": cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
    }
    return [_normalize_provider_entry(legacy_item)]


def _get_client(provider_cfg: dict[str, Any]):
    provider = provider_cfg["provider"]
    api_key = provider_cfg["api_key"]
    base_url = provider_cfg["base_url"]
    headers = provider_cfg["headers"]
    timeout = float(provider_cfg.get("timeout", 30))
    max_retries = int(provider_cfg.get("max_retries", 1))

    cache_key = (provider, api_key, base_url, timeout, max_retries)
    if cache_key not in _client_cache:
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if headers:
            kwargs["default_headers"] = headers
        _client_cache[cache_key] = OpenAI(**kwargs)
    return _client_cache[cache_key]


def _chat_with_fallback(prompt: str, cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    pool = _resolve_provider_pool(cfg)
    if not pool:
        return None, None

    available = [item for item in pool if item.get("api_key")]
    if not available:
        logger.warning("AI filtering: no valid API key configured for any provider, skipping")
        return None, None

    last_exc: Exception | None = None
    for item in available:
        provider = item["provider"]
        model = item["model"]
        try:
            logger.info("AI filtering: trying provider=%s model=%s", provider, model)
            client = _get_client(item)
            result = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            text = (result.choices[0].message.content or "").strip()
            if text:
                return text, f"{provider}:{model}"
            raise ValueError("empty response")
        except Exception as exc:
            last_exc = exc
            logger.warning("AI filtering failed for provider=%s model=%s: %s", provider, model, exc)

    if last_exc:
        logger.warning("AI filtering: all providers failed, fallback to non-AI path: %s", last_exc)
    return None, None


def _chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _should_fail_closed(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("fail_closed", True))


def _parse_json_response(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI filtering: invalid JSON response")
        return None


def filter_rss_entry(entry: dict, cfg: dict) -> dict | None:
    """Backward-compatible single-item wrapper."""
    result = filter_rss_entries([entry], cfg)
    return result[0] if result else None


def filter_rss_entries(entries: list[dict], cfg: dict) -> list[dict]:
    """Batch filter RSS items."""
    if not cfg.get("enabled", False) or not cfg.get("filter_rss", True):
        return entries

    logger.info("AI filtering RSS entries: processing %d items", len(entries))
    filtered: list[dict] = []
    batch_size = int(cfg.get("batch_size", 8))
    fail_closed = _should_fail_closed(cfg)
    batches = _chunked(entries, batch_size)

    for batch_index, batch in enumerate(batches):
        payload = [
            {
                "id": str(i),
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "")[:1200],
                "url": entry.get("url", ""),
            }
            for i, entry in enumerate(batch)
        ]
        prompt = (
            "判断每条资讯是否与推理小说、悬疑出版、侦探文学动态有关。"
            "返回 JSON，对象格式为 "
            '{"items":[{"id":"0","keep":true,"summary":"80字内中文摘要"}]}。'
            "如果不相关，keep 设为 false，summary 设为空字符串。\n"
            f"输入数据：{json.dumps(payload, ensure_ascii=False)}"
        )

        text, source = _chat_with_fallback(prompt, cfg)
        data = _parse_json_response(text) if text else None
        items = data.get("items", []) if isinstance(data, dict) else []
        decisions = {str(item.get("id")): item for item in items if isinstance(item, dict)}

        if not decisions and fail_closed:
            logger.warning(
                "AI filtering RSS entries: no valid decision for current batch, dropping %d items because fail_closed=true",
                len(batch),
            )
            if batch_index < len(batches) - 1:
                time.sleep(API_DELAY)
            continue

        for i, entry in enumerate(batch):
            decision = decisions.get(str(i))
            if not decision:
                if fail_closed:
                    logger.warning(
                        "AI filtering RSS entries: missing decision for item '%s', dropped because fail_closed=true",
                        entry.get("title", ""),
                    )
                    continue
                filtered.append(entry)
                continue
            if decision.get("keep"):
                normalized = dict(entry)
                summary = str(decision.get("summary", "")).strip()
                if summary:
                    normalized["ai_summary"] = summary
                if source:
                    normalized["ai_provider"] = source
                filtered.append(normalized)

        if batch_index < len(batches) - 1:
            time.sleep(API_DELAY)

    logger.info("AI filtering RSS entries: kept %d items", len(filtered))
    return filtered


def filter_douban_book(book: dict, cfg: dict) -> dict | None:
    """Backward-compatible single-item wrapper."""
    result = filter_douban_books([book], cfg)
    return result[0] if result else None


def filter_douban_books(books: list[dict], cfg: dict) -> list[dict]:
    """Batch filter books."""
    if not cfg.get("enabled", False) or not cfg.get("filter_douban", False):
        return books

    logger.info("AI filtering Douban books: processing %d books", len(books))
    filtered: list[dict] = []
    batch_size = int(cfg.get("batch_size", 8))
    target_year = int(cfg.get("target_year", time.localtime().tm_year))
    fail_closed = _should_fail_closed(cfg)
    batches = _chunked(books, batch_size)

    for batch_index, batch in enumerate(batches):
        payload = [
            {
                "id": str(i),
                "title": book.get("title", ""),
                "author": ", ".join(book.get("author", [])),
                "press": ", ".join(book.get("press", [])),
                "year": book.get("year", ""),
                "rating": book.get("rating", 0),
                "abstract": book.get("abstract", "")[:800],
            }
            for i, book in enumerate(batch)
        ]
        prompt = (
            f"判断图书是否属于 {target_year} 年及之后出版的推理/悬疑/侦探小说，"
            "排除研究、评论、纪实、漫画、教材。"
            "返回 JSON，对象格式为 "
            '{"items":[{"id":"0","keep":true,"reason":"60字内中文推荐理由"}]}。\n'
            f"输入数据：{json.dumps(payload, ensure_ascii=False)}"
        )

        text, source = _chat_with_fallback(prompt, cfg)
        data = _parse_json_response(text) if text else None
        items = data.get("items", []) if isinstance(data, dict) else []
        decisions = {str(item.get("id")): item for item in items if isinstance(item, dict)}

        if not decisions and fail_closed:
            logger.warning(
                "AI filtering Douban books: no valid decision for current batch, dropping %d books because fail_closed=true",
                len(batch),
            )
            if batch_index < len(batches) - 1:
                time.sleep(API_DELAY)
            continue

        for i, book in enumerate(batch):
            decision = decisions.get(str(i))
            if not decision:
                if fail_closed:
                    logger.warning(
                        "AI filtering Douban books: missing decision for '%s', dropped because fail_closed=true",
                        book.get("title", ""),
                    )
                    continue
                filtered.append(book)
                continue
            if decision.get("keep"):
                normalized = dict(book)
                reason = str(decision.get("reason", "")).strip()
                if reason:
                    normalized["ai_recommend"] = reason
                if source:
                    normalized["ai_provider"] = source
                filtered.append(normalized)

        if batch_index < len(batches) - 1:
            time.sleep(API_DELAY)

    logger.info("AI filtering Douban books: kept %d books for target year %s", len(filtered), target_year)
    return filtered


def summarize_weekly_selection(books: list[dict], entries: list[dict], cfg: dict) -> str:
    """Generate a one-line summary for the final weekly selection."""
    if not books and not entries:
        return "本周暂无符合条件的推理新书与资讯。"

    if cfg.get("enabled"):
        payload = {
            "books": [
                {
                    "title": book.get("title", ""),
                    "author": ", ".join(book.get("author", [])),
                    "press": ", ".join(book.get("press", [])),
                    "year": book.get("year", ""),
                    "rating": book.get("rating", 0),
                }
                for book in books[:8]
            ],
            "entries": [
                {
                    "title": entry.get("title", ""),
                    "source": entry.get("source", ""),
                }
                for entry in entries[:6]
            ],
        }
        prompt = (
            "请基于本周推理周报的最终入选内容，生成 1 句话中文总结。"
            "要求：18到45字，像编辑导语，概括本周新书风格或趋势，不要分点，不要加书名号列表，不要输出多句。"
            "返回 JSON，格式为 "
            '{"summary":"一句话总结"}。\n'
            f"输入数据：{json.dumps(payload, ensure_ascii=False)}"
        )
        text, _ = _chat_with_fallback(prompt, cfg)
        data = _parse_json_response(text) if text else None
        summary = str(data.get("summary", "")).strip() if isinstance(data, dict) else ""
        if summary:
            return summary

    book_count = len(books)
    entry_count = len(entries)
    top_titles = [str(book.get("title", "")).strip() for book in books[:3] if str(book.get("title", "")).strip()]
    if book_count and entry_count:
        return f"本周入选 {book_count} 本推理新书与 {entry_count} 条资讯，重点围绕{ '、'.join(top_titles) if top_titles else '年度悬疑新作' }展开。"
    if book_count:
        return f"本周入选 {book_count} 本推理新书，整体以{ '、'.join(top_titles) if top_titles else '年度悬疑与侦探新作' }为代表。"
    return f"本周共整理 {entry_count} 条推理相关资讯，内容聚焦出版与文学动态。"
