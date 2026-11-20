"""P1 回归测试：新鲜度加分 + 豆列时间窗口。

对应缺陷：
1. 打分公式 `max(0, 8 - abs(target_year - year) * 4)` 对所有同年度图书恒等于 8 分，
   等于完全没有区分「本月出版」和「半年前出版」，新书永远被高口碑老书挤掉；
2. 豆瓣豆列复用资讯的 `days` 窗口筛「收录时间」，而豆列收录普遍早于出版，
   导致待出版的当月新书整批被筛掉。
"""

from datetime import datetime, timezone

import app_helpers
from scrapers import china_sources


# --- 缺陷 1：新鲜度加分 ---------------------------------------------------


def _rules(**overrides):
    rules = {"freshness_bonus_max": 24, "freshness_window_months": 6}
    rules.update(overrides)
    return rules


def test_freshness_bonus_rewards_current_month():
    now = datetime(2026, 11, 19)
    assert app_helpers._freshness_bonus("2026-11", _rules(), now) == 24.0


def test_freshness_bonus_treats_upcoming_books_as_fresh():
    """预售/待出版（出版月在未来）应拿满额加分，这正是周报最想推的。"""
    now = datetime(2026, 11, 19)
    assert app_helpers._freshness_bonus("2026-12", _rules(), now) == 24.0
    assert app_helpers._freshness_bonus("2027-03", _rules(), now) == 24.0


def test_freshness_bonus_decays_with_age():
    now = datetime(2026, 11, 19)
    rules = _rules()

    current = app_helpers._freshness_bonus("2026-11", rules, now)
    one_month = app_helpers._freshness_bonus("2026-10", rules, now)
    three_months = app_helpers._freshness_bonus("2026-08", rules, now)

    assert current > one_month > three_months >= 0


def test_freshness_bonus_reaches_zero_at_window_edge():
    now = datetime(2026, 11, 19)
    assert app_helpers._freshness_bonus("2026-05", _rules(), now) == 0.0
    assert app_helpers._freshness_bonus("2026-01", _rules(), now) == 0.0


def test_freshness_bonus_ignores_year_only_label():
    """只识别到年份时无法判断新旧，应返回 0 而不是当作 1 月误扣分。"""
    now = datetime(2026, 11, 19)
    assert app_helpers._freshness_bonus("2026", _rules(), now) == 0.0
    assert app_helpers._freshness_bonus("", _rules(), now) == 0.0


def test_freshness_bonus_can_be_disabled():
    now = datetime(2026, 11, 19)
    assert app_helpers._freshness_bonus("2026-11", _rules(freshness_bonus_max=0), now) == 0.0


def test_score_books_freshness_breaks_ties_between_similar_books():
    """新鲜度用于在「评分接近」时把新书顶上去，而不是让低分新书无脑压高分老书。"""
    books = [
        {
            "source": "douban",
            "id": "old",
            "title": "半年前的旧书",
            "year": "2026-05",
            "rating": 8.6,
            "rating_count": 2500,
        },
        {
            "source": "douban",
            "id": "fresh",
            "title": "本月新书",
            "year": "2026-11",
            "rating": 8.6,
            "rating_count": 2500,
        },
    ]

    scored = app_helpers.score_books(books, 2026, _rules())

    assert [book["id"] for book in scored] == ["fresh", "old"]
    assert scored[0]["freshness_score"] == 24.0
    assert scored[1]["freshness_score"] == 0.0


def test_score_books_old_high_rating_still_beats_weak_fresh_book():
    """老书评分明显更高时仍应胜出，新鲜度不能盖过口碑差距。"""
    books = [
        {
            "source": "douban",
            "id": "old",
            "title": "半年前的高口碑书",
            "year": "2026-05",
            "rating": 8.6,
            "rating_count": 2500,
        },
        {
            "source": "douban",
            "id": "fresh",
            "title": "本月低分新书",
            "year": "2026-11",
            "rating": 7.4,
            "rating_count": 120,
        },
    ]

    scored = app_helpers.score_books(books, 2026, _rules())

    assert [book["id"] for book in scored] == ["old", "fresh"]


def test_score_books_without_rules_keeps_backward_compatible_order():
    """不传 rules_cfg 时不应加分，保证既有调用方的排序不突变。"""
    books = [
        {"source": "douban", "id": "a", "title": "A", "year": "2026-11", "rating": 7.0, "rating_count": 10},
        {"source": "douban", "id": "b", "title": "B", "year": "2026-11", "rating": 9.0, "rating_count": 10},
    ]

    scored = app_helpers.score_books(books, 2026)

    assert [book["id"] for book in scored] == ["b", "a"]
    assert scored[0]["freshness_score"] == 0.0


def test_score_books_keeps_pending_china_books_ahead_of_regular():
    """豆列待出版条目（无评分）应排在常规条目之前，作为新书补充。"""
    books = [
        {
            "source": "douban",
            "id": "regular",
            "title": "常规条目",
            "year": "2026-11",
            "rating": 9.0,
            "rating_count": 500,
        },
        {
            "source": "china_book:豆列",
            "id": "pending",
            "title": "待出版",
            "year": "2026-12",
            "rating": 0,
            "rating_count": 0,
        },
    ]

    scored = app_helpers.score_books(books, 2026, _rules())

    assert [book["id"] for book in scored] == ["pending", "regular"]


# --- 缺陷 2：豆列时间窗口 ------------------------------------------------


def test_doulist_uses_book_days_instead_of_days():
    """豆列按自己的 book_days 放宽窗口，不受资讯的 days 限制。"""
    now = datetime.now(timezone.utc)
    cutoff = now.replace()  # 资讯窗口（21 天）会筛掉这些条目

    item = {
        "name": "测试豆列",
        "url": "https://example.test/doulist/1/",
        "max_pages": 1,
        "book_days": 180,
    }
    captured = {}

    def fake_request_html(url, timeout):
        return (
            '<div class="doulist-item">'
            '<div class="title"><a href="https://book.douban.com/subject/123/">测试书</a></div>'
            '<div class="abstract">作者: 某人 出版社: 某社 出版时间: 2026-12</div>'
            '<time class="time">2026-08-01 10:00:00</time>'
            "</div>"
        )

    original = china_sources._request_html
    china_sources._request_html = fake_request_html
    try:
        books = china_sources._fetch_douban_doulist_books(item, cutoff, 20, 2026)
    finally:
        china_sources._request_html = original

    captured["count"] = len(books)
    assert captured["count"] == 1, "book_days=180 时应保留 8 月收录、12 月出版的条目"
    assert books[0]["year"] == "2026-12"


def test_doulist_book_days_zero_disables_recency_filter():
    item = {
        "name": "测试豆列",
        "url": "https://example.test/doulist/1/",
        "max_pages": 1,
        "book_days": 0,
    }
    cutoff = datetime.now(timezone.utc)

    def fake_request_html(url, timeout):
        return (
            '<div class="doulist-item">'
            '<div class="title"><a href="https://book.douban.com/subject/456/">很早收录的书</a></div>'
            '<div class="abstract">作者: 某人 出版社: 某社 出版时间: 2026-10</div>'
            '<time class="time">2020-01-01 10:00:00</time>'
            "</div>"
        )

    original = china_sources._request_html
    china_sources._request_html = fake_request_html
    try:
        books = china_sources._fetch_douban_doulist_books(item, cutoff, 20, 2026)
    finally:
        china_sources._request_html = original

    assert len(books) == 1
    assert books[0]["year"] == "2026-10"
