"""P0 回归测试：豆列出版时间解析 + 出版年月贯穿报告。

对应缺陷：
1. 豆瓣豆列把出版信息写成「出版时间」而非「出版年」，旧正则整批丢条目；
2. 出版年月在规则过滤阶段被压成 4 位年份，导致「本月新书」和
   「几个月前出版的补录书」在报告里无法区分。
"""

import app_helpers
import report as report_module
from scrapers.china_sources import (
    _extract_book_year,
    _extract_field_list,
    _extract_pub_date,
)


# --- 缺陷 1：豆列出版信息解析 ---------------------------------------------


def test_extract_pub_date_accepts_publication_time_label():
    """新旧两种标签都必须能解析，这是豆列条目能否入池的开关。"""
    assert _extract_pub_date("作者: [美]香农·柯克 出版社: 深圳出版社 出版时间: 2026-12") == "2026-12"
    assert _extract_pub_date("作者: 夏桑 出版社: 新星出版社 出版年: 2026-11-1") == "2026-11"


def test_extract_pub_date_keeps_month_and_normalizes_padding():
    assert _extract_pub_date("出版社: 新星出版社 出版年: 2026-7") == "2026-07"
    assert _extract_pub_date("出版社: 某社 出版日期: 2026/3") == "2026-03"
    assert _extract_pub_date("出版社: 某社 出版年: 2026年5月") == "2026-05"


def test_extract_pub_date_falls_back_to_year_only():
    assert _extract_pub_date("出版社: 某社 出版年: 2026") == "2026"


def test_extract_pub_date_returns_empty_without_publication_info():
    assert _extract_pub_date("作者: 王五 出版社: 某社") == ""
    assert _extract_pub_date("") == ""


def test_extract_book_year_matches_target_year():
    """target_year 过滤器依赖这个函数，必须能识别「出版时间」写法。"""
    assert _extract_book_year("出版社: 深圳出版社 出版时间: 2026-12") == 2026
    assert _extract_book_year("出版社: 某社 出版时间: 2016-01") == 2016
    assert _extract_book_year("作者: 王五 出版社: 某社") is None


def test_extract_field_list_does_not_swallow_publication_time():
    """出版社字段曾被「出版时间: 2026-12」污染。"""
    text = "作者: [美]香农·柯克 出版社: 深圳出版社 出版时间: 2026-12"
    assert _extract_field_list(text, "出版社") == ["深圳出版社"]
    assert _extract_field_list(text, "作者") == ["[美]香农·柯克"]


# --- 缺陷 2：出版年月贯穿链路 ---------------------------------------------


def test_normalize_year_label_preserves_month():
    assert app_helpers._normalize_year_label("2026-11") == "2026-11"
    assert app_helpers._normalize_year_label("2026-9") == "2026-09"
    assert app_helpers._normalize_year_label("2026/3") == "2026-03"
    assert app_helpers._normalize_year_label("2026年5月") == "2026-05"
    assert app_helpers._normalize_year_label("2026") == "2026"
    assert app_helpers._normalize_year_label("") == ""


def test_filter_books_by_rules_keeps_month_instead_of_truncating():
    rules = {
        "target_year": 2026,
        "min_rating": 0,
        "min_rating_count": 0,
        "exact_target_year_only": True,
        "recent_months_window": 12,
        "exclude_keywords": [],
    }
    books = [
        {"source": "douban", "id": "a", "title": "本月新书", "year": "2026-11", "rating": 8, "rating_count": 10},
        {"source": "douban", "id": "b", "title": "半年前", "year": "2026-5", "rating": 8, "rating_count": 10},
    ]

    kept = app_helpers.filter_books_by_rules(books, rules)

    assert [book["year"] for book in kept] == ["2026-11", "2026-05"]


def test_format_year_label_renders_month():
    assert report_module._format_year_label("2026-11") == "2026年11月"
    assert report_module._format_year_label("2026") == "2026年"
    assert report_module._format_year_label("") == "未知年份"


def test_report_renders_publication_month_for_two_books(tmp_path, monkeypatch):
    """两个不同月份的书，在 Markdown 里必须显示成不同的时间。"""
    monkeypatch.setattr(report_module, "_cache_cover_image", lambda cover_url, output_dir, book_id: "")
    monkeypatch.setattr(report_module, "_attach_full_rss_excerpts", lambda entries, ai_cfg: None)

    books = [
        {
            "id": "nov",
            "title": "本月新书",
            "author": ["A"],
            "press": ["P"],
            "year": "2026-11",
            "rating": 8.0,
            "rating_count": 20,
            "score": 90.0,
            "url": "https://example.test/nov",
            "is_new": True,
        },
        {
            "id": "jul",
            "title": "七月旧书",
            "author": ["B"],
            "press": ["P"],
            "year": "2026-07",
            "rating": 8.0,
            "rating_count": 20,
            "score": 80.0,
            "url": "https://example.test/jul",
            "is_new": False,
        },
    ]
    cfg = {
        "output_dir": "out",
        "title_prefix": "Weekly",
        "top_books": 2,
        "top_rss": 0,
        "full_books": 2,
        "full_rss": 0,
        "weekly_summary": "",
        "excerpt_ai": {"enabled": False},
    }

    md_path, _ = report_module.generate_report(books, [], cfg, str(tmp_path), {})
    md_text = open(md_path, encoding="utf-8").read()

    assert "2026年11月" in md_text
    assert "2026年7月" in md_text
    assert "出版时间" in md_text
