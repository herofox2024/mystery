"""Scheduled local runner for weekly report generation and static publishing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import main
import publish_pages

PROJECT_ROOT = Path(main.PROJECT_ROOT)
logger = logging.getLogger("weekly_report.scheduler")


def _status_path() -> Path:
    return Path(main._runtime_path(str(PROJECT_ROOT), "last_schedule.json"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_schedule_status(payload: dict[str, Any]) -> None:
    path = _status_path()
    current = _read_json(path)
    current.update(_jsonable(payload))
    current["updated_at"] = main._now_utc_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _last_run_status() -> dict[str, Any]:
    return _read_json(Path(main._runtime_path(str(PROJECT_ROOT), "last_run.json")))


def _load_config() -> dict:
    cfg = main.load_config()
    main.ensure_runtime_logging(str(PROJECT_ROOT))
    return cfg


def _publish_latest(cfg: dict, *, latest_html: str = "", dry_run: bool = False, git_push: bool = False) -> dict:
    output_dir = (PROJECT_ROOT / str(cfg.get("report", {}).get("output_dir", "output"))).resolve()
    return publish_pages.publish_latest(
        PROJECT_ROOT.resolve(),
        output_dir,
        PROJECT_ROOT.resolve(),
        "reports",
        latest_html=latest_html,
        dry_run=dry_run,
        git_push=git_push,
    )


def run_once(*, mode: str, dry_run: bool = False, git_push: bool = False) -> int:
    started_at = main._now_utc_iso()
    _write_schedule_status(
        {
            "status": "running",
            "phase": "starting",
            "message": f"Scheduled mode started: {mode}",
            "mode": mode,
            "dry_run": dry_run,
            "git_push": git_push,
            "started_at": started_at,
            "completed_at": "",
        }
    )

    try:
        cfg = _load_config()

        if mode == "run":
            _write_schedule_status({"phase": "running_report", "message": "Generating weekly report."})
            main.run_once(cfg, is_test=False)
            run_status = _last_run_status()
            _write_schedule_status(
                {
                    "status": "success",
                    "phase": "completed",
                    "message": "Scheduled report generation finished.",
                    "run_status": run_status,
                    "completed_at": main._now_utc_iso(),
                }
            )
            return 0

        if mode == "publish":
            _write_schedule_status({"phase": "publishing", "message": "Publishing latest static report."})
            publish_result = _publish_latest(cfg, dry_run=dry_run, git_push=git_push)
            _write_schedule_status(
                {
                    "status": publish_result.get("status", "success"),
                    "phase": "completed",
                    "message": "Scheduled static publish finished.",
                    "publish_result": publish_result,
                    "completed_at": main._now_utc_iso(),
                }
            )
            return 0

        if mode != "run-and-publish":
            raise ValueError(f"unsupported mode: {mode}")

        _write_schedule_status({"phase": "running_report", "message": "Generating weekly report before publish."})
        main.run_once(cfg, is_test=False)
        run_status = _last_run_status()
        latest_html = str(run_status.get("html") or "")
        if run_status.get("status") != "success" or not latest_html:
            _write_schedule_status(
                {
                    "status": "skipped_no_new_report",
                    "phase": "skipped",
                    "message": "No new HTML report was generated; publish skipped.",
                    "run_status": run_status,
                    "completed_at": main._now_utc_iso(),
                }
            )
            return 0

        _write_schedule_status({"phase": "publishing", "message": "Publishing generated static report."})
        publish_result = _publish_latest(cfg, latest_html=latest_html, dry_run=dry_run, git_push=git_push)
        _write_schedule_status(
            {
                "status": "success",
                "phase": "completed",
                "message": "Scheduled run-and-publish finished.",
                "run_status": run_status,
                "publish_result": publish_result,
                "completed_at": main._now_utc_iso(),
            }
        )
        return 0
    except Exception as exc:
        logger.exception("Scheduled runner failed")
        _write_schedule_status(
            {
                "status": "failed",
                "phase": "failed",
                "message": "Scheduled runner failed.",
                "error": str(exc),
                "completed_at": main._now_utc_iso(),
            }
        )
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run weekly report tasks from a local scheduler.")
    parser.add_argument(
        "--mode",
        choices=("run", "publish", "run-and-publish"),
        default="run-and-publish",
        help="scheduled task mode",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate publish plan without writing site files")
    parser.add_argument("--git-push", action="store_true", help="commit and push static site changes after publish")
    return parser.parse_args(argv)


def main_cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_once(mode=args.mode, dry_run=args.dry_run, git_push=args.git_push)


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
