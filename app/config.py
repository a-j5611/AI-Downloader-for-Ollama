"""配置读写：JSON 持久化到 %APPDATA%\\AIDownloader\\config.json"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_DIR_NAME = "AIDownloader"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_DIR_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    "theme": "dark",              # dark | light
    "lang": "zh",                 # zh | en
    "host": "http://127.0.0.1:11434",
    "ollama_dir": r"D:\Ollama",
    "models_dir": r"D:\Ollama\models",
    "background": {
        "source": "",
        "preset": "midnight",
        "blur": 0,
        "dim": 35,
        "library": [],
    },
    "catalog": {
        "sort": "popular",        # popular | name | updated
        "cap": "",                # 能力过滤
        "include_community": True,  # 是否把社区模型并入目录
    },
    "library_history": [],      # 绑定过的下载位置（最近 6 个）
    "window": {"w": 1360, "h": 860, "x": None, "y": None},
    "download": {
        "pending_model": "",      # 上次未下载完的模型（中断后可续传）
        "threads": 2,             # 下载线程数（实测官方源 2 线程最快）
        "source": "",             # 下载源（空 = 官方 registry）
        "sources": [],            # 用户添加的镜像源 [{name, base}]
        "native": False,          # True = 用 Ollama 原生 pull
        "remember": True,         # 记住上次在弹窗里的选择
    },
    "aidb": {
        "tool": r"D:\Desktop\Code\CodeProject\OllamaModelDB\aidb.exe",
        "db": r"D:\Desktop\Code\CodeProject\OllamaModelDB\models.aidb",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    data = {}
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return _merge(DEFAULTS, data if isinstance(data, dict) else {})


def save(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def data_dir() -> Path:
    """缓存目录（模型目录、版本列表都放这里）。"""
    path = CONFIG_DIR / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ollama_exe(cfg: dict) -> str:
    return str(Path(cfg.get("ollama_dir", r"D:\Ollama")) / "ollama.exe")
