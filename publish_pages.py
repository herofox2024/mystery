"""Publish weekly HTML reports to GitHub Pages with archive support.

Usage:
    python publish_pages.py
    python publish_pages.py --git-push
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPORT_RE = re.compile(r"^推理资讯周报_(\d{4}-\d{2}-\d{2})\.html$")
ARCHIVE_START = "<!-- WEEKLY_ARCHIVE_LINK_START -->"
ARCHIVE_END = "<!-- WEEKLY_ARCHIVE_LINK_END -->"


def _find_latest_report(output_dir: Path) -> Path:
    candidates: list[tuple[datetime, Path]] = []
    for file in output_dir.glob("推理资讯周报_*.html"):
        match = REPORT_RE.match(file.name)
        if not match:
            continue
        date_value = datetime.strptime(match.group(1), "%Y-%m-%d")
        candidates.append((date_value, file))
    if not candidates:
        raise FileNotFoundError(f"no weekly report html found in: {output_dir}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _copy_latest_as_index(latest_report: Path, site_dir: Path) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    target = site_dir / "index.html"
    shutil.copy2(latest_report, target)
    return target


def _archive_report(latest_report: Path, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / latest_report.name
    target.write_text(_rewrite_asset_paths_for_archive(latest_report.read_text(encoding="utf-8")), encoding="utf-8")
    return target


def _sync_all_reports(output_dir: Path, reports_dir: Path) -> int:
    reports_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for file in output_dir.glob("推理资讯周报_*.html"):
        if not REPORT_RE.match(file.name):
            continue
        target = reports_dir / file.name
        target.write_text(_rewrite_asset_paths_for_archive(file.read_text(encoding="utf-8")), encoding="utf-8")
        copied += 1
    return copied


def _rewrite_asset_paths_for_archive(content: str) -> str:
    return (
        content.replace('src="assets/', 'src="../assets/')
        .replace("src='assets/", "src='../assets/")
        .replace('href="assets/', 'href="../assets/')
        .replace("href='assets/", "href='../assets/")
    )


def _copy_assets_if_exists(output_dir: Path, site_dir: Path) -> None:
    src = output_dir / "assets"
    if not src.exists():
        return
    dst = site_dir / "assets"
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _collect_archives(reports_dir: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    for file in reports_dir.glob("推理资讯周报_*.html"):
        match = REPORT_RE.match(file.name)
        if not match:
            continue
        rows.append((match.group(1), file))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows


def _write_archive_index(reports_dir: Path, archives: list[tuple[str, Path]]) -> Path:
    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\">",
        "<head>",
        "<meta charset=\"UTF-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
        "<title>推理资讯周报归档</title>",
        "<style>",
        "body{font-family: 'Microsoft YaHei', sans-serif; margin: 0; background:#0b1423; color:#eaf2ff;}",
        ".wrap{max-width:980px; margin:40px auto; padding:0 16px;}",
        "h1{margin:0 0 8px; font-size:32px;}",
        ".sub{color:#9fb2cc; margin-bottom:20px;}",
        ".back{display:inline-block; margin-bottom:20px; color:#77d1ff; text-decoration:none;}",
        ".card{background:#111e33; border:1px solid #2a3f61; border-radius:12px; padding:14px 16px; margin:10px 0;}",
        ".date{font-weight:700; color:#ffd27f;}",
        "a{color:#77d1ff; text-decoration:none;}",
        "a:hover{text-decoration:underline;}",
        ".counter{margin-top:24px; text-align:center; color:#9fb2cc; font-size:0.82rem;}",
        ".counter span{color:#77d1ff; font-weight:700;}",
        "</style>",
        "</head>",
        "<body>",
        "<div class=\"wrap\">",
        "<h1>推理资讯周报归档</h1>",
        "<div class=\"sub\">按日期查看历史周报</div>",
        "<a class=\"back\" href=\"../index.html\">返回最新周报</a>",
    ]
    if archives:
        for date_text, file in archives:
            lines.append(
                f"<div class=\"card\"><span class=\"date\">{date_text}</span> · "
                f"<a href=\"./{file.name}\">{file.name}</a></div>"
            )
    else:
        lines.append("<div class=\"card\">暂无归档内容</div>")

    lines.extend([
        "<div class=\"counter\">",
        "<span id=\"busuanzi_container_site_pv\" style=\"display:none;\">站点总访问 <span id=\"busuanzi_value_site_pv\"></span> 次</span>",
        "</div>",
        "</div>",
        "<script async src=\"//busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js\"></script>",
        "</body>",
        "</html>",
    ])
    target = reports_dir / "index.html"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _inject_archive_link(index_path: Path, relative_archive_url: str) -> None:
    content = index_path.read_text(encoding="utf-8")
    snippet = (
        f"{ARCHIVE_START}\n"
        "<a href=\"{url}\" style=\"position:fixed;right:16px;bottom:16px;z-index:9999;"
        "padding:10px 14px;border-radius:999px;background:#122843;color:#77d1ff;"
        "border:1px solid #2a4f77;text-decoration:none;font:14px/1.2 'Microsoft YaHei',sans-serif;"
        "box-shadow:0 6px 20px rgba(0,0,0,.35)\">历史归档</a>\n"
        f"{ARCHIVE_END}"
    ).format(url=relative_archive_url)

    if ARCHIVE_START in content and ARCHIVE_END in content:
        content = re.sub(
            rf"{re.escape(ARCHIVE_START)}.*?{re.escape(ARCHIVE_END)}",
            snippet,
            content,
            flags=re.S,
        )
    elif "</body>" in content:
        content = content.replace("</body>", f"{snippet}\n</body>")
    else:
        content = content + "\n" + snippet + "\n"

    index_path.write_text(content, encoding="utf-8")


def _run_git_publish(site_dir: Path, latest_name: str) -> None:
    git_dir = site_dir / ".git"
    if not git_dir.exists():
        print("skip git publish: current directory is not a git repo")
        return

    # Publish the site-facing files only: homepage, archives, and synced assets.
    rel_paths = ["index.html", "reports", "assets"]
    subprocess.run(["git", "add", *rel_paths], cwd=site_dir, check=True)
    commit_msg = f"publish weekly report: {latest_name}"
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=site_dir,
        check=False,
    )
    if status.returncode == 0:
        print("no staged changes, skip commit and push")
        return
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=site_dir, check=True)
    subprocess.run(["git", "push"], cwd=site_dir, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish weekly report to GitHub Pages with archives.")
    parser.add_argument("--project-root", default=".", help="project root path")
    parser.add_argument("--output-dir", default="output", help="report output directory")
    parser.add_argument("--site-dir", default=".", help="GitHub Pages source directory")
    parser.add_argument("--reports-subdir", default="reports", help="archive sub directory name")
    parser.add_argument("--latest-html", default="", help="specific latest report html path")
    parser.add_argument("--git-push", action="store_true", help="run git add/commit/push")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    site_dir = (project_root / args.site_dir).resolve()
    reports_dir = site_dir / args.reports_subdir

    latest_report = Path(args.latest_html).resolve() if args.latest_html else _find_latest_report(output_dir)
    if not latest_report.exists():
        raise FileNotFoundError(f"latest report not found: {latest_report}")

    index_path = _copy_latest_as_index(latest_report, site_dir)
    archived_report = _archive_report(latest_report, reports_dir)
    synced_count = _sync_all_reports(output_dir, reports_dir)
    _copy_assets_if_exists(output_dir, site_dir)
    archives = _collect_archives(reports_dir)
    archive_index = _write_archive_index(reports_dir, archives)
    _inject_archive_link(index_path, f"./{args.reports_subdir}/index.html")

    print(f"latest report: {latest_report}")
    print(f"site index: {index_path}")
    print(f"archived report: {archived_report}")
    print(f"archive index: {archive_index}")
    print(f"synced reports: {synced_count}")
    print(f"archives total: {len(archives)}")

    if args.git_push:
        _run_git_publish(site_dir, latest_report.name)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"publish failed: {exc}")
        raise
