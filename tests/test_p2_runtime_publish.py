import json
from pathlib import Path

import pytest

import main
import publish_pages


def test_run_lock_prevents_second_acquire_and_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "LOCK_STALE_SECONDS", 999999)

    first_lock = main._acquire_run_lock(str(tmp_path), is_test=True)
    second_lock = main._acquire_run_lock(str(tmp_path), is_test=True)

    assert first_lock is not None
    assert second_lock is None
    assert Path(first_lock).exists()

    main._release_run_lock(first_lock)
    assert not Path(first_lock).exists()


def test_record_run_status_writes_isolated_test_file(tmp_path):
    main._record_run_status(
        str(tmp_path),
        {"status": "success", "stats": {"final_books": 1}},
        is_test=True,
    )

    status_path = tmp_path / "data" / "runtime" / "last_run_test.json"
    data = json.loads(status_path.read_text(encoding="utf-8"))

    assert data["status"] == "success"
    assert data["stats"]["final_books"] == 1
    assert data["updated_at"]


def test_run_once_skips_when_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECT_ROOT", str(tmp_path))
    lock_path = main._acquire_run_lock(str(tmp_path), is_test=True)
    assert lock_path is not None

    main.run_once({"filter_rules": {"target_year": 2026}}, is_test=True)

    status_path = tmp_path / "data" / "runtime" / "last_run_test.json"
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["status"] == "skipped_locked"

    main._release_run_lock(lock_path)


def test_publish_plan_validates_latest_report(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    latest = output_dir / "推理资讯周报_2026-05-28.html"
    latest.write_text("<html><body>ok</body></html>", encoding="utf-8")

    plan = publish_pages._build_publish_plan(
        tmp_path,
        output_dir,
        tmp_path,
        "reports",
        latest_html=str(latest),
    )

    assert plan["latest_report"] == latest
    assert plan["archive_count"] == 1
    assert plan["site_index"] == tmp_path / "index.html"


def test_publish_plan_rejects_incomplete_html(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    latest = output_dir / "推理资讯周报_2026-05-28.html"
    latest.write_text("<html><body>incomplete", encoding="utf-8")

    with pytest.raises(ValueError, match="complete html"):
        publish_pages._build_publish_plan(
            tmp_path,
            output_dir,
            tmp_path,
            "reports",
            latest_html=str(latest),
        )


def test_publish_dry_run_does_not_write_site_files(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    latest = output_dir / "推理资讯周报_2026-05-28.html"
    latest.write_text("<html><body>ok</body></html>", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "publish_pages.py",
            "--project-root",
            str(tmp_path),
            "--output-dir",
            "output",
            "--site-dir",
            ".",
            "--dry-run",
        ],
    )

    assert publish_pages.main() == 0
    assert not (tmp_path / "index.html").exists()
    assert not (tmp_path / "reports").exists()
