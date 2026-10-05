"""多线程 / 镜像下载引擎。

为什么自己实现下载：
  * Ollama 的 /api/pull 只能单连接、且固定走官方 registry，速度受单连接限速制约
    （实测单连接 0.28 MB/s，2 连接聚合 0.62 MB/s）；
  * registry 支持 HTTP Range（实测返回 206），所以可以分段多线程；
  * 允许换镜像源（自建反代 / 其它可用的 registry 代理）以绕开慢链路。

工作方式：
  1. 从 <源>/v2/<命名空间>/<模型>/manifests/<标签> 取清单（含每个分片的 digest 与大小）；
  2. 每个分片按 <线程数> 切成若干段并行下载（支持断点续传，段文件按已下长度续写）；
  3. 合并后校验 sha256，写入 Ollama 的 blobs/sha256-<digest>；
  4. 最后写入 manifests 目录，Ollama 随即认为该模型已安装。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AIDownloader/1.0")

MIN_CHUNK = 2 << 20          # 单个分段最小 2MB，太小会浪费连接
PART_SUFFIX = ".part"
READ_TIMEOUT = 90            # 单次连接读超时（网络抖动时给足时间）
MAX_RETRY = 4                # 每段最多重试次数（每次从已下载字节续传）
RETRY_WAIT = (1, 2, 4, 8)    # 重试前的退避秒数

# 下载源预设：name / 地址前缀（形如 https://host，路径沿用 registry 的 /v2/...）
DEFAULT_SOURCES = [
    {"name": "官方 registry.ollama.ai", "base": "https://registry.ollama.ai"},
]


class MirrorError(Exception):
    pass


def _request(url: str, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def split_url(name: str) -> tuple[str, str]:
    """把 full 名字拆成 (命名空间, 模型)。社区模型形如 用户名/模型。"""
    if "/" in name:
        ns, _, model = name.rpartition("/")
        return ns, model
    return "library", name


def manifest_path(base: str, name: str, tag: str) -> str:
    ns, model = split_url(name)
    return f"{base.rstrip('/')}/v2/{ns}/{model}/manifests/{tag}"


def blob_path(base: str, name: str, digest: str) -> str:
    """registry v2 的分片路径必须带完整仓库名：/v2/<命名空间>/<模型>/blobs/<摘要>"""
    ns, model = split_url(name)
    return f"{base.rstrip('/')}/v2/{ns}/{model}/blobs/{digest}"


def fetch_manifest(base: str, name: str, tag: str, timeout: float = 30) -> dict:
    try:
        with _request(manifest_path(base, name, tag), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise MirrorError(f"该源没有 {name}:{tag}（404）") from exc
        raise MirrorError(f"清单请求失败 HTTP {exc.code}") from exc
    except Exception as exc:
        raise MirrorError(f"清单请求失败 {type(exc).__name__}") from exc


def probe_speed(base: str, name: str = "smollm", tag: str = "135m",
                seconds: float = 6.0) -> dict:
    """给下载源测速：下几秒取平均速率，并检测是否支持 Range。"""
    started = time.time()
    try:
        man = fetch_manifest(base, name, tag, timeout=15)
        layers = [l for l in (man.get("layers") or []) if l.get("size")]
        if not layers:
            return {"ok": False, "error": "清单里没有分片", "base": base}
        layer = max(layers, key=lambda l: l.get("size", 0))
        url = blob_path(base, name, layer["digest"])
        # 先探 Range
        supports_range = False
        try:
            with _request(url, {"Range": "bytes=0-1048575"}, timeout=15) as resp:
                supports_range = (resp.status == 206)
                head = resp.read(1 << 20)
        except Exception:
            head = b""
        total = len(head)
        t0 = time.time()
        with _request(url, timeout=20) as resp:
            while time.time() - t0 < seconds:
                block = resp.read(1 << 20)
                if not block:
                    break
                total += len(block)
        dt = max(time.time() - t0, 0.2)
        return {"ok": True, "base": base, "range": supports_range,
                "rate": total / dt, "sample": total,
                "elapsed": time.time() - started}
    except MirrorError as exc:
        return {"ok": False, "error": str(exc), "base": base}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}", "base": base}


class LayerJob:
    """一个分片的下载任务（多段并行）。"""

    def __init__(self, url: str, digest: str, size: int, dest: Path, threads: int):
        self.url = url
        self.digest = digest
        self.size = size
        self.dest = dest
        self.threads = max(1, threads)
        self.done = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = ""

    # ------------------------------------------------ 分段计划
    def _parts(self) -> list[tuple[int, int]]:
        """返回 [(开始, 长度)]；按线程数均分，单段不小于 MIN_CHUNK。"""
        if self.threads <= 1 or self.size < MIN_CHUNK * 2:
            return [(0, self.size)]
        count = min(self.threads, max(1, self.size // MIN_CHUNK))
        step = self.size // count
        out = []
        for i in range(count):
            start = i * step
            length = step if i < count - 1 else self.size - start
            out.append((start, length))
        return out

    def part_file(self, index: int, start: int) -> Path:
        return self.dest.with_name(self.dest.name + f"{PART_SUFFIX}{index}-{start}")

    # ------------------------------------------------ 单段
    def _fetch_once(self, index: int, start: int, length: int) -> bool:
        """下载一次；返回是否已拿满该段。失败时保留 .part 供下次续传。"""
        path = self.part_file(index, start)
        have = path.stat().st_size if path.exists() else 0
        if have >= length:
            with self.lock:
                self.done += length
            return True
        headers = {"Range": f"bytes={start + have}-{start + length - 1}"}
        mode = "ab" if have else "wb"
        with _request(self.url, headers, timeout=READ_TIMEOUT) as resp:
            if have and resp.status != 206:
                have = 0                  # 服务端不支持续传：从头来过
                mode = "wb"
            with io.open(path, mode) as fh:
                got = have
                while got < length and not self.stop.is_set():
                    block = resp.read(min(1 << 20, length - got))
                    if not block:
                        break
                    fh.write(block)
                    got += len(block)
                    with self.lock:
                        self.done += len(block)
        return path.stat().st_size >= length

    def _fetch_part(self, index: int, start: int, length: int):
        """带重试的分段下载：网络抖动/超时后从断点继续，最多 MAX_RETRY 次。"""
        last = ""
        for attempt in range(MAX_RETRY + 1):
            if self.stop.is_set():
                return
            try:
                if self._fetch_once(index, start, length):
                    return
                last = "连接提前结束"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            if attempt >= MAX_RETRY or self.stop.is_set():
                break
            wait = RETRY_WAIT[min(attempt, len(RETRY_WAIT) - 1)]
            with self.lock:
                self.error = f"{last}（第 {attempt + 1} 次失败，{wait}s 后续传）"
            time.sleep(wait)
        self.error = last
        raise RuntimeError(last)

    # ------------------------------------------------ 执行
    def run(self, on_tick=None) -> bool:
        parts = self._parts()
        # 复位计数（续传时把已存在的段算进来）
        with self.lock:
            self.done = 0
        for idx, (start, length) in enumerate(parts):
            path = self.part_file(idx, start)
            if path.exists():
                with self.lock:
                    self.done += min(path.stat().st_size, length)

        errors: list[Exception] = []

        def worker(idx, start, length):
            try:
                self._fetch_part(idx, start, length)
            except Exception as exc:                 # 单段失败不影响其它段
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i, s, l), daemon=True)
                   for i, (s, l) in enumerate(parts)]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            time.sleep(0.25)
            if on_tick:
                on_tick()
        for t in threads:
            t.join(timeout=1)
        if errors and self.error:
            return False
        return not self.stop.is_set()

    # ------------------------------------------------ 合并 + 校验
    def merge(self, progress=None) -> tuple[bool, str]:
        """把各段顺序拼成最终文件并校验 sha256。"""
        expect = self.digest.split(":")[-1]
        digest = hashlib.sha256()
        parts = self._parts()
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        with io.open(self.dest, "wb") as out:
            for idx, (start, length) in enumerate(parts):
                path = self.part_file(idx, start)
                if not path.exists() or path.stat().st_size != length:
                    return False, "分段文件不完整"
                with io.open(path, "rb") as fh:
                    while True:
                        block = fh.read(1 << 22)
                        if not block:
                            break
                        out.write(block)
                        digest.update(block)
        actual = digest.hexdigest()
        if actual != expect:
            return False, f"校验不匹配（{actual[:12]} ≠ {expect[:12]}）"
        for idx, (start, _l) in enumerate(parts):
            try:
                self.part_file(idx, start).unlink()
            except Exception:
                pass
        return True, ""


class MirrorDownloader:
    """把某个 模型:标签 下载到本地 Ollama 目录。"""

    def __init__(self, app, name: str, tag: str, base: str, threads: int):
        self.app = app
        self.name = name
        self.tag = tag
        self.base = base.rstrip("/")
        self.threads = max(1, min(int(threads), 32))
        self.models_dir = Path(app.cfg.get("models_dir") or "")
        self.blobs = self.models_dir / "blobs"
        self.stop = threading.Event()
        self.total = 0
        self.done = 0
        self.rate = 0.0
        self.status = "准备中"
        self.error = ""
        self._lock = threading.Lock()
        self._t0 = time.time()

    # ------------------------------------------------ 路径
    def manifest_file(self) -> Path:
        ns, model = split_url(self.name)
        return self.models_dir / "manifests" / "registry.ollama.ai" / ns / model / self.tag

    # ------------------------------------------------ 主流程
    def run(self, on_tick=None) -> bool:
        try:
            self.status = "获取清单"
            man = fetch_manifest(self.base, self.name, self.tag)
        except MirrorError as exc:
            self.error = str(exc)
            return False

        layers = [l for l in (man.get("layers") or []) if l.get("size")]
        cfg = man.get("config") or {}
        if cfg.get("digest"):
            layers = layers + [cfg]
        self.total = sum(int(l.get("size") or 0) for l in layers)
        if not layers:
            self.error = "清单里没有可分片下载的内容"
            return False

        self.blobs.mkdir(parents=True, exist_ok=True)
        for layer in sorted(layers, key=lambda l: -int(l.get("size") or 0)):
            if self.stop.is_set():
                self.error = "已取消"
                return False
            digest = layer.get("digest") or ""
            size = int(layer.get("size") or 0)
            dest = self.blobs / f"sha256-{digest.split(':')[-1]}"
            if dest.exists() and dest.stat().st_size == size:
                self.done += size                  # 已有同分片（多个模型共享）
                self.status = "跳过已存在的分片"
                continue
            self.status = f"下载分片 {digest[7:19]}…"
            url = blob_path(self.base, self.name, digest)
            job = LayerJob(url, digest, size, dest, self.threads)
            base_done = self.done
            meter_prev = (time.time(), 0.0)

            def tick(job=job, base_done=base_done):
                nonlocal meter_prev
                now = time.time()
                with self._lock:
                    self.done = base_done + job.done
                dt = now - meter_prev[0]
                if dt >= 0.5:
                    instant = (job.done - meter_prev[1]) / dt
                    self.rate = instant if self.rate <= 0 else 0.4 * instant + 0.6 * self.rate
                    meter_prev = (now, job.done)
                if on_tick:
                    on_tick()

            if not job.run(tick):
                self.error = job.error or "下载中断"
                return False
            self.status = "校验分片"
            ok, why = job.merge(tick)
            if not ok:
                self.error = f"分片 {digest[7:19]} {why}"
                return False

        # 写清单：Ollama 看到它即认为该模型已安装
        try:
            self.status = "写入清单"
            target = self.manifest_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(man, ensure_ascii=False).encode("utf-8")
            target.write_bytes(raw)
        except Exception as exc:
            self.error = f"写入清单失败 {type(exc).__name__}"
            return False
        self.status = "完成"
        return True

    def cancel(self):
        self.stop.set()

    def snapshot(self) -> dict:
        return {"total": self.total, "done": self.done, "rate": self.rate,
                "status": self.status, "error": self.error,
                "percent": (self.done / self.total) if self.total else 0.0}
