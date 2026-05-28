from pathlib import Path

import report


def test_report_templates_are_loaded_from_template_directory():
    template_dir = Path(report.TEMPLATE_DIR)

    assert (template_dir / "report.md.j2").exists()
    assert (template_dir / "report.html.j2").exists()
    assert report.MD_TEMPLATE.name == "report.md.j2"
    assert report.HTML_TEMPLATE.name == "report.html.j2"


def test_generate_report_renders_markdown_and_html_from_templates(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_cache_cover_image", lambda cover_url, output_dir, book_id: "")
    monkeypatch.setattr(report, "_attach_full_rss_excerpts", lambda entries, ai_cfg: None)

    books = [
        {
            "id": "book-1",
            "title": "Test Mystery",
            "subtitle": "A Case",
            "author": ["Author"],
            "press": ["Press"],
            "year": "2026",
            "rating": 8.5,
            "rating_count": 12,
            "score": 99.5,
            "url": "https://example.test/book",
            "cover_url": "",
            "is_new": True,
            "ai_recommend": "Worth reading.",
            "abstract": "A locked-room mystery.",
        }
    ]
    entries = [
        {
            "id": "rss-1",
            "title": "Mystery news",
            "source": "rss:test",
            "url": "https://example.test/news",
            "published": "2026-05-28T00:00:00+00:00",
            "summary": "detective publishing update",
            "is_new": True,
            "ai_summary": "Short summary.",
        }
    ]
    cfg = {
        "output_dir": "out",
        "title_prefix": "Weekly",
        "top_books": 1,
        "top_rss": 1,
        "full_books": 1,
        "full_rss": 1,
        "weekly_summary": "Summary line.",
        "excerpt_ai": {"enabled": False},
    }
    stats = {
        "raw_books": 1,
        "filtered_books": 1,
        "final_books": 1,
        "raw_rss": 1,
        "filtered_rss": 1,
        "final_rss": 1,
    }

    md_path, html_path = report.generate_report(books, entries, cfg, str(tmp_path), stats)

    md_text = Path(md_path).read_text(encoding="utf-8")
    html_text = Path(html_path).read_text(encoding="utf-8")

    assert Path(md_path).exists()
    assert Path(html_path).exists()
    assert "Test Mystery" in md_text
    assert "Mystery news" in md_text
    assert "<!DOCTYPE html>" in html_text
    assert "Summary line." in html_text
