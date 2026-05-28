"""Local web console for running and inspecting weekly report jobs."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import main
import publish_pages

PROJECT_ROOT = Path(main.PROJECT_ROOT)
_RUN_THREAD: threading.Thread | None = None
_RUN_THREAD_LOCK = threading.Lock()

app = FastAPI(title="推理资讯周报本地控制台")


def _safe_relative_path(value: str) -> Path:
    candidate = (PROJECT_ROOT / value).resolve()
    root = PROJECT_ROOT.resolve()
    if candidate == root or root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path is outside project root")
    return candidate


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _status_path(is_test: bool = False) -> Path:
    return Path(main._runtime_path(str(PROJECT_ROOT), "last_run.json", is_test=is_test))


def _lock_path(is_test: bool = False) -> Path:
    return Path(main._runtime_path(str(PROJECT_ROOT), "weekly_report.lock", is_test=is_test))


def _publish_status_path() -> Path:
    return Path(main._runtime_path(str(PROJECT_ROOT), "last_publish.json"))


def _schedule_status_path() -> Path:
    return Path(main._runtime_path(str(PROJECT_ROOT), "last_schedule.json"))


def _runtime_log_path() -> Path:
    return Path(main.ensure_runtime_logging(str(PROJECT_ROOT)))


def _is_thread_alive() -> bool:
    return bool(_RUN_THREAD and _RUN_THREAD.is_alive())


def _is_running(is_test: bool = False) -> bool:
    return _is_thread_alive() or _lock_path(is_test=is_test).exists()


def _record_publish_status(payload: dict[str, Any]) -> None:
    path = _publish_status_path()
    current = _read_json(path)
    current.update(payload)
    current["updated_at"] = main._now_utc_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_public_config_summary() -> dict[str, Any]:
    try:
        cfg = main.load_config()
    except (FileNotFoundError, SystemExit):
        cfg = {}
    rules = cfg.get("filter_rules", {})
    ai_cfg = cfg.get("ai_filter", {})
    providers = ai_cfg.get("providers") or []
    return {
        "target_year": rules.get("target_year"),
        "min_rating": rules.get("min_rating"),
        "min_rating_count": rules.get("min_rating_count"),
        "exact_target_year_only": rules.get("exact_target_year_only"),
        "ai_enabled": ai_cfg.get("enabled"),
        "ai_providers": [provider.get("provider") for provider in providers if isinstance(provider, dict)],
        "output_dir": cfg.get("report", {}).get("output_dir", "output"),
    }


def _list_reports(output_dir: str = "output") -> list[dict[str, Any]]:
    directory = _safe_relative_path(output_dir)
    if not directory.exists():
        return []

    reports: list[dict[str, Any]] = []
    for path in directory.glob("*.html"):
        stat = path.stat()
        reports.append(
            {
                "name": path.name,
                "path": str(path.relative_to(PROJECT_ROOT)),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "url": f"/report/{path.relative_to(PROJECT_ROOT).as_posix()}",
            }
        )
    reports.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    return reports


def _git_summary(site_dir: Path) -> dict[str, Any]:
    if not (site_dir / ".git").exists():
        return {"is_repo": False, "branch": "", "remote_count": 0, "has_changes": False}

    def run_git(args: list[str]) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=site_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip()

    remotes = [line for line in run_git(["remote"]).splitlines() if line.strip()]
    porcelain = run_git(["status", "--porcelain"])
    return {
        "is_repo": True,
        "branch": run_git(["branch", "--show-current"]),
        "remote_count": len(remotes),
        "has_changes": bool(porcelain),
    }


def _plan_payload(plan: dict[str, Any], *, dry_run: bool = False, git_push: bool = False) -> dict[str, Any]:
    site_dir = Path(plan["site_dir"])
    return {
        "dry_run": dry_run,
        "git_push": git_push,
        "latest_report": str(plan["latest_report"]),
        "site_index": str(plan["site_index"]),
        "latest_alias": str(plan["latest_alias"]),
        "archived_report": str(plan["archived_report"]),
        "archive_index": str(plan["archive_index"]),
        "archive_manifest": str(plan["archive_manifest"]),
        "archive_count": plan["archive_count"],
        "assets_dir": str(plan["assets_dir"]),
        "git": _git_summary(site_dir),
    }


def _build_publish_plan(latest_html: str = "") -> dict[str, Any]:
    cfg = _load_public_config_summary()
    output_dir = (PROJECT_ROOT / str(cfg.get("output_dir") or "output")).resolve()
    return publish_pages._build_publish_plan(
        PROJECT_ROOT.resolve(),
        output_dir,
        PROJECT_ROOT.resolve(),
        "reports",
        latest_html=latest_html,
    )


def _publish_latest(*, dry_run: bool = False, git_push: bool = False, latest_html: str = "") -> dict[str, Any]:
    started_at = main._now_utc_iso()
    _record_publish_status(
        {
            "status": "running",
            "phase": "validating",
            "message": "Validating latest generated report.",
            "started_at": started_at,
            "completed_at": "",
            "git_push": git_push,
            "dry_run": dry_run,
        }
    )
    plan = _build_publish_plan(latest_html=latest_html)
    payload = _plan_payload(plan, dry_run=dry_run, git_push=git_push)
    if dry_run:
        payload.update(
            {
                "status": "dry_run",
                "phase": "validated",
                "message": "Publish plan validated without writing site files.",
                "started_at": started_at,
                "completed_at": main._now_utc_iso(),
            }
        )
        _record_publish_status(payload)
        return payload

    _record_publish_status(
        {
            "status": "running",
            "phase": "syncing_site",
            "message": "Copying latest report and archive files.",
            "plan": payload,
        }
    )
    if git_push:
        _record_publish_status(
            {
                "status": "running",
                "phase": "git_push",
                "message": "Committing and pushing site files.",
                "plan": payload,
            }
        )
    publish_result = publish_pages.publish_latest(
        plan["project_root"],
        plan["output_dir"],
        plan["site_dir"],
        "reports",
        latest_html=latest_html,
        dry_run=False,
        git_push=git_push,
    )

    result = {
        **payload,
        "status": "success",
        "phase": "completed",
        "message": "Static GitHub Pages files published locally.",
        "index": str(publish_result["site_index"]),
        "latest_alias": str(publish_result["latest_alias"]),
        "archived": str(publish_result["archived_report"]),
        "archive_index": str(publish_result["archive_index"]),
        "archive_manifest": str(publish_result["archive_manifest"]),
        "synced_reports": publish_result["synced_reports"],
        "archives_total": publish_result["archives_total"],
        "started_at": started_at,
        "completed_at": main._now_utc_iso(),
    }
    _record_publish_status(result)
    return result


def _tail_text(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    return "\n".join(content[-max(min(lines, 500), 1) :])


def _run_job(is_test: bool = False) -> None:
    try:
        cfg = main.load_config()
        main.run_once(cfg, is_test=is_test)
    except SystemExit as exc:
        main._record_run_status(
            str(PROJECT_ROOT),
            {
                "status": "failed",
                "phase": "failed",
                "message": "Configuration validation failed.",
                "is_test": is_test,
                "error": f"SystemExit({exc.code})",
                "completed_at": main._now_utc_iso(),
            },
            is_test=is_test,
        )


def _publish_job(*, git_push: bool = False, dry_run: bool = False) -> None:
    try:
        _publish_latest(dry_run=dry_run, git_push=git_push)
    except Exception as exc:
        main.logger.exception("Local web publish failed")
        _record_publish_status(
            {
                "status": "failed",
                "phase": "failed",
                "message": "Static publish failed.",
                "error": str(exc),
                "git_push": git_push,
                "dry_run": dry_run,
                "completed_at": main._now_utc_iso(),
            }
        )


def _run_and_publish_job(*, git_push: bool = False) -> None:
    try:
        _run_job(is_test=False)
        run_status = _read_json(_status_path(is_test=False))
        latest_html = str(run_status.get("html") or "")
        if run_status.get("status") != "success" or not latest_html:
            _record_publish_status(
                {
                    "status": "skipped_no_new_report",
                    "phase": "skipped",
                    "message": "Report generation did not create a new HTML file; publish was skipped.",
                    "run_status": run_status.get("status", ""),
                    "git_push": git_push,
                    "completed_at": main._now_utc_iso(),
                }
            )
            return
        _publish_latest(dry_run=False, git_push=git_push, latest_html=latest_html)
    except Exception as exc:
        main.logger.exception("Local web run-and-publish failed")
        _record_publish_status(
            {
                "status": "failed",
                "phase": "failed",
                "message": "Run-and-publish failed.",
                "error": str(exc),
                "git_push": git_push,
                "completed_at": main._now_utc_iso(),
            }
        )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


@app.get("/api/config")
def config_summary() -> dict[str, Any]:
    return _load_public_config_summary()


@app.get("/api/status")
def status(test: bool = False) -> dict[str, Any]:
    payload = _read_json(_status_path(is_test=test))
    payload.setdefault("status", "idle")
    payload.setdefault("phase", "idle")
    payload.setdefault("message", "No run has been recorded yet.")
    payload["running"] = _is_running(is_test=test)
    payload["lock_exists"] = _lock_path(is_test=test).exists()
    return payload


@app.post("/api/run")
def run(test: bool = False) -> dict[str, Any]:
    global _RUN_THREAD
    with _RUN_THREAD_LOCK:
        if _is_running(is_test=test):
            raise HTTPException(status_code=409, detail="A weekly report run is already in progress.")

        main.ensure_runtime_logging(str(PROJECT_ROOT))
        main._record_run_status(
            str(PROJECT_ROOT),
            {
                "status": "queued",
                "phase": "queued",
                "message": "Run queued from local web console.",
                "is_test": test,
                "started_at": main._now_utc_iso(),
                "completed_at": "",
            },
            is_test=test,
        )
        _RUN_THREAD = threading.Thread(target=_run_job, kwargs={"is_test": test}, daemon=True)
        _RUN_THREAD.start()
    return {"status": "queued", "running": True, "is_test": test}


@app.get("/api/publish/status")
def publish_status() -> dict[str, Any]:
    payload = _read_json(_publish_status_path())
    payload.setdefault("status", "idle")
    payload.setdefault("phase", "idle")
    payload.setdefault("message", "No publish has been recorded yet.")
    payload["running"] = _is_thread_alive()
    return payload


@app.get("/api/schedule/status")
def schedule_status() -> dict[str, Any]:
    payload = _read_json(_schedule_status_path())
    payload.setdefault("status", "idle")
    payload.setdefault("phase", "idle")
    payload.setdefault("message", "No scheduled run has been recorded yet.")
    return payload


@app.get("/api/publish/plan")
def publish_plan() -> dict[str, Any]:
    try:
        plan = _build_publish_plan()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _plan_payload(plan, dry_run=True, git_push=False)


@app.post("/api/publish")
def publish(dry_run: bool = False, git_push: bool = False) -> dict[str, Any]:
    global _RUN_THREAD
    if dry_run:
        try:
            return _publish_latest(dry_run=True, git_push=git_push)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _RUN_THREAD_LOCK:
        if _is_running():
            raise HTTPException(status_code=409, detail="A run or publish job is already in progress.")

        _record_publish_status(
            {
                "status": "queued",
                "phase": "queued",
                "message": "Publish queued from local web console.",
                "git_push": git_push,
                "dry_run": False,
                "started_at": main._now_utc_iso(),
                "completed_at": "",
            }
        )
        _RUN_THREAD = threading.Thread(target=_publish_job, kwargs={"git_push": git_push}, daemon=True)
        _RUN_THREAD.start()
    return {"status": "queued", "running": True, "git_push": git_push}


@app.post("/api/run-and-publish")
def run_and_publish(git_push: bool = False) -> dict[str, Any]:
    global _RUN_THREAD
    with _RUN_THREAD_LOCK:
        if _is_running():
            raise HTTPException(status_code=409, detail="A run or publish job is already in progress.")

        _record_publish_status(
            {
                "status": "queued",
                "phase": "queued",
                "message": "Run-and-publish queued from local web console.",
                "git_push": git_push,
                "dry_run": False,
                "started_at": main._now_utc_iso(),
                "completed_at": "",
            }
        )
        _RUN_THREAD = threading.Thread(target=_run_and_publish_job, kwargs={"git_push": git_push}, daemon=True)
        _RUN_THREAD.start()
    return {"status": "queued", "running": True, "git_push": git_push}


@app.get("/api/reports")
def reports(test: bool = False) -> dict[str, Any]:
    output_dir = "output_test" if test else _load_public_config_summary().get("output_dir", "output")
    items = _list_reports(str(output_dir))
    return {"reports": items, "latest": items[0] if items else None}


@app.get("/api/reports/latest")
def latest_report(test: bool = False) -> dict[str, Any]:
    items = reports(test=test)["reports"]
    if not items:
        raise HTTPException(status_code=404, detail="No report found")
    return items[0]


@app.get("/api/logs")
def logs(lines: int = 120) -> dict[str, Any]:
    log_path = _runtime_log_path()
    return {"path": str(log_path.relative_to(PROJECT_ROOT)), "content": _tail_text(log_path, lines)}


@app.get("/report/{relative_path:path}")
def report_file(relative_path: str) -> FileResponse:
    path = _safe_relative_path(relative_path)
    if not path.exists() or path.suffix.lower() != ".html":
        raise HTTPException(status_code=404, detail="Report not found")
    allowed_dirs = {(PROJECT_ROOT / "output").resolve(), (PROJECT_ROOT / "output_test").resolve()}
    if not any(path == directory or directory in path.parents for directory in allowed_dirs):
        raise HTTPException(status_code=403, detail="Only generated reports can be served")
    return FileResponse(path)


_INDEX_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>推理资讯周报本地控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f4ef;
      --panel: #ffffff;
      --text: #202124;
      --muted: #6f6f68;
      --line: #d8d3c7;
      --accent: #176b87;
      --accent-strong: #0f4c5c;
      --warn: #a15c17;
      --bad: #9f2a2a;
      --ok: #246b45;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: #fbfaf7;
    }
    .wrap {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 20px 0;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .subtitle {
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }
    main.wrap {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 16px;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    section + section { margin-top: 16px; }
    h2 {
      margin: 0 0 12px;
      font-size: 16px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    button, a.button {
      appearance: none;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 7px;
      padding: 9px 13px;
      font-size: 14px;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      min-height: 38px;
    }
    button.secondary, a.button.secondary {
      background: white;
      color: var(--accent-strong);
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    .status-line {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 13px;
      background: #ece7da;
      color: #38352e;
    }
    .pill.running { background: #dcecf1; color: var(--accent-strong); }
    .pill.success { background: #dceade; color: var(--ok); }
    .pill.failed { background: #f2dcdc; color: var(--bad); }
    .pill.warn { background: #f1e3cf; color: var(--warn); }
    dl {
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 8px 12px;
      margin: 14px 0 0;
      font-size: 14px;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    .stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      min-height: 70px;
    }
    .metric b {
      display: block;
      font-size: 22px;
      margin-bottom: 2px;
    }
    .metric span {
      color: var(--muted);
      font-size: 13px;
    }
    .reports {
      display: grid;
      gap: 8px;
    }
    .report-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 9px 0;
      border-top: 1px solid var(--line);
      font-size: 14px;
    }
    .report-row:first-child { border-top: 0; }
    .report-row a {
      color: var(--accent-strong);
      text-decoration: none;
      overflow-wrap: anywhere;
    }
    pre {
      margin: 0;
      padding: 12px;
      background: #1f2326;
      color: #e8eaed;
      border-radius: 7px;
      min-height: 280px;
      max-height: 420px;
      overflow: auto;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
    }
    .error { color: var(--bad); margin-top: 10px; font-size: 14px; }
    @media (max-width: 820px) {
      main.wrap { grid-template-columns: 1fr; }
      .stats { grid-template-columns: 1fr 1fr; }
      dl { grid-template-columns: 110px 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>推理资讯周报本地控制台</h1>
      <div class="subtitle">本地运行抓取和智谱过滤，GitHub Pages 只展示静态结果。</div>
    </div>
  </header>
  <main class="wrap">
    <div>
      <section>
        <h2>运行控制</h2>
        <div class="status-line">
          <span id="statusPill" class="pill">idle</span>
          <span id="phaseText">等待状态读取</span>
        </div>
        <div class="actions">
          <button id="runButton" type="button">立即更新周报</button>
          <button id="testRunButton" class="secondary" type="button">测试运行</button>
          <button id="publishDryRunButton" class="secondary" type="button">发布预检</button>
          <button id="publishButton" class="secondary" type="button">发布静态页</button>
          <button id="runPublishButton" type="button">生成并发布</button>
          <a id="latestLink" class="button secondary" href="#" target="_blank" rel="noreferrer">打开最新报告</a>
        </div>
        <div id="runError" class="error"></div>
        <div id="publishError" class="error"></div>
        <dl id="statusDetails"></dl>
      </section>
      <section>
        <h2>抓取与过滤统计</h2>
        <div class="stats">
          <div class="metric"><b id="rawBooks">0</b><span>原始书籍</span></div>
          <div class="metric"><b id="filteredBooks">0</b><span>规则后书籍</span></div>
          <div class="metric"><b id="finalBooks">0</b><span>新增书籍</span></div>
          <div class="metric"><b id="rawRss">0</b><span>原始资讯</span></div>
          <div class="metric"><b id="filteredRss">0</b><span>规则后资讯</span></div>
          <div class="metric"><b id="finalRss">0</b><span>新增资讯</span></div>
        </div>
      </section>
      <section>
        <h2>最近日志</h2>
        <pre id="logs">读取中...</pre>
      </section>
    </div>
    <aside>
      <section>
        <h2>配置摘要</h2>
        <dl id="configDetails"></dl>
      </section>
      <section>
        <h2>静态发布</h2>
        <dl id="publishDetails"></dl>
      </section>
      <section>
        <h2>定时任务</h2>
        <dl id="scheduleDetails"></dl>
      </section>
      <section>
        <h2>历史报告</h2>
        <div id="reports" class="reports">读取中...</div>
      </section>
    </aside>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);

    function fmtTime(value) {
      if (!value) return "";
      const d = new Date(value);
      return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
    }

    function setDetails(node, rows) {
      node.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v || ""}</dd>`).join("");
    }

    function statusClass(status, running) {
      if (running || status === "queued") return "pill running";
      if (status === "success") return "pill success";
      if (status === "failed") return "pill failed";
      if (status === "no_new_items" || status === "skipped_locked") return "pill warn";
      return "pill";
    }

    async function refreshStatus() {
      const res = await fetch("/api/status");
      const data = await res.json();
      $("statusPill").className = statusClass(data.status, data.running);
      $("statusPill").textContent = data.running ? "running" : data.status;
      $("phaseText").textContent = `${data.phase || "idle"} · ${data.message || ""}`;
      $("runButton").disabled = !!data.running;
      $("testRunButton").disabled = !!data.running;
      $("publishDryRunButton").disabled = !!data.running;
      $("publishButton").disabled = !!data.running;
      $("runPublishButton").disabled = !!data.running;
      const stats = data.stats || {};
      $("rawBooks").textContent = stats.raw_books || 0;
      $("filteredBooks").textContent = stats.filtered_books || 0;
      $("finalBooks").textContent = stats.final_books || 0;
      $("rawRss").textContent = stats.raw_rss || 0;
      $("filteredRss").textContent = stats.filtered_rss || 0;
      $("finalRss").textContent = stats.final_rss || 0;
      setDetails($("statusDetails"), [
        ["开始时间", fmtTime(data.started_at)],
        ["结束时间", fmtTime(data.completed_at)],
        ["Markdown", data.markdown || ""],
        ["HTML", data.html || ""],
        ["错误", data.error || ""]
      ]);
    }

    async function refreshPublishStatus() {
      const res = await fetch("/api/publish/status");
      const data = await res.json();
      $("publishDryRunButton").disabled = !!data.running;
      $("publishButton").disabled = !!data.running;
      $("runPublishButton").disabled = !!data.running;
      const git = data.git || (data.plan && data.plan.git) || {};
      setDetails($("publishDetails"), [
        ["状态", data.running ? "running" : (data.status || "idle")],
        ["阶段", data.phase || ""],
        ["说明", data.message || ""],
        ["最新报告", data.latest_report || (data.plan && data.plan.latest_report) || ""],
        ["首页", data.site_index || (data.plan && data.plan.site_index) || ""],
        ["最新别名", data.latest_alias || (data.plan && data.plan.latest_alias) || ""],
        ["JSON 索引", data.archive_manifest || (data.plan && data.plan.archive_manifest) || ""],
        ["归档数量", data.archive_count || (data.plan && data.plan.archive_count) || ""],
        ["Git 仓库", git.is_repo === undefined ? "" : String(git.is_repo)],
        ["Git 分支", git.branch || ""],
        ["远程数量", git.remote_count === undefined ? "" : git.remote_count],
        ["结束时间", fmtTime(data.completed_at)],
        ["错误", data.error || ""]
      ]);
    }

    async function refreshScheduleStatus() {
      const res = await fetch("/api/schedule/status");
      const data = await res.json();
      setDetails($("scheduleDetails"), [
        ["状态", data.status || "idle"],
        ["阶段", data.phase || ""],
        ["模式", data.mode || ""],
        ["说明", data.message || ""],
        ["开始时间", fmtTime(data.started_at)],
        ["结束时间", fmtTime(data.completed_at)],
        ["错误", data.error || ""]
      ]);
    }

    async function refreshConfig() {
      const res = await fetch("/api/config");
      const data = await res.json();
      setDetails($("configDetails"), [
        ["目标年份", data.target_year],
        ["最低评分", data.min_rating],
        ["最低评价数", data.min_rating_count],
        ["精确年份", String(data.exact_target_year_only)],
        ["AI 启用", String(data.ai_enabled)],
        ["Provider", (data.ai_providers || []).join(" → ")],
        ["输出目录", data.output_dir]
      ]);
    }

    async function refreshReports() {
      const res = await fetch("/api/reports");
      const data = await res.json();
      const reports = data.reports || [];
      $("latestLink").style.pointerEvents = data.latest ? "auto" : "none";
      $("latestLink").style.opacity = data.latest ? "1" : ".5";
      if (data.latest) $("latestLink").href = data.latest.url;
      $("reports").innerHTML = reports.length
        ? reports.slice(0, 8).map(item => `<div class="report-row"><a href="${item.url}" target="_blank" rel="noreferrer">${item.name}</a><span>${fmtTime(item.modified_at * 1000)}</span></div>`).join("")
        : "暂无报告";
    }

    async function refreshLogs() {
      const res = await fetch("/api/logs?lines=160");
      const data = await res.json();
      $("logs").textContent = data.content || "暂无日志";
    }

    async function run(test=false) {
      $("runError").textContent = "";
      const res = await fetch(`/api/run?test=${test}`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        $("runError").textContent = data.detail || "启动失败";
      }
      await refreshAll();
    }

    async function publishSite(dryRun=false) {
      $("publishError").textContent = "";
      const res = await fetch(`/api/publish?dry_run=${dryRun}`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        $("publishError").textContent = data.detail || "发布启动失败";
      }
      await refreshAll();
    }

    async function runAndPublish() {
      $("publishError").textContent = "";
      const res = await fetch("/api/run-and-publish", { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        $("publishError").textContent = data.detail || "生成并发布启动失败";
      }
      await refreshAll();
    }

    async function refreshAll() {
      await Promise.all([refreshStatus(), refreshPublishStatus(), refreshScheduleStatus(), refreshReports(), refreshLogs()]);
    }

    $("runButton").addEventListener("click", () => run(false));
    $("testRunButton").addEventListener("click", () => run(true));
    $("publishDryRunButton").addEventListener("click", () => publishSite(true));
    $("publishButton").addEventListener("click", () => publishSite(false));
    $("runPublishButton").addEventListener("click", () => runAndPublish());
    refreshConfig();
    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
