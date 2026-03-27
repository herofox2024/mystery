"""推理资讯周报 — 主入口

用法:
    python main.py           # 启动定时模式，每周五自动执行
    python main.py --once    # 立即执行一次并退出
"""

import argparse
import logging
import os
import sys
import time

import schedule
import yaml

from app_helpers import (
    ensure_defaults,
    filter_books_by_rules,
    filter_rss_by_rules,
    load_state,
    mark_new_items,
    save_state,
    score_books,
    score_rss,
    validate_config,
)
from report import generate_report
from scrapers import (
    fetch_china_book_entries,
    fetch_china_entries,
    fetch_douban_books,
    fetch_rss_entries,
    filter_rss_entries,
    filter_douban_books,
)
from scrapers.ai_filter import summarize_weekly_selection

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("weekly_report")


def load_config() -> dict:
    """加载 config.yaml 配置文件。"""
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 基础配置验证
    if not cfg:
        logger.error("配置文件为空")
        sys.exit(1)

    cfg = ensure_defaults(cfg)
    errors = validate_config(cfg)
    if errors:
        for error in errors:
            logger.error("配置错误: %s", error)
        sys.exit(1)

    return cfg


def run_once(cfg: dict) -> None:
    """执行一次完整的抓取 + 生成流程。"""
    logger.info("===== 开始执行推理资讯周报生成 =====")
    rules_cfg = cfg.get("filter_rules", {})
    ai_cfg = cfg.get("ai_filter", {})
    state = load_state(PROJECT_ROOT, cfg.get("state", {}))
    stats = {
        "raw_books": 0,
        "filtered_books": 0,
        "final_books": 0,
        "raw_rss": 0,
        "filtered_rss": 0,
        "final_rss": 0,
    }

    # 1. 豆瓣新书
    books = []
    try:
        books = fetch_douban_books(cfg.get("douban", {}))
    except Exception:
        logger.error("豆瓣抓取异常", exc_info=True)

    # 1.1 中文图书辅助源（如豆瓣豆列）
    china_books = []
    try:
        china_cfg = dict(cfg.get("china_sources", {}))
        china_cfg.setdefault("target_year", rules_cfg.get("target_year"))
        china_books = fetch_china_book_entries(china_cfg)
    except Exception:
        logger.error("中文图书源抓取异常", exc_info=True)
    books.extend(china_books)

    # 2. RSS 资讯
    rss_entries = []
    try:
        rss_entries = fetch_rss_entries(cfg.get("rss", {}))
    except Exception:
        logger.error("RSS 抓取异常", exc_info=True)

    # 2.1 中文资讯源
    china_entries = []
    try:
        china_cfg = dict(cfg.get("china_sources", {}))
        china_cfg.setdefault("target_year", rules_cfg.get("target_year"))
        china_entries = fetch_china_entries(china_cfg)
    except Exception:
        logger.error("中文资讯源抓取异常", exc_info=True)

    rss_entries.extend(china_entries)

    stats["raw_books"] = len(books)
    stats["raw_rss"] = len(rss_entries)

    # 3. 规则过滤 + 排序，先把明显低价值内容剔掉
    books = score_books(filter_books_by_rules(books, rules_cfg), int(rules_cfg["target_year"]))
    rss_entries = score_rss(filter_rss_by_rules(rss_entries, rules_cfg))

    stats["filtered_books"] = len(books)
    stats["filtered_rss"] = len(rss_entries)

    books = books[: rules_cfg.get("max_books_before_ai", 50)]
    rss_entries = rss_entries[: rules_cfg.get("max_rss_before_ai", 30)]

    # 4. AI 筛选
    if ai_cfg.get("enabled"):
        ai_cfg = dict(ai_cfg)
        ai_cfg["target_year"] = rules_cfg["target_year"]

        # 4.1 AI 筛选豆瓣书籍
        if ai_cfg.get("filter_douban") and books:
            try:
                books = filter_douban_books(books, ai_cfg)
            except Exception:
                logger.error("AI 筛选豆瓣异常", exc_info=True)

        # 4.2 AI 筛选 RSS 条目
        if ai_cfg.get("filter_rss") and rss_entries:
            try:
                rss_entries = filter_rss_entries(rss_entries, ai_cfg)
            except Exception:
                logger.error("AI 筛选 RSS 异常", exc_info=True)

    # 5. 历史去重，只保留本周首次出现的内容进入最终排序
    books = mark_new_items(score_books(books, int(rules_cfg["target_year"])), state, "books")
    rss_entries = mark_new_items(score_rss(rss_entries), state, "rss")

    books = [item for item in books if item.get("is_new")]
    rss_entries = [item for item in rss_entries if item.get("is_new")]

    stats["final_books"] = len(books)
    stats["final_rss"] = len(rss_entries)

    save_state(state, int(cfg.get("state", {}).get("max_entries_per_bucket", 2000)))

    if not books and not rss_entries:
        logger.warning("没有发现新的书籍或资讯，跳过报告生成")
        return

    report_cfg = dict(cfg.get("report", {}))
    report_cfg.update(
        {
            "top_books": rules_cfg.get("top_books", 12),
            "top_rss": rules_cfg.get("top_rss", 10),
            "full_books": rules_cfg.get("full_books", 30),
            "full_rss": rules_cfg.get("full_rss", 20),
        }
    )
    report_cfg["excerpt_ai"] = {
        "enabled": bool(ai_cfg.get("enabled")),
        "providers": ai_cfg.get("providers", []),
        "provider": ai_cfg.get("provider", "openrouter"),
        "api_key": ai_cfg.get("api_key", ""),
        "base_url": ai_cfg.get("base_url", ""),
        "model": ai_cfg.get("model", ""),
        "fail_closed": False,
    }
    report_cfg["weekly_summary"] = summarize_weekly_selection(books, rss_entries, ai_cfg)

    # 6. 生成报告
    md_path, html_path = generate_report(
        books, rss_entries, report_cfg, PROJECT_ROOT, stats
    )

    logger.info("===== 周报生成完毕 =====")
    logger.info("  Markdown: %s", md_path)
    logger.info("  HTML:     %s", html_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="推理资讯周报自动抓取工具")
    parser.add_argument(
        "--once", action="store_true", help="立即执行一次并退出（不进入定时模式）"
    )
    args = parser.parse_args()

    cfg = load_config()

    if args.once:
        run_once(cfg)
        return

    # 定时模式
    sched_cfg = cfg.get("schedule", {})
    day = sched_cfg.get("day", "friday")
    run_time = sched_cfg.get("time", "18:00")

    job = getattr(schedule.every(), day)
    job.at(run_time).do(run_once, cfg)

    logger.info(
        "定时模式启动：每周%s %s 自动执行。按 Ctrl+C 退出。",
        day, run_time,
    )

    # 立即执行一次，然后进入循环
    run_once(cfg)

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("用户中断，退出。")
        sys.exit(0)


if __name__ == "__main__":
    main()
