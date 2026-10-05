"""模型库位置：绑定下载目录，并自动识别该目录里已经存在的模型。

为什么需要这个：
  * Ollama 只认一个 `OLLAMA_MODELS` 目录。换了目录，原来的模型就"看不见"了，
    但其实文件还在磁盘上（换回来又能用）。
  * 有些模型是别的工具/别的安装留下的，服务没加载，界面也应该认得出来。

所以这里做的事：
  1. **扫描任意目录**：读 `manifests/**`，把模型、版本、体积、量化、完整性都列出来
     （即便当前服务用的不是这个目录）；
  2. **探测正在运行的服务用的是哪个目录**：从 Ollama 的 server.log 里读
     `server config` 那行里的 `OLLAMA_MODELS`；
  3. 给出**差异**：磁盘上有什么、服务加载了什么、绑定的又是哪个。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_SUBDIRS = (
    r"D:\Ollama\models",
    r"C:\Ollama\models",
    r"E:\Ollama\models",
)


def normalize(path: str | Path) -> str:
    """统一成可比较的形式（大小写、分隔符、结尾斜杠）。"""
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    text = text.replace("/", "\\").rstrip("\\")
    return text.lower()


def same_dir(a: str | Path, b: str | Path) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def server_log_paths() -> list[Path]:
    out = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Ollama" / "server.log")
    out.append(Path.home() / ".ollama" / "logs" / "server.log")
    return out


def running_models_dir() -> str:
    """从 Ollama 服务日志里读出运行中的模型目录（读不到返回空串）。"""
    for log in server_log_paths():
        try:
            if not log.exists():
                continue
            # 只读尾部，日志可能很大
            size = log.stat().st_size
            with log.open("rb") as fh:
                fh.seek(max(0, size - 400_000))
                tail = fh.read().decode("utf-8", "replace")
        except Exception:
            continue
        hits = re.findall(r"OLLAMA_MODELS:(\S+)", tail)
        if hits:
            value = hits[-1].strip().strip('"')
            # 日志里是转义过的（D:\\Ollama\\models）
            return value.replace("\\\\", "\\")
    return ""


def candidate_dirs(configured: str = "") -> list[str]:
    """列出可能的模型目录，供界面下拉/提示使用。"""
    out: list[str] = []

    def add(path: str):
        if not path:
            return
        p = str(path).strip()
        if p and not any(same_dir(p, x) for x in out):
            out.append(p)

    add(os.environ.get("OLLAMA_MODELS", ""))
    add(configured)
    add(running_models_dir())
    add(Path.home() / ".ollama" / "models")
    for p in DEFAULT_SUBDIRS:
        add(p)
    # 只保留真实存在的，或者配置里指定的那个
    keep = [p for p in out if os.path.isdir(p) or same_dir(p, configured)]
    return keep


class ModelLibrary:
    """一个模型库目录（Ollama 的 models 目录）。"""

    def __init__(self, path: str | Path):
        self._raw = str(path or "").strip()
        self.path = Path(self._raw) if self._raw else Path("")
        self.error = ""
        self.models: dict[str, dict] = {}     # "deepseek-r1:1.5b" -> 信息
        self.tags = 0
        self.total_size = 0
        self.blobs_size = 0
        self.missing = 0                      # 引用了但磁盘上缺文件的层数
        self.scanned = False

    # ------------------------------------------------ 目录属性
    @property
    def blobs_dir(self) -> Path:
        return self.path / "blobs"

    @property
    def manifests_dir(self) -> Path:
        return self.path / "manifests"

    def exists(self) -> bool:
        return bool(self._raw) and self.path.is_dir()

    def looks_valid(self) -> bool:
        return self.exists() and (self.blobs_dir.is_dir() or self.manifests_dir.is_dir())

    def writable(self) -> bool:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            probe = self.path / ".aidl_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except Exception:
            return False

    def summary(self) -> str:
        if not self._raw:
            return "尚未绑定下载位置"
        if not self.exists():
            return "目录不存在"
        if not self.looks_valid():
            return "不是 Ollama 模型目录（缺少 blobs/ 或 manifests/）"
        return (f"{self.model_count} 个模型 · {len(self.models)} 个版本 · "
                f"{human(self.total_size)}")

    # ------------------------------------------------ 扫描
    def scan(self, deep_size: bool = False) -> dict:
        """扫描目录；deep_size=True 时额外统计 blobs 目录实际占用。"""
        self.models = {}
        self.model_count = 0
        self.tags = 0
        self.total_size = 0
        self.missing = 0
        self.error = ""
        self.scanned = True

        if not self.exists():
            self.error = "目录不存在"
            return self.status()
        if not self.manifests_dir.is_dir():
            self.error = "没有 manifests 目录"
            return self.status()

        seen_names: set[str] = set()
        for manifest in self.manifests_dir.rglob("*"):
            try:
                if not manifest.is_file():
                    continue
            except OSError:
                continue
            info = self._read_manifest(manifest)
            if not info:
                continue
            name = info["full"]
            self.models[name] = info
            self.total_size += info["size"]
            self.missing += info["missing_layers"]
            seen_names.add(info["name"])

        self.model_count = len(seen_names)
        self.tags = len(self.models)
        if deep_size:
            self.blobs_size = self._dir_size(self.blobs_dir)
        return self.status()

    def _dir_size(self, path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def _read_manifest(self, manifest: Path) -> dict | None:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        # 路径 -> 名字：manifests/<registry>/<仓库...>/<标签>
        try:
            rel = manifest.relative_to(self.manifests_dir)
        except ValueError:
            return None
        parts = list(rel.parts)
        if len(parts) < 2:
            return None
        tag = parts[-1]
        repo_parts = parts[1:-1]
        repo = "/".join(repo_parts)
        if repo.startswith("library/"):
            repo = repo[len("library/"):]
        name = repo or tag
        full = f"{name}:{tag}"

        size = 0
        missing = 0
        details = {}
        layers = data.get("layers") or []
        cfg = data.get("config") or {}
        for layer in list(layers) + ([cfg] if cfg else []):
            layer_size = int(layer.get("size") or 0)
            size += layer_size
            digest = (layer.get("digest") or "").split(":")[-1]
            if digest and not (self.blobs_dir / f"sha256-{digest}").exists():
                missing += 1
        # 从 config 里取模型信息
        cfg_digest = (cfg.get("digest") or "").split(":")[-1]
        if cfg_digest:
            try:
                config_blob = self.blobs_dir / f"sha256-{cfg_digest}"
                if config_blob.exists() and config_blob.stat().st_size < 1 << 20:
                    info = json.loads(config_blob.read_text(encoding="utf-8", errors="replace"))
                    details = {
                        "family": info.get("model_family") or "",
                        "params": info.get("model_type") or "",
                        "quant": info.get("file_type") or "",
                        "format": info.get("model_format") or "",
                    }
            except Exception:
                details = {}
        try:
            modified = manifest.stat().st_mtime
        except OSError:
            modified = 0
        return {
            "full": full, "name": name, "tag": tag, "repo": repo,
            "manifest": str(manifest), "size": size, "missing_layers": missing,
            "modified": modified, "layers": len(layers), **details,
        }

    # ------------------------------------------------ 结果
    def status(self) -> dict:
        return {
            "path": str(self.path),
            "exists": self.exists(),
            "valid": self.looks_valid(),
            "error": self.error,
            "models": self.models,
            "versions": len(self.models),
            "model_count": self.model_count,
            "total_size": self.total_size,
            "blobs_size": self.blobs_size,
            "missing": self.missing,
        }

    def disk_map(self) -> dict:
        """给界面用的 {name: {size, params, quant, family, on_disk}}（与 installed_map 同构）。"""
        out = {}
        for full, info in self.models.items():
            out[full] = {
                "size": info["size"],
                "digest": "",
                "modified": "",
                "family": info.get("family", ""),
                "params": info.get("params", ""),
                "quant": info.get("quant", ""),
                "on_disk": True,
                "complete": info.get("missing_layers", 0) == 0,
            }
        return out


def human(num_bytes: float) -> str:
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return "-"
