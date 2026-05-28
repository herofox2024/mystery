"""推理资讯周报 — 主入口

用法:
    python main.py           # 启动定时模式，每周五自动执行
    python main.py --once    # 立即执行一次并退出
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

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
LOCK_STALE_SECONDS = 6 * 60 * 60
RUNTIME_LOG_NAME = "weekly_report.log"


def _prepare_ai_config(ai_cfg: dict, rules_cfg: dict, is_test: bool = False) -> dict:
    prepared = dict(ai_cfg)
    prepared["target_year"] = rules_cfg["target_year"]
    prepared.setdefault("_ai_filter_stats", {})
    if is_test and prepared.get("fail_closed", True):
        prepared["fail_closed"] = False
        logger.info("Test mode uses AI fail-open: keeping rule-filtered candidates when AI is unavailable")
    return prepared


def _log_ai_filter_summary(ai_cfg: dict, before_books: int, after_books: int, before_rss: int, after_rss: int) -> None:
    stats = ai_cfg.get("_ai_filter_stats", {})
    logger.info(
        "AI filter summary: books %d -> %d, rss %d -> %d, providers=%s/%s, calls ok=%s failed=%s unavailable=%s, dropped=%s, pass_through=%s, last_failure=%s",
        before_books,
        after_books,
        before_rss,
        after_rss,
        stats.get("available_provider_count", 0),
        stats.get("provider_count", 0),
        stats.get("successful_calls", 0),
        stats.get("failed_calls", 0),
        stats.get("unavailable_calls", 0),
        stats.get("dropped_items", 0),
        stats.get("passed_through_items", 0),
        stats.get("last_failure", ""),
    )


def _build_state_config(cfg: dict, is_test: bool = False) -> dict:
    state_cfg = dict(cfg.get("state", {}))
    if is_test:
        base_state_path = str(state_cfg.get("path", "data/state.json"))
        if base_state_path.lower().endswith(".json"):
            state_cfg["path"] = base_state_path[:-5] + "_test.json"
        else:
            state_cfg["path"] = base_state_path + "_test"
    return state_cfg


def _runtime_dir(project_root: str) -> str:
    path = os.path.join(project_root, "data", "runtime")
    os.makedirs(path, exist_ok=True)
    return path


def _runtime_path(project_root: str, name: str, is_test: bool = False) -> str:
    stem, ext = os.path.splitext(name)
    file_name = f"{stem}_test{ext}" if is_test else name
    return os.path.join(_runtime_dir(project_root), file_name)


def ensure_runtime_logging(project_root: str = PROJECT_ROOT) -> str:
    """Attach a runtime file logger once so the local web console can read logs."""
    log_path = _runtime_path(project_root, RUNTIME_LOG_NAME)
    abs_log_path = os.path.abspath(log_path)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == abs_log_path:
            return log_path

    file_handler = logging.FileHandler(abs_log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)
    return log_path


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_file(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _is_lock_stale(path: str, stale_seconds: int = LOCK_STALE_SECONDS) -> bool:
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    return age > stale_seconds


def _acquire_run_lock(project_root: str, is_test: bool = False) -> str | None:
    lock_path = _runtime_path(project_root, "weekly_report.lock", is_test=is_test)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
    except FileExistsError:
        if _is_lock_stale(lock_path):
            logger.warning("Removing stale run lock: %s", lock_path)
            try:
                os.remove(lock_path)
            except OSError:
                return None
            return _acquire_run_lock(project_root, is_test=is_test)
        return None

    payload = {
        "pid": os.getpid(),
        "started_at": _now_utc_iso(),
        "is_test": is_test,
    }
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return lock_path


def _release_run_lock(lock_path: str | None) -> None:
    if not lock_path:
        return
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Failed to remove run lock: %s", lock_path, exc_info=True)


def _record_run_status(project_root: str, payload: dict, is_test: bool = False) -> None:
    status_path = _runtime_path(project_root, "last_run.json", is_test=is_test)
    current = _read_json_file(status_path)
    current.update(payload)
    current["updated_at"] = _now_utc_iso()
    _write_json_file(status_path, current)


def _record_run_phase(
    project_root: str,
    phase: str,
    message: str,
    *,
    status: str = "running",
    stats: dict | None = None,
    is_test: bool = False,
    extra: dict | None = None,
) -> None:
    payload = {
        "status": status,
        "phase": phase,
        "message": message,
        "is_test": is_test,
    }
    if stats is not None:
        payload["stats"] = dict(stats)
    if extra:
        payload.update(extra)
    _record_run_status(project_root, payload, is_test=is_test)


def _initial_stats() -> dict:
    return {
        "raw_books": 0,
        "filtered_books": 0,
        "final_books": 0,
        "raw_rss": 0,
        "filtered_rss": 0,
        "final_rss": 0,
    }


def _fetch_source_items(cfg: dict, rules_cfg: dict) -> tuple[list[dict], list[dict], dict]:
    stats = _initial_stats()
    books: list[dict] = []
    rss_entries: list[dict] = []

    try:
        books = fetch_douban_books(cfg.get("douban", {}))
    except Exception:
        logger.error("Douban fetch failed", exc_info=True)

    try:
        china_cfg = dict(cfg.get("china_sources", {}))
        china_cfg.setdefault("target_year", rules_cfg.get("target_year"))
        books.extend(fetch_china_book_entries(china_cfg))
    except Exception:
        logger.error("China book source fetch failed", exc_info=True)

    try:
        rss_entries = fetch_rss_entries(cfg.get("rss", {}))
    except Exception:
        logger.error("RSS fetch failed", exc_info=True)

    try:
        china_cfg = dict(cfg.get("china_sources", {}))
        china_cfg.setdefault("target_year", rules_cfg.get("target_year"))
        rss_entries.extend(fetch_china_entries(china_cfg))
    except Exception:
        logger.error("China news source fetch failed", exc_info=True)

    stats["raw_books"] = len(books)
    stats["raw_rss"] = len(rss_entries)
    return books, rss_entries, stats


def _apply_rule_filters(books: list[dict], rss_entries: list[dict], rules_cfg: dict, stats: dict) -> tuple[list[dict], list[dict]]:
    target_year = int(rules_cfg["target_year"])
    filtered_books = score_books(filter_books_by_rules(books, rules_cfg), target_year)
    filtered_rss = score_rss(filter_rss_by_rules(rss_entries, rules_cfg))

    stats["filtered_books"] = len(filtered_books)
    stats["filtered_rss"] = len(filtered_rss)

    return (
        filtered_books[: rules_cfg.get("max_books_before_ai", 50)],
        filtered_rss[: rules_cfg.get("max_rss_before_ai", 30)],
    )


def _apply_ai_filters(books: list[dict], rss_entries: list[dict], ai_cfg: dict, rules_cfg: dict, is_test: bool = False) -> tuple[list[dict], list[dict], dict]:
    if not ai_cfg.get("enabled"):
        return books, rss_entries, ai_cfg

    prepared_ai_cfg = _prepare_ai_config(ai_cfg, rules_cfg, is_test=is_test)
    before_ai_books = len(books)
    before_ai_rss = len(rss_entries)

    if prepared_ai_cfg.get("filter_douban") and books:
        try:
            books = filter_douban_books(books, prepared_ai_cfg)
        except Exception:
            logger.error("AI book filtering failed", exc_info=True)

    if prepared_ai_cfg.get("filter_rss") and rss_entries:
        try:
            rss_entries = filter_rss_entries(rss_entries, prepared_ai_cfg)
        except Exception:
            logger.error("AI RSS filtering failed", exc_info=True)

    _log_ai_filter_summary(prepared_ai_cfg, before_ai_books, len(books), before_ai_rss, len(rss_entries))
    return books, rss_entries, prepared_ai_cfg


def _mark_final_items(books: list[dict], rss_entries: list[dict], rules_cfg: dict, state: dict, stats: dict) -> tuple[list[dict], list[dict]]:
    target_year = int(rules_cfg["target_year"])
    marked_books = mark_new_items(score_books(books, target_year), state, "books")
    marked_rss = mark_new_items(score_rss(rss_entries), state, "rss")

    final_books = [item for item in marked_books if item.get("is_new")]
    final_rss = [item for item in marked_rss if item.get("is_new")]

    stats["final_books"] = len(final_books)
    stats["final_rss"] = len(final_rss)
    return final_books, final_rss


def _build_report_config(cfg: dict, rules_cfg: dict, ai_cfg: dict, books: list[dict], rss_entries: list[dict], is_test: bool = False) -> dict:
    report_cfg = dict(cfg.get("report", {}))
    if is_test:
        report_cfg["output_dir"] = str(report_cfg.get("output_dir", "output")) + "_test"
        report_cfg["title_prefix"] = str(report_cfg.get("title_prefix", "推理资讯周报")) + "_测试"
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
    return report_cfg


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



def run_once(cfg: dict, is_test: bool = False) -> None:
    """Run one complete fetch/filter/report cycle."""
    ensure_runtime_logging(PROJECT_ROOT)
    lock_path = _acquire_run_lock(PROJECT_ROOT, is_test=is_test)
    if not lock_path:
        logger.warning("Another weekly report run is already in progress; skipped this run")
        _record_run_status(
            PROJECT_ROOT,
            {
                "status": "skipped_locked",
                "phase": "locked",
                "message": "Another weekly report run is already in progress.",
                "is_test": is_test,
                "completed_at": _now_utc_iso(),
            },
            is_test=is_test,
        )
        return

    logger.info("===== Start weekly report generation =====")
    started_at = _now_utc_iso()
    _record_run_phase(
        PROJECT_ROOT,
        "starting",
        "Starting weekly report generation.",
        is_test=is_test,
        extra={"started_at": started_at, "completed_at": ""},
    )
    try:
        rules_cfg = cfg.get("filter_rules", {})
        _record_run_phase(PROJECT_ROOT, "loading_state", "Loading state and configuration.", is_test=is_test)
        state_cfg = _build_state_config(cfg, is_test=is_test)
        state = load_state(PROJECT_ROOT, state_cfg)

        _record_run_phase(PROJECT_ROOT, "fetching_sources", "Fetching source items.", is_test=is_test)
        books, rss_entries, stats = _fetch_source_items(cfg, rules_cfg)
        _record_run_phase(PROJECT_ROOT, "rule_filtering", "Applying rule filters.", stats=stats, is_test=is_test)
        books, rss_entries = _apply_rule_filters(books, rss_entries, rules_cfg, stats)
        _record_run_phase(PROJECT_ROOT, "ai_filtering", "Applying AI filters.", stats=stats, is_test=is_test)
        books, rss_entries, ai_cfg = _apply_ai_filters(
            books,
            rss_entries,
            cfg.get("ai_filter", {}),
            rules_cfg,
            is_test=is_test,
        )
        ai_stats = dict(ai_cfg.get("_ai_filter_stats", {})) if isinstance(ai_cfg, dict) else {}
        _record_run_phase(
            PROJECT_ROOT,
            "deduping",
            "Checking historical state for new items.",
            stats=stats,
            is_test=is_test,
            extra={"ai_filter_stats": ai_stats},
        )
        books, rss_entries = _mark_final_items(books, rss_entries, rules_cfg, state, stats)

        if not books and not rss_entries:
            _record_run_phase(PROJECT_ROOT, "saving_state", "Saving state without report output.", stats=stats, is_test=is_test)
            save_state(state, int(state_cfg.get("max_entries_per_bucket", 2000)))
            logger.warning("No new books or entries found; skipped report generation")
            _record_run_status(
                PROJECT_ROOT,
                {
                    "status": "no_new_items",
                    "phase": "completed",
                    "message": "No new books or entries found; skipped report generation.",
                    "is_test": is_test,
                    "stats": stats,
                    "ai_filter_stats": ai_stats,
                    "started_at": started_at,
                    "completed_at": _now_utc_iso(),
                },
                is_test=is_test,
            )
            return

        _record_run_phase(PROJECT_ROOT, "generating_report", "Generating Markdown and HTML report.", stats=stats, is_test=is_test)
        report_cfg = _build_report_config(
            cfg,
            rules_cfg,
            ai_cfg,
            books,
            rss_entries,
            is_test=is_test,
        )
        md_path, html_path = generate_report(
            books,
            rss_entries,
            report_cfg,
            PROJECT_ROOT,
            stats,
        )
        _record_run_phase(PROJECT_ROOT, "saving_state", "Saving state after report generation.", stats=stats, is_test=is_test)
        save_state(state, int(state_cfg.get("max_entries_per_bucket", 2000)))
        _record_run_status(
            PROJECT_ROOT,
            {
                "status": "success",
                "phase": "completed",
                "message": "Weekly report generation finished.",
                "is_test": is_test,
                "stats": stats,
                "ai_filter_stats": ai_stats,
                "markdown": md_path,
                "html": html_path,
                "started_at": started_at,
                "completed_at": _now_utc_iso(),
            },
            is_test=is_test,
        )

        logger.info("===== Weekly report generation finished =====")
        logger.info("  Markdown: %s", md_path)
        logger.info("  HTML:     %s", html_path)
    except Exception as exc:
        _record_run_status(
            PROJECT_ROOT,
            {
                "status": "failed",
                "phase": "failed",
                "message": "Weekly report generation failed.",
                "is_test": is_test,
                "error": str(exc),
                "started_at": started_at,
                "completed_at": _now_utc_iso(),
            },
            is_test=is_test,
        )
        raise
    finally:
        _release_run_lock(lock_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="推理资讯周报自动抓取工具")
    parser.add_argument(
        "--once", action="store_true", help="立即执行一次并退出（不进入定时模式）"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="测试模式：输出到独立目录并标记测试文件名，不与正式周报混用",
    )
    args = parser.parse_args()

    cfg = load_config()

    if args.once:
        run_once(cfg, is_test=args.test)
        return

    # 定时模式
    sched_cfg = cfg.get("schedule", {})
    day = sched_cfg.get("day", "friday")
    run_time = sched_cfg.get("time", "18:00")

    job = getattr(schedule.every(), day)
    job.at(run_time).do(run_once, cfg, args.test)

    logger.info(
        "定时模式启动：每周%s %s 自动执行。按 Ctrl+C 退出。",
        day, run_time,
    )

    # 立即执行一次，然后进入循环
    run_once(cfg, is_test=args.test)

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("用户中断，退出。")
        sys.exit(0)


if __name__ == "__main__":
    main()
