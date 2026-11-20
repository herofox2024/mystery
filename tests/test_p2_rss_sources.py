"""P2 回归测试：RSS 源可用性与配置编码。

对应缺陷：
1. config.yaml 里的 4 个新浪读书 RSS 源最新条目停留在 2005~2008 年，
   加上 days 窗口后永远抓不到任何内容，资讯栏目长期为 0；
2. 同一批 feed 的 name 字段在文件里被替换成了字面量 "????-??"（24 个 0x3F 字节），
   日志里一直显示乱码。

这些测试不依赖外网：只校验配置文件本身的形状、编码与关键词规则。
"""

import re
from pathlib import Path

import yaml

from app_helpers import ensure_defaults, filter_rss_by_rules, validate_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_PATH = PROJECT_ROOT / "config.yaml.example"

# 已确认失效的新浪读书源：最新条目停留在 2005~2008 年。
DEAD_SINA_HOSTS = ("rss.sina.com.cn",)


def _load_config():
    return ensure_defaults(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


# --- 缺陷 1：RSS 源 -------------------------------------------------------


def test_no_dead_sina_feeds_remain():
    """失效的新浪读书源必须全部移除。"""
    cfg = _load_config()
    urls = [feed.get("url", "") for feed in cfg["rss"]["feeds"]]

    for url in urls:
        assert not any(host in url for host in DEAD_SINA_HOSTS), f"仍存在失效源: {url}"


def test_rss_feeds_are_configured_and_valid():
    cfg = _load_config()
    feeds = cfg["rss"]["feeds"]

    assert feeds, "rss.feeds 不能为空，否则资讯栏目永远是 0 条"
    assert validate_config(cfg) == []

    for feed in feeds:
        assert str(feed.get("url", "")).startswith("http"), f"feed url 非法: {feed}"
        assert str(feed.get("name", "")).strip(), f"feed 缺少 name: {feed}"


def test_rss_feed_names_are_readable_chinese_or_ascii():
    """name 不能是乱码占位符。"""
    cfg = _load_config()

    for feed in cfg["rss"]["feeds"]:
        name = str(feed.get("name", ""))
        assert "??" not in name, f"feed name 疑似乱码: {name!r}"
        assert re.search(r"[\w\u4e00-\u9fff]", name), f"feed name 无有效字符: {name!r}"


# --- 缺陷 2：配置编码 -----------------------------------------------------


def test_config_files_have_no_replacement_question_marks():
    """config.yaml 里 4 个 feed 名称曾被替换为 "????-??"，共 24 个 0x3F 字节。"""
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        raw = path.read_bytes()
        # 0x3F 本身是合法的 '?' 字符，这里只针对「连续 2 个以上」的占位符形态。
        assert b"??" not in raw, f"{path.name} 存在乱码占位符"
        text = raw.decode("utf-8")
        assert "\ufffd" not in text, f"{path.name} 存在 UTF-8 解码替换字符"


def test_config_files_are_valid_utf8_and_parse():
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        raw = path.read_bytes()
        text = raw.decode("utf-8")  # 解码失败会直接抛异常
        assert isinstance(yaml.safe_load(text), dict)


# --- 噪声过滤 -------------------------------------------------------------


def _rss_rules():
    return _load_config()["filter_rules"]


def test_non_book_rss_content_is_excluded():
    """接入英文源后，游戏/播客/影视/漫画等带 mystery 关键词的内容需被排除。"""
    rules = _rss_rules()
    noise = [
        {"title": "11 Best Noir Video Games of All Time", "summary": "crime fiction", "source": "rss:test"},
        {"title": "The Best Crime and Mystery Podcasts in 2027", "summary": "", "source": "rss:test"},
        {"title": "15 Best Spy Comics and Graphic Novels", "summary": "mystery", "source": "rss:test"},
        {"title": "A mystery thriller TV series adaptation", "summary": "", "source": "rss:test"},
    ]

    assert filter_rss_by_rules(noise, rules) == []


def test_book_relevant_rss_content_is_kept():
    """真正的推理书讯必须保留，不能被噪声规则误伤。"""
    rules = _rss_rules()
    books = [
        {
            "title": "7 mysteries that focus on the motive behind crimes",
            "summary": "crime fiction roundup",
            "source": "rss:Mystery Tribune",
        },
        {
            "title": "Elly Griffiths' 5 favorite female detectives in fiction",
            "summary": "detective novels",
            "source": "rss:CrimeReads",
        },
    ]

    kept = filter_rss_by_rules(books, rules)

    assert len(kept) == 2
