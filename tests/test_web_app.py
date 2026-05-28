import json
from pathlib import Path

from fastapi.testclient import TestClient

import publish_pages
import web_app


def _report_name(date_text="2026-05-28"):
    pattern = publish_pages.REPORT_RE.pattern
    prefix = pattern[1 : pattern.index("_(")]
    return f"{prefix}_{date_text}.html"


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "PROJECT_ROOT", Path(tmp_path))
    monkeypatch.setattr(web_app.main, "PROJECT_ROOT", str(tmp_path))
    web_app._RUN_THREAD = None
    return TestClient(web_app.app)


def test_status_reads_last_run_file(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "last_run.json").write_text(
        json.dumps({"status": "success", "phase": "completed", "stats": {"final_books": 2}}),
        encoding="utf-8",
    )

    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["phase"] == "completed"
    assert data["stats"]["final_books"] == 2
    assert data["running"] is False


def test_reports_lists_latest_html(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    older = output_dir / "report_2026-05-20.html"
    newer = output_dir / "report_2026-05-28.html"
    older.write_text("<html>older</html>", encoding="utf-8")
    newer.write_text("<html>newer</html>", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/reports")

    assert response.status_code == 200
    data = response.json()
    assert data["latest"]["name"] == newer.name
    assert data["reports"][0]["url"] == f"/report/output/{newer.name}"


def test_run_rejects_existing_lock(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "weekly_report.lock").write_text("{}", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/run")

    assert response.status_code == 409


def test_schedule_status_reads_last_schedule_file(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "last_schedule.json").write_text(
        json.dumps({"status": "success", "phase": "completed", "mode": "run-and-publish"}),
        encoding="utf-8",
    )

    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/schedule/status")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["mode"] == "run-and-publish"


def test_report_file_only_serves_output_html(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    report = output_dir / "report.html"
    report.write_text("<html>ok</html>", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)
    ok_response = client.get("/report/output/report.html")
    bad_response = client.get("/report/config.yaml")

    assert ok_response.status_code == 200
    assert bad_response.status_code in {403, 404}


def test_publish_dry_run_endpoint_validates_without_writing(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    latest = output_dir / _report_name()
    latest.write_text("<html><body>ok</body></html>", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/publish?dry_run=true")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run"
    assert data["archive_count"] == 1
    assert not (tmp_path / "index.html").exists()
    assert not (tmp_path / "reports").exists()


def test_publish_latest_writes_static_site_files(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    latest = output_dir / _report_name()
    latest.write_text('<html><body><img src="assets/c.jpg"></body></html>', encoding="utf-8")

    _client(tmp_path, monkeypatch)
    result = web_app._publish_latest(dry_run=False)

    assert result["status"] == "success"
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "reports" / "latest.html").exists()
    manifest = json.loads((tmp_path / "reports" / "index.json").read_text(encoding="utf-8"))
    assert manifest["latest"]["url"] == "./latest.html"
    assert manifest["reports"][0]["name"] == latest.name
    archived = tmp_path / "reports" / latest.name
    assert archived.exists()
    assert 'src="../assets/c.jpg"' in archived.read_text(encoding="utf-8")
    assert (tmp_path / "reports" / "index.html").exists()
