"""AI 目录：抓取 Ollama 的**全部**模型与其**全部版本**。

数据来源（ollama.com 公开页面 / registry）：
  * 全量模型   https://ollama.com/library            一页返回全部官方模型（实测 242 个）
  * 补充来源   https://ollama.com/search             社区/最新模型（每页 20 条，翻页无效）
                https://ollama.com/api/tags          官方最新列表（JSON）
  * 版本列表   https://ollama.com/library/<模型>/tags  全部标签（如 qwen3 → 58 个）
  * 精确体积   https://registry.ollama.ai/v2/library/<模型>/manifests/<版本>
  * 社区模型   https://registry.ollama.ai/v2/<命名空间>/<模型>/manifests/<版本>

抓一次的结果会缓存到磁盘（catalog.json / tags_<模型>.json），
离线也能浏览，界面里可手动刷新。
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AIDownloader/1.0")

LIBRARY_URL = "https://ollama.com/library"
TAGS_URL = "https://ollama.com/library/{}/tags"
MANIFEST_URL = "https://registry.ollama.ai/v2/library/{}/manifests/{}"
MANIFEST_URL_NS = "https://registry.ollama.ai/v2/{}/manifests/{}"
TAGS_CACHE_TTL = 6 * 3600
CATALOG_TTL = 6 * 3600

CAP_LABELS = {
    "tools": ("工具", "Tools"),
    "thinking": ("思考", "Thinking"),
    "vision": ("视觉", "Vision"),
    "embedding": ("嵌入", "Embedding"),
    "audio": ("音频", "Audio"),
    "cloud": ("云端", "Cloud"),
}
SORTS = ("popular", "name", "updated")


class CatalogError(Exception):
    pass


# ============================================================ 网络
def _get(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _parse_pulls(text: str) -> int:
    match = re.search(r"([\d.]+)\s*([KMB]?)\s*(?:&nbsp;)?\s*Pulls?", text, re.I)
    if not match:
        return 0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").upper()
    return int(value * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[unit])


def _parse_sizes(text: str) -> list[str]:
    sizes: list[str] = []
    for match in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([bBmM])(?![\w])", text):
        size = f"{match.group(1)}{match.group(2).lower()}"
        if size not in sizes:
            sizes.append(size)
    return sizes[:8]


def parse_library(page: str, source: str = "library") -> list[dict]:
    """解析模型列表页（library 与 search 的卡片结构相同）。"""
    items: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="/library/([A-Za-z0-9._\-]+)"', page):
        name = match.group(1)
        if ":" in name or name in seen:
            continue
        seen.add(name)
        chunk = page[match.end(): match.end() + 3000]
        desc = ""
        dm = re.search(r'<p class="[^"]*break-words[^"]*">(.*?)</p>', chunk, re.S)
        if dm:
            desc = _strip_tags(dm.group(1))
        updated = ""
        um = re.search(r"(\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago)", chunk)
        if um:
            updated = um.group(1)
        caps = sorted(set(re.findall(
            r">\s*(tools|vision|thinking|embedding|audio|cloud)\s*<", chunk)))
        plain = _strip_tags(chunk)
        items.append({
            "name": name, "description": desc, "updated": updated, "caps": caps,
            "sizes": _parse_sizes(plain[:600]), "pulls": _parse_pulls(plain),
            "source": source,
        })
    return items


def parse_tags(page: str, model: str) -> list[str]:
    """解析版本页，拿到该模型的**全部**标签（qwen3 实测 58 个）。"""
    tags: list[str] = []
    for match in re.finditer(
            rf'href="/library/{re.escape(model)}:([A-Za-z0-9._\-]+)"', page):
        tag = match.group(1)
        if tag not in tags:
            tags.append(tag)
    if not tags:
        for match in re.finditer(rf"{re.escape(model)}:([A-Za-z0-9._\-]+)", page):
            tag = match.group(1)
            if tag not in tags and len(tag) < 40:
                tags.append(tag)
    if "latest" in tags:
        tags.remove("latest")
        tags.insert(0, "latest")
    return tags


def parse_tag_sizes(page: str, model: str) -> dict:
    """版本页里每个标签的体积文本（如 4.9GB），用于列表秒显。"""
    sizes: dict[str, str] = {}
    for match in re.finditer(
            rf'href="/library/{re.escape(model)}:([A-Za-z0-9._\-]+)"', page):
        tag = match.group(1)
        chunk = page[match.end(): match.end() + 900]
        sm = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|KB))", chunk)
        if sm and tag not in sizes:
            sizes[tag] = sm.group(1).replace(" ", "")
    return sizes


# ============================================================ 目录
class Catalog:
    """全量模型目录（带磁盘缓存）。"""

    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.catalog_file = self.dir / "catalog.json"
        self.models: list[dict] = []
        self.fetched_at: float = 0.0
        self.source_note: str = ""
        self._lock = threading.Lock()
        self.load_cache()

    # ------------------------------------------------ 缓存
    def load_cache(self) -> bool:
        try:
            data = json.loads(self.catalog_file.read_text(encoding="utf-8"))
            self.models = data.get("models") or []
            self.fetched_at = float(data.get("fetched_at") or 0)
            self.source_note = data.get("note") or ""
            return bool(self.models)
        except Exception:
            return False

    def save_cache(self):
        try:
            self.catalog_file.write_text(json.dumps({
                "fetched_at": self.fetched_at, "note": self.source_note,
                "models": self.models}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def is_stale(self) -> bool:
        return (not self.models) or (time.time() - self.fetched_at > CATALOG_TTL)

    def age_text(self) -> str:
        if not self.fetched_at:
            return ""
        secs = max(0.0, time.time() - self.fetched_at)
        if secs < 90:
            return f"{int(secs)} 秒前"
        if secs < 5400:
            return f"{int(secs // 60)} 分钟前"
        return f"{secs / 3600:.1f} 小时前"

    # ------------------------------------------------ 抓取
    def refresh(self, want_community: bool = False) -> dict:
        """抓全量目录。

        提速要点（实测）：
          * 社区补充页翻页无效、且当前只多出 0 个模型却要多花约 3 秒 → 默认跳过；
          * library 与 api/tags **并发**抓取，把两段等待重叠起来；
          * 目录页 811KB 无法压缩（服务端不返回 gzip），所以靠 6 小时缓存 + 后台静默刷新。
        """
        from concurrent.futures import ThreadPoolExecutor

        collected: dict[str, dict] = {}
        note: list[str] = []
        errors: list[str] = []

        def fetch_library():
            return parse_library(_get(LIBRARY_URL, timeout=45))

        def fetch_featured():
            try:
                data = json.loads(_get("https://ollama.com/api/tags", timeout=20))
            except Exception:
                return []
            out = []
            for model in data.get("models", []):
                name = (model.get("name") or "").split(":")[0]
                if name:
                    out.append({"name": name, "description": "", "updated": "",
                                "caps": [], "sizes": [model.get("name", "").split(":")[-1]],
                                "pulls": 0, "source": "featured"})
            return out

        jobs = {"library": fetch_library, "featured": fetch_featured}
        if want_community:
            jobs["community"] = lambda: parse_library(
                _get("https://ollama.com/search", timeout=25), source="community")

        results: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {key: pool.submit(fn) for key, fn in jobs.items()}
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except Exception as exc:
                    results[key] = []
                    errors.append(f"{key}:{type(exc).__name__}")

        lib = results.get("library") or []
        if not lib and self.models:
            raise CatalogError("；".join(errors) + "（已保留上次目录）")
        if not lib:
            raise CatalogError("；".join(errors) or "目录为空")
        for item in lib:
            collected[item["name"]] = item
        note.append(f"library {len(lib)}")

        for key in ("community", "featured"):
            added = 0
            for item in results.get(key) or []:
                if item["name"] not in collected:
                    collected[item["name"]] = item
                    added += 1
            if added or key == "featured":
                note.append(f"{key} +{added}")

        models = sorted(collected.values(), key=lambda m: m["name"].lower())
        with self._lock:
            self.models = models
            self.fetched_at = time.time()
            self.source_note = " · ".join(note)
        self.save_cache()
        return {"count": len(models), "note": self.source_note, "errors": errors}

    # ------------------------------------------------ 查询
    def filter(self, query: str = "", cap: str = "", sort: str = "popular",
               source: str = "") -> list[dict]:
        text = (query or "").strip().lower()
        out = []
        for item in self.models:
            if cap and cap not in (item.get("caps") or []):
                continue
            if source and item.get("source") != source:
                continue
            if text:
                hay = " ".join([
                    item.get("name", ""), item.get("description", ""),
                    " ".join(item.get("sizes") or []),
                    " ".join(item.get("caps") or []),
                ]).lower()
                if text not in hay:
                    continue
            out.append(item)
        if sort == "name":
            out.sort(key=lambda m: m.get("name", "").lower())
        elif sort == "updated":
            out.sort(key=lambda m: (m.get("updated") or "zzzz", m.get("name", "")))
        else:
            out.sort(key=lambda m: (-(m.get("pulls") or 0), m.get("name", "").lower()))
        return out

    def prefetch_tags(self, names: list[str], limit: int = 24):
        """后台预取热门模型的版本列表，界面上点开就是秒显。"""
        from concurrent.futures import ThreadPoolExecutor

        todo = []
        for name in names[:limit]:
            cache = self.tags_cache_file(name)
            if cache.exists():
                try:
                    data = json.loads(cache.read_text(encoding="utf-8"))
                    if time.time() - float(data.get("fetched_at") or 0) < TAGS_CACHE_TTL:
                        continue
                except Exception:
                    pass
            todo.append(name)
        if not todo:
            return 0
        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for _ in pool.map(lambda n: self._safe_tags(n), todo):
                done += 1
        return done

    def _safe_tags(self, name: str):
        try:
            return self.get_tags(name)
        except Exception:
            return None

    def prefetch_sizes(self, name: str, tags: list[str], limit: int = 12):
        """并发取前若干版本的精确体积（列表里先显示版本页的体积，精确值随后补上）。"""
        from concurrent.futures import ThreadPoolExecutor

        targets = tags[:limit]

        def one(tag):
            cache = self.size_cache_file(name, tag)
            if cache.exists():
                return None
            info = self.tag_info(name, tag)
            if info.get("size"):
                try:
                    cache.write_text(str(info["size"]), encoding="utf-8")
                except Exception:
                    pass
            return (tag, info["size"])

        out = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            for res in pool.map(one, targets):
                if res:
                    out.append(res)
        return out

    def size_cache_file(self, name: str, tag: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}_{tag}")
        return self.dir / f"size_{safe}.txt"

    def cached_size(self, name: str, tag: str) -> int:
        cache = self.size_cache_file(name, tag)
        try:
            if cache.exists():
                return int(cache.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            pass
        return 0

    def has(self, name: str) -> bool:
        return any(m.get("name") == name for m in self.models)

    def upsert(self, model: dict):
        with self._lock:
            for idx, item in enumerate(self.models):
                if item.get("name") == model.get("name"):
                    self.models[idx] = {**item, **model}
                    break
            else:
                self.models.append(model)
            self.models.sort(key=lambda m: m.get("name", "").lower())

    # ------------------------------------------------ 版本
    def tags_cache_file(self, model: str) -> Path:
        return self.dir / f"tags_{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.json"

    def get_tags(self, model: str, force: bool = False) -> dict:
        cache = self.tags_cache_file(model)
        if not force and cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - float(data.get("fetched_at") or 0) < TAGS_CACHE_TTL:
                    return data
            except Exception:
                pass
        page = _get(TAGS_URL.format(model), timeout=30)
        data = {"model": model, "tags": parse_tags(page, model),
                "sizes": parse_tag_sizes(page, model), "fetched_at": time.time()}
        try:
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return data

    # ------------------------------------------------ registry
    def manifest_url(self, name: str, tag: str) -> str:
        if "/" in name:
            return MANIFEST_URL_NS.format(name, tag)
        return MANIFEST_URL.format(name, tag)

    def tag_info(self, name: str, tag: str) -> dict:
        """精确体积 + 量化方式 + 是否带视觉工程文件（带磁盘缓存）。"""
        cached = self.cached_size(name, tag)
        info = {"size": cached, "quant": "", "layers": 0, "vision": False}
        if cached:
            return info
        try:
            data = json.loads(_get(self.manifest_url(name, tag), timeout=25))
        except Exception:
            return info
        layers = data.get("layers") or []
        info["layers"] = len(layers)
        info["size"] = (sum(int(l.get("size", 0)) for l in layers)
                        + int((data.get("config") or {}).get("size", 0) or 0))
        for layer in layers:
            media = layer.get("mediaType", "")
            if "projector" in media or "mmproj" in media:
                info["vision"] = True
            match = re.search(
                r"(q\d+_[A-Za-z0-9_]+|q\d+_\d|f16|bf16|f32|iq\d+[A-Za-z0-9_]*)", media)
            if match and not info["quant"]:
                info["quant"] = match.group(1)
        return info


# ============================================================ 本地已安装
def installed_map(client) -> dict:
    out = {}
    try:
        for model in client.tags():
            name = model.get("name") or ""
            if not name:
                continue
            details = model.get("details") or {}
            out[name] = {
                "size": int(model.get("size") or 0),
                "digest": (model.get("digest") or "")[:12],
                "modified": model.get("modified_at") or "",
                "family": details.get("family") or "",
                "params": details.get("parameter_size") or "",
                "quant": details.get("quantization_level") or "",
            }
    except Exception:
        pass
    return out


def installed_state(installed: dict, name: str, tag: str) -> str:
    """'' / 'exact'（已装该版本）/ 'base'（装了同模型其它版本）"""
    if f"{name}:{tag}" in installed:
        return "exact"
    if any(key.split(":")[0] == name for key in installed):
        return "base"
    return ""
