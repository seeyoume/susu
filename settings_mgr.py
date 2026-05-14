"""统一设置持久化 - 全部 UI 配置存到 accounts/_settings.json"""
import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "accounts" / "_settings.json"

_cache = None


def load():
    global _cache
    if _cache is not None:
        return _cache
    if not SETTINGS_FILE.exists():
        _cache = {}
        return _cache
    try:
        _cache = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}
    return _cache


def save_all(data=None):
    global _cache
    if data is not None:
        _cache = data
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    try:
        SETTINGS_FILE.write_text(
            json.dumps(_cache or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def get(key, default=None):
    return load().get(key, default)


def set_value(key, value):
    cfg = load()
    cfg[key] = value
    save_all(cfg)
