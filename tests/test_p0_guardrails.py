import json

import main
from scrapers import ai_filter
from scrapers.ai_filter import filter_douban_books, filter_rss_entries


def test_ai_filter_fail_open_keeps_items_without_api_key(monkeypatch):
    for env_name in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "DOUBAO_API_KEY",
        "ZHIPUAI_API_KEY",
        "ZHIPU_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    ai_filter._disabled_provider_keys.clear()

    cfg = {
        "enabled": True,
        "filter_douban": True,
        "filter_rss": True,
        "fail_closed": False,
        "providers": [{"provider": "openrouter", "api_key": ""}],
        "batch_size": 4,
    }
    books = [
        {
            "id": "book-1",
            "title": "Test Mystery",
            "author": [],
            "press": [],
            "year": "2026",
            "rating": 0,
            "abstract": "",
        }
    ]
    entries = [
        {
            "id": "entry-1",
            "title": "Mystery news",
            "summary": "detective publishing update",
            "url": "https://example.test/news",
        }
    ]

    assert filter_douban_books(books, cfg) == books
    assert filter_rss_entries(entries, cfg) == entries

    stats = cfg["_ai_filter_stats"]
    assert stats["unavailable_calls"] == 2
    assert stats["passed_through_items"] == 2
    assert stats["dropped_items"] == 0
    assert stats["last_failure"] == "no_api_key"


def test_run_once_test_mode_uses_isolated_state_and_fail_open(tmp_path, monkeypatch):
    generated = {}

    monkeypatch.setattr(main, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        main,
        "fetch_douban_books",
        lambda cfg: [
            {
                "source": "douban",
                "id": "book-1",
                "title": "Test Mystery",
                "subtitle": "",
                "author": [],
                "press": [],
                "year": "2026",
                "rating": 0,
                "rating_count": 0,
                "cover_url": "",
                "url": "https://example.test/book",
                "abstract": "detective fiction",
            }
        ],
    )
    monkeypatch.setattr(main, "fetch_china_book_entries", lambda cfg: [])
    monkeypatch.setattr(
        main,
        "fetch_rss_entries",
        lambda cfg: [
            {
                "source": "rss:test",
                "id": "entry-1",
                "title": "Mystery publishing news",
                "url": "https://example.test/news",
                "published": "",
                "summary": "detective publishing update",
            }
        ],
    )
    monkeypatch.setattr(main, "fetch_china_entries", lambda cfg: [])

    def fake_generate_report(books, rss_entries, report_cfg, project_root, stats):
        generated["books"] = books
        generated["rss_entries"] = rss_entries
        generated["report_cfg"] = report_cfg
        generated["project_root"] = project_root
        generated["stats"] = stats
        return str(tmp_path / "report.md"), str(tmp_path / "report.html")

    monkeypatch.setattr(main, "generate_report", fake_generate_report)

    cfg = {
        "douban": {},
        "rss": {},
        "china_sources": {"enabled": False},
        "ai_filter": {
            "enabled": True,
            "filter_douban": True,
            "filter_rss": True,
            "fail_closed": True,
            "providers": [{"provider": "openrouter", "api_key": ""}],
            "batch_size": 8,
        },
        "filter_rules": {
            "target_year": 2026,
            "min_rating": 0,
            "min_rating_count": 0,
            "exact_target_year_only": True,
            "recent_months_window": 12,
            "exclude_keywords": [],
            "rss_include_keywords": ["mystery", "detective"],
            "rss_exclude_keywords": [],
            "max_books_before_ai": 50,
            "max_rss_before_ai": 30,
            "top_books": 12,
            "top_rss": 10,
            "full_books": 30,
            "full_rss": 20,
        },
        "report": {"output_dir": "output", "title_prefix": "Weekly"},
        "state": {"path": "data/state.json", "max_entries_per_bucket": 2000},
    }

    main.run_once(cfg, is_test=True)

    assert len(generated["books"]) == 1
    assert len(generated["rss_entries"]) == 1
    assert generated["report_cfg"]["output_dir"] == "output_test"
    assert generated["report_cfg"]["title_prefix"] == "Weekly_测试"
    assert (tmp_path / "data" / "state_test.json").exists()
    assert not (tmp_path / "data" / "state.json").exists()

    state = json.loads((tmp_path / "data" / "state_test.json").read_text(encoding="utf-8"))
    assert "book-1" in state["books"]
    assert "entry-1" in state["rss"]


def test_zhipu_provider_defaults_and_env_key(monkeypatch):
    monkeypatch.setenv("ZHIPUAI_API_KEY", "zhipu-key")

    pool = ai_filter._resolve_provider_pool({"providers": [{"provider": "zhipu"}]})

    assert pool == [
        {
            "provider": "zhipu",
            "api_key": "zhipu-key",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "GLM-4-Flash-250414",
            "headers": {},
            "timeout": 300.0,
            "max_retries": 1,
        }
    ]


def test_zhipu_provider_accepts_legacy_env_name(monkeypatch):
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "legacy-zhipu-key")

    pool = ai_filter._resolve_provider_pool({"providers": [{"provider": "zhipu"}]})

    assert pool[0]["api_key"] == "legacy-zhipu-key"
