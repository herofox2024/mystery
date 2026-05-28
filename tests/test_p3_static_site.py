import json
from pathlib import Path

import publish_pages


def _report_name(date_text="2026-05-28"):
    pattern = publish_pages.REPORT_RE.pattern
    prefix = pattern[1 : pattern.index("_(")]
    return f"{prefix}_{date_text}.html"


def _write_report(path: Path, title: str, books: int, rss: int):
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html>",
                "<head>",
                f"<title>{title}</title>",
                "</head>",
                "<body>",
                f"<div class=\"hero-value\">{books}</div>",
                f"<div class=\"hero-value\">{rss}</div>",
                '<img src="assets/c.jpg">',
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )


def test_archive_manifest_tracks_latest_alias_and_counts(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    older = reports_dir / _report_name("2026-05-21")
    newer = reports_dir / _report_name("2026-05-28")
    _write_report(older, "Older Weekly", 3, 1)
    _write_report(newer, "Newer Weekly", 7, 2)

    archives = publish_pages._collect_archives(reports_dir)
    manifest_path = publish_pages._write_archive_manifest(reports_dir, archives, reports_dir / "latest.html")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert data["latest"]["name"] == newer.name
    assert data["latest"]["url"] == "./latest.html"
    assert data["latest"]["selected_books"] == 7
    assert data["latest"]["selected_rss"] == 2
    assert [item["date"] for item in data["reports"]] == ["2026-05-28", "2026-05-21"]


def test_archive_index_links_latest_and_json_manifest(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / _report_name()
    _write_report(report, "Latest Weekly", 5, 4)

    index_path = publish_pages._write_archive_index(reports_dir, publish_pages._collect_archives(reports_dir))
    content = index_path.read_text(encoding="utf-8")

    assert "./latest.html" in content
    assert "./index.json" in content
    assert "Latest Weekly" in content
    assert "5 本书" in content
