import json
from pathlib import Path

import main
import scheduled_runner


def _setup_project(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled_runner, "PROJECT_ROOT", Path(tmp_path))
    monkeypatch.setattr(main, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(scheduled_runner.main, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(scheduled_runner.main, "ensure_runtime_logging", lambda project_root: str(tmp_path / "data" / "runtime" / "weekly_report.log"))
    monkeypatch.setattr(
        scheduled_runner.main,
        "load_config",
        lambda: {"report": {"output_dir": "output"}, "filter_rules": {"target_year": 2026}},
    )


def _schedule_status(tmp_path):
    return json.loads((tmp_path / "data" / "runtime" / "last_schedule.json").read_text(encoding="utf-8"))


def test_scheduled_run_and_publish_publishes_generated_html(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    report = tmp_path / "output" / "report.html"
    report.parent.mkdir()
    report.write_text("<html></html>", encoding="utf-8")

    def fake_run_once(cfg, is_test=False):
        main._record_run_status(
            str(tmp_path),
            {"status": "success", "html": str(report), "stats": {"final_books": 1}},
            is_test=False,
        )

    def fake_publish_latest(*args, **kwargs):
        return {"status": "success", "site_index": tmp_path / "index.html", "git_push": kwargs.get("git_push")}

    monkeypatch.setattr(scheduled_runner.main, "run_once", fake_run_once)
    monkeypatch.setattr(scheduled_runner.publish_pages, "publish_latest", fake_publish_latest)

    assert scheduled_runner.run_once(mode="run-and-publish", git_push=True) == 0
    status = _schedule_status(tmp_path)

    assert status["status"] == "success"
    assert status["run_status"]["status"] == "success"
    assert status["publish_result"]["site_index"].endswith("index.html")
    assert status["publish_result"]["git_push"] is True


def test_scheduled_run_and_publish_skips_when_no_new_report(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)

    def fake_run_once(cfg, is_test=False):
        main._record_run_status(
            str(tmp_path),
            {"status": "no_new_items", "stats": {"final_books": 0}},
            is_test=False,
        )

    monkeypatch.setattr(scheduled_runner.main, "run_once", fake_run_once)

    assert scheduled_runner.run_once(mode="run-and-publish") == 0
    status = _schedule_status(tmp_path)

    assert status["status"] == "skipped_no_new_report"
    assert status["phase"] == "skipped"


def test_scheduled_publish_mode_records_dry_run_result(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)

    def fake_publish_latest(*args, **kwargs):
        return {"status": "dry_run", "archive_count": 2, "dry_run": kwargs.get("dry_run")}

    monkeypatch.setattr(scheduled_runner.publish_pages, "publish_latest", fake_publish_latest)

    assert scheduled_runner.run_once(mode="publish", dry_run=True) == 0
    status = _schedule_status(tmp_path)

    assert status["status"] == "dry_run"
    assert status["publish_result"]["archive_count"] == 2
    assert status["publish_result"]["dry_run"] is True


def test_scheduled_runner_records_failure(tmp_path, monkeypatch):
    _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduled_runner.main, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("bad config")))

    assert scheduled_runner.run_once(mode="run") == 1
    status = _schedule_status(tmp_path)

    assert status["status"] == "failed"
    assert "bad config" in status["error"]
