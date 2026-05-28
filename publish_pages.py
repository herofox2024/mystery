"""Publish weekly HTML reports to GitHub Pages with archive support.

Usage:
    python publish_pages.py
    python publish_pages.py --git-push
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from html import escape, unescape
from pathlib import Path

REPORT_RE = re.compile(r"^推理资讯周报_(\d{4}-\d{2}-\d{2})\.html$")
ARCHIVE_START = "<!-- WEEKLY_ARCHIVE_LINK_START -->"
ARCHIVE_END = "<!-- WEEKLY_ARCHIVE_LINK_END -->"
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
HERO_VALUE_RE = re.compile(r"class=[\"']hero-value[\"'][^>]*>\s*(\d+)\s*<", re.I)


def _find_latest_report(output_dir: Path) -> Path:
    if not output_dir.exists():
        raise FileNotFoundError(f"output directory not found: {output_dir}")
    candidates: list[tuple[datetime, Path]] = []
    for file in output_dir.glob("*.html"):
        match = REPORT_RE.match(file.name)
        if not match:
            continue
        date_value = datetime.strptime(match.group(1), "%Y-%m-%d")
        candidates.append((date_value, file))
    if not candidates:
        raise FileNotFoundError(f"no weekly report html found in: {output_dir}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _validate_latest_report(latest_report: Path, output_dir: Path) -> None:
    if not latest_report.exists():
        raise FileNotFoundError(f"latest report not found: {latest_report}")
    if latest_report.suffix.lower() != ".html":
        raise ValueError(f"latest report must be an html file: {latest_report}")
    try:
        latest_report.resolve().relative_to(output_dir.resolve())
    except ValueError:
        raise ValueError(f"latest report must be inside output directory: {latest_report}") from None
    content = latest_report.read_text(encoding="utf-8", errors="ignore")
    if "</html>" not in content.lower():
        raise ValueError(f"latest report does not look like a complete html file: {latest_report}")


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


def _write_latest_alias(latest_report: Path, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "latest.html"
    target.write_text(_rewrite_asset_paths_for_archive(latest_report.read_text(encoding="utf-8")), encoding="utf-8")
    return target


def _sync_all_reports(output_dir: Path, reports_dir: Path) -> int:
    reports_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for file in output_dir.glob("*.html"):
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
    for file in reports_dir.glob("*.html"):
        match = REPORT_RE.match(file.name)
        if not match:
            continue
        rows.append((match.group(1), file))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows


def _clean_html_text(value: str) -> str:
    return " ".join(TAG_RE.sub(" ", unescape(value or "")).split())


def _metadata_for_report(file: Path, url: str) -> dict:
    content = file.read_text(encoding="utf-8", errors="ignore")
    title_match = TITLE_RE.search(content)
    title = _clean_html_text(title_match.group(1)) if title_match else file.stem
    hero_values = [int(value) for value in HERO_VALUE_RE.findall(content)]
    match = REPORT_RE.match(file.name)
    stat = file.stat()
    return {
        "date": match.group(1) if match else "",
        "name": file.name,
        "title": title or file.stem,
        "url": url,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "selected_books": hero_values[0] if len(hero_values) >= 1 else None,
        "selected_rss": hero_values[1] if len(hero_values) >= 2 else None,
    }


def _build_archive_metadata(archives: list[tuple[str, Path]]) -> list[dict]:
    return [_metadata_for_report(file, f"./{file.name}") for _, file in archives]


def _write_archive_manifest(reports_dir: Path, archives: list[tuple[str, Path]], latest_alias: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    reports = _build_archive_metadata(archives)
    latest = reports[0] if reports else {}
    if latest:
        latest = dict(latest)
        latest["url"] = f"./{latest_alias.name}"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest": latest,
        "reports": reports,
    }
    target = reports_dir / "index.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _write_archive_index(reports_dir: Path, archives: list[tuple[str, Path]]) -> Path:
    metadata = _build_archive_metadata(archives)
    latest = metadata[0] if metadata else {}
    latest_title = escape(str(latest.get("title") or "暂无周报"))
    latest_date = escape(str(latest.get("date") or "未知"))
    latest_books = latest.get("selected_books")
    latest_rss = latest.get("selected_rss")
    latest_books_text = "未知" if latest_books is None else str(latest_books)
    latest_rss_text = "未知" if latest_rss is None else str(latest_rss)

    lines = [
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\">",
        "<head>",
        "<meta charset=\"UTF-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
        "<title>推理资讯周报归档</title>",
        "<style>",
        ":root{--bg:#f6f4ef;--panel:#fff;--ink:#202124;--muted:#666b70;--line:#d8d3c7;--accent:#176b87;--ok:#246b45;}",
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;line-height:1.65}",
        ".wrap{width:min(1080px,calc(100vw - 32px));margin:0 auto;padding:28px 0 56px}",
        "header{padding:22px 0;border-bottom:1px solid var(--line);background:#fbfaf7}",
        "h1{margin:0;font-size:30px;letter-spacing:0}h2{margin:0 0 12px;font-size:18px}",
        ".sub{margin-top:6px;color:var(--muted);font-size:14px}",
        ".toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}",
        "a.button{display:inline-flex;align-items:center;min-height:38px;padding:8px 12px;border-radius:8px;border:1px solid var(--accent);color:var(--accent);text-decoration:none;background:#fff}",
        "a.button.primary{background:var(--accent);color:#fff}",
        ".latest{margin-top:18px;padding:16px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}",
        ".metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px}",
        ".metric{border:1px solid var(--line);border-radius:8px;padding:12px;background:#fbfaf7}.metric b{display:block;font-size:24px}.metric span{color:var(--muted);font-size:13px}",
        ".list{display:grid;gap:8px;margin-top:18px}.row{display:grid;grid-template-columns:120px 1fr auto;gap:12px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:8px;background:#fff}",
        ".date{font-weight:700;color:var(--ok)}.title a{color:var(--accent);text-decoration:none}.meta{color:var(--muted);font-size:13px;white-space:nowrap}",
        ".counter{margin-top:24px;text-align:center;color:var(--muted);font-size:13px}",
        "@media(max-width:720px){.metrics{grid-template-columns:1fr}.row{grid-template-columns:1fr}.meta{white-space:normal}}",
        "</style>",
        "</head>",
        "<body>",
        "<header><div class=\"wrap\">",
        "<h1>推理资讯周报归档</h1>",
        "<div class=\"sub\">GitHub Pages 静态展示页：最新一期、历史归档和机器可读索引。</div>",
        "<div class=\"toolbar\">",
        "<a class=\"button primary\" href=\"../index.html\">返回首页</a>",
        "<a class=\"button\" href=\"./latest.html\">打开最新周报</a>",
        "<a class=\"button\" href=\"./index.json\">查看 JSON 索引</a>",
        "</div>",
        "</div></header>",
        "<main class=\"wrap\">",
        "<section class=\"latest\">",
        "<h2>最新一期</h2>",
        f"<div class=\"title\"><a href=\"./latest.html\">{latest_title}</a></div>",
        f"<div class=\"sub\">发布日期：{latest_date} · 归档总数：{len(metadata)}</div>",
        "<div class=\"metrics\">",
        f"<div class=\"metric\"><b>{escape(latest_books_text)}</b><span>精选新书</span></div>",
        f"<div class=\"metric\"><b>{escape(latest_rss_text)}</b><span>精选资讯</span></div>",
        f"<div class=\"metric\"><b>{len(metadata)}</b><span>历史周报</span></div>",
        "</div>",
        "</section>",
        "<section class=\"list\">",
    ]
    if metadata:
        for item in metadata:
            date_text = escape(str(item.get("date") or ""))
            title = escape(str(item.get("title") or item.get("name") or ""))
            url = escape(str(item.get("url") or "#"))
            books = item.get("selected_books")
            rss = item.get("selected_rss")
            counts = []
            if books is not None:
                counts.append(f"{books} 本书")
            if rss is not None:
                counts.append(f"{rss} 条资讯")
            count_text = escape(" / ".join(counts) if counts else "统计未知")
            lines.append(
                f"<article class=\"row\"><div class=\"date\">{date_text}</div>"
                f"<div class=\"title\"><a href=\"{url}\">{title}</a></div>"
                f"<div class=\"meta\">{count_text}</div></article>"
            )
    else:
        lines.append("<article class=\"row\"><div>暂无归档内容</div></article>")

    lines.extend([
        "</section>",
        "<div class=\"counter\">",
        "<span id=\"busuanzi_container_site_pv\" style=\"display:none;\">站点总访问 <span id=\"busuanzi_value_site_pv\"></span> 次</span>",
        "</div>",
        "</main>",
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


def _build_publish_plan(
    project_root: Path,
    output_dir: Path,
    site_dir: Path,
    reports_subdir: str,
    latest_html: str = "",
) -> dict:
    reports_dir = site_dir / reports_subdir
    latest_report = Path(latest_html).resolve() if latest_html else _find_latest_report(output_dir)
    _validate_latest_report(latest_report, output_dir)
    archives = [
        file
        for file in output_dir.glob("*.html")
        if REPORT_RE.match(file.name)
    ]
    return {
        "project_root": project_root,
        "output_dir": output_dir,
        "site_dir": site_dir,
        "reports_dir": reports_dir,
        "latest_report": latest_report,
        "site_index": site_dir / "index.html",
        "latest_alias": reports_dir / "latest.html",
        "archived_report": reports_dir / latest_report.name,
        "archive_index": reports_dir / "index.html",
        "archive_manifest": reports_dir / "index.json",
        "archive_count": len(archives),
        "assets_dir": output_dir / "assets",
    }


def _print_publish_plan(plan: dict, dry_run: bool = False) -> None:
    prefix = "dry run: " if dry_run else ""
    print(f"{prefix}latest report: {plan['latest_report']}")
    print(f"{prefix}site index: {plan['site_index']}")
    print(f"{prefix}latest alias: {plan['latest_alias']}")
    print(f"{prefix}archived report: {plan['archived_report']}")
    print(f"{prefix}archive index: {plan['archive_index']}")
    print(f"{prefix}archive manifest: {plan['archive_manifest']}")
    print(f"{prefix}reports to sync: {plan['archive_count']}")
    print(f"{prefix}assets source: {plan['assets_dir']}")


def publish_latest(
    project_root: Path,
    output_dir: Path,
    site_dir: Path,
    reports_subdir: str = "reports",
    *,
    latest_html: str = "",
    dry_run: bool = False,
    git_push: bool = False,
) -> dict:
    """Publish the latest generated report into the static GitHub Pages layout."""
    plan = _build_publish_plan(project_root, output_dir, site_dir, reports_subdir, latest_html=latest_html)
    if dry_run:
        return {
            "status": "dry_run",
            "plan": plan,
            "latest_report": plan["latest_report"],
            "site_index": plan["site_index"],
            "latest_alias": plan["latest_alias"],
            "archived_report": plan["archived_report"],
            "archive_index": plan["archive_index"],
            "archive_manifest": plan["archive_manifest"],
            "archive_count": plan["archive_count"],
            "assets_dir": plan["assets_dir"],
            "git_push": git_push,
        }

    latest_report = plan["latest_report"]
    reports_dir = plan["reports_dir"]
    index_path = _copy_latest_as_index(latest_report, site_dir)
    latest_alias = _write_latest_alias(latest_report, reports_dir)
    archived_report = _archive_report(latest_report, reports_dir)
    synced_count = _sync_all_reports(output_dir, reports_dir)
    _copy_assets_if_exists(output_dir, site_dir)
    archives = _collect_archives(reports_dir)
    archive_manifest = _write_archive_manifest(reports_dir, archives, latest_alias)
    archive_index = _write_archive_index(reports_dir, archives)
    _inject_archive_link(index_path, f"./{reports_subdir}/index.html")

    if git_push:
        _run_git_publish(site_dir, latest_report.name)

    return {
        "status": "success",
        "plan": plan,
        "latest_report": latest_report,
        "site_index": index_path,
        "latest_alias": latest_alias,
        "archived_report": archived_report,
        "archive_index": archive_index,
        "archive_manifest": archive_manifest,
        "synced_reports": synced_count,
        "archives_total": len(archives),
        "git_push": git_push,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish weekly report to GitHub Pages with archives.")
    parser.add_argument("--project-root", default=".", help="project root path")
    parser.add_argument("--output-dir", default="output", help="report output directory")
    parser.add_argument("--site-dir", default=".", help="GitHub Pages source directory")
    parser.add_argument("--reports-subdir", default="reports", help="archive sub directory name")
    parser.add_argument("--latest-html", default="", help="specific latest report html path")
    parser.add_argument("--dry-run", action="store_true", help="show publish plan without writing files")
    parser.add_argument("--git-push", action="store_true", help="run git add/commit/push")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    site_dir = (project_root / args.site_dir).resolve()
    result = publish_latest(
        project_root,
        output_dir,
        site_dir,
        args.reports_subdir,
        latest_html=args.latest_html,
        dry_run=args.dry_run,
        git_push=args.git_push,
    )
    plan = result["plan"]

    if args.dry_run:
        _print_publish_plan(plan, dry_run=True)
        if args.git_push:
            print("dry run: skip git publish")
        return 0

    print(f"latest report: {result['latest_report']}")
    print(f"site index: {result['site_index']}")
    print(f"latest alias: {result['latest_alias']}")
    print(f"archived report: {result['archived_report']}")
    print(f"archive index: {result['archive_index']}")
    print(f"archive manifest: {result['archive_manifest']}")
    print(f"synced reports: {result['synced_reports']}")
    print(f"archives total: {result['archives_total']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"publish failed: {exc}")
        raise
