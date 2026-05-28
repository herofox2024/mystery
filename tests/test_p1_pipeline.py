import main


def _rules():
    return {
        "target_year": 2026,
        "min_rating": 0,
        "min_rating_count": 0,
        "exact_target_year_only": True,
        "recent_months_window": 12,
        "exclude_keywords": ["essay"],
        "rss_include_keywords": ["mystery", "detective"],
        "rss_exclude_keywords": ["romance"],
        "max_books_before_ai": 1,
        "max_rss_before_ai": 1,
        "top_books": 2,
        "top_rss": 3,
        "full_books": 4,
        "full_rss": 5,
    }


def test_build_state_config_uses_isolated_test_path():
    cfg = {"state": {"path": "data/state.json", "max_entries_per_bucket": 10}}

    assert main._build_state_config(cfg, is_test=False)["path"] == "data/state.json"
    assert main._build_state_config(cfg, is_test=True)["path"] == "data/state_test.json"


def test_apply_rule_filters_scores_and_limits_candidates():
    stats = main._initial_stats()
    books = [
        {
            "source": "douban",
            "id": "book-high",
            "title": "High Mystery",
            "subtitle": "",
            "author": [],
            "press": [],
            "year": "2026",
            "rating": 9.0,
            "rating_count": 100,
        },
        {
            "source": "douban",
            "id": "book-low",
            "title": "Low Mystery",
            "subtitle": "",
            "author": [],
            "press": [],
            "year": "2026",
            "rating": 1.0,
            "rating_count": 1,
        },
    ]
    entries = [
        {
            "id": "rss-1",
            "title": "Mystery publishing update",
            "summary": "detective news",
            "source": "rss:test",
        },
        {
            "id": "rss-2",
            "title": "Romance publishing update",
            "summary": "not relevant",
            "source": "rss:test",
        },
    ]

    filtered_books, filtered_rss = main._apply_rule_filters(books, entries, _rules(), stats)

    assert [book["id"] for book in filtered_books] == ["book-high"]
    assert [entry["id"] for entry in filtered_rss] == ["rss-1"]
    assert stats["filtered_books"] == 2
    assert stats["filtered_rss"] == 1


def test_mark_final_items_excludes_seen_items_and_updates_stats():
    stats = main._initial_stats()
    state = {
        "books": {
            "seen-book": {
                "title": "Seen Book",
                "first_seen": "2026-01-01",
                "last_seen": "2026-01-01",
            }
        },
        "rss": {},
    }
    books = [
        {"id": "seen-book", "title": "Seen Book", "year": "2026", "rating": 8, "rating_count": 10},
        {"id": "new-book", "title": "New Book", "year": "2026", "rating": 7, "rating_count": 5},
    ]
    entries = [{"id": "new-rss", "title": "Mystery news", "summary": "detective"}]

    final_books, final_rss = main._mark_final_items(books, entries, _rules(), state, stats)

    assert [book["id"] for book in final_books] == ["new-book"]
    assert [entry["id"] for entry in final_rss] == ["new-rss"]
    assert stats["final_books"] == 1
    assert stats["final_rss"] == 1
    assert "new-book" in state["books"]
    assert "new-rss" in state["rss"]


def test_build_report_config_carries_limits_and_safe_excerpt_ai(monkeypatch):
    monkeypatch.setattr(main, "summarize_weekly_selection", lambda books, entries, cfg: "summary")
    cfg = {"report": {"output_dir": "output", "title_prefix": "Weekly"}}
    ai_cfg = {
        "enabled": True,
        "providers": [{"provider": "zhipu"}],
        "provider": "zhipu",
        "model": "GLM-4-Flash-250414",
        "fail_closed": True,
    }

    report_cfg = main._build_report_config(cfg, _rules(), ai_cfg, [], [], is_test=True)

    assert report_cfg["output_dir"] == "output_test"
    assert report_cfg["title_prefix"] == "Weekly_测试"
    assert report_cfg["top_books"] == 2
    assert report_cfg["full_rss"] == 5
    assert report_cfg["excerpt_ai"]["fail_closed"] is False
    assert report_cfg["excerpt_ai"]["provider"] == "zhipu"
    assert report_cfg["weekly_summary"] == "summary"
