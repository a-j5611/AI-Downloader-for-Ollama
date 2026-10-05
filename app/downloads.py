"""下载管理：队列、进度、取消、断点续传、停滞检测、完整性核对。

这些结论都是踩过坑之后定下来的：
  * **失败绝不当作成功**：worker 正常跑完才算完成，并且完成后必须回查
    /api/tags 确认模型真的装上了 —— Ollama 分块账本损坏时会"返回成功但没装"。
  * **速度按增量算**：累计字节 ÷ 总耗时是平均速度，续传时还会把上次已下载的
    部分算进来，显示会虚高；这里用 RateMeter（采样增量 + 指数平滑）。
  * **分片保留**：中断后分片留在 blobs/*-partial*，下次拉取由 Ollama 续传；
    同时把待续传的模型写进配置，重启后仍能在界面上"继续下载"。
  * **停滞检测**：180 秒无字节进展就提示，并给出"清理并重下"入口
    （停服务 → 删损坏分片 → 起服务 → 重下），用来脱离 Ollama 的死锁状态。
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .ollama_api import RateMeter, human_size

STALL_SECONDS = 180


class Download:
    """一次下载任务。"""

    def __init__(self, name: str, tag: str, threads: int = 0, base: str = "",
                 mode: str = "mirror"):
        self.name = name
        self.tag = tag
        self.full = f"{name}:{tag}" if tag else name
        self.threads = max(1, int(threads or 1))
        self.base = base
        self.mode = mode if base else "native"      # mirror=自研多线程；native=Ollama 原生
        self.engine = None
        self.state = "queued"        # queued | running | done | failed | cancelled
        self.done = 0
        self.total = 0
        self.status = ""
        self.rate = 0.0
        self.error = ""
        self.started = 0.0
        self.finished = 0.0
        self.layers: set[str] = set()
        self.meter = RateMeter()
        self.last_progress = time.time()
        self.stalled = False

    @property
    def percent(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 0.0

    @property
    def elapsed(self) -> float:
        end = self.finished or time.time()
        return max(0.0, end - self.started) if self.started else 0.0

    def eta_text(self) -> str:
        if self.rate > 0 and self.total > self.done:
            secs = (self.total - self.done) / self.rate
            if secs < 60:
                return f"{int(secs)} 秒"
            if secs < 3600:
                return f"{int(secs // 60)} 分 {int(secs % 60)} 秒"
            return f"{secs / 3600:.1f} 小时"
        return ""

    def progress_text(self, t) -> str:
        if self.state == "queued":
            return t("dl.queued")
        if self.state == "running":
            parts = [f"{human_size(self.done)} / {human_size(self.total)}"
                     if self.total else t("dl.starting")]
            if self.rate > 0:
                parts.append(f"{human_size(self.rate)}/s")
            if self.eta_text():
                parts.append(t("dl.eta", self.eta_text()))
            if len(self.layers) > 1:
                parts.append(t("dl.layers", len(self.layers)))
            if self.stalled:
                parts.append(t("dl.stalled_short"))
            return "  ".join(parts)
        if self.state == "done":
            return t("dl.done_at", human_size(self.total or self.done), self.elapsed)
        if self.state == "failed":
            return self.error or t("dl.failed")
        return t("dl.cancelled")


class DownloadManager:
    """串行下载队列（Ollama 同时只跑一个拉取最稳）。"""

    def __init__(self, app):
        self.app = app
        self.queue: deque[Download] = deque()
        self.current: Download | None = None
        self.history: list[Download] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------ 对外
    def enqueue(self, name: str, tag: str = "latest", threads: int = 0,
                base: str = "", mode: str = "mirror", remember: bool = True,
                **extra) -> Download:
        """remember 等界面选项由调用方持久化，这里只取需要的字段。"""
        job = Download(name, tag, threads=threads, base=base, mode=mode)
        with self._lock:
            active = list(self.queue) + ([self.current] if self.current else [])
            for existing in active:
                if existing and existing.full == job.full and existing.state in (
                        "queued", "running"):
                    return existing
            self.queue.append(job)
        self.app.cfg.setdefault("download", {})["pending_model"] = job.full
        self.app.save_cfg()
        self.app.log_event("INFO", f"queued download: {job.full}")
        self._wake.set()
        self.app.on_download_event("queued", job)
        return job

    def cancel(self, job: Download):
        if job.state == "running":
            job.state = "cancelled"
            self._cancel.set()
            if job.engine is not None:
                try:
                    job.engine.cancel()
                except Exception:
                    pass
        elif job.state == "queued":
            job.state = "cancelled"
            with self._lock:
                if job in self.queue:
                    self.queue.remove(job)
        self.app.log_event("INFO", f"cancel download: {job.full}")
        self.app.on_download_event("cancelled", job)

    def cancel_all(self):
        for job in list(self.queue):
            self.cancel(job)
        if self.current:
            self.cancel(self.current)

    def clear_finished(self):
        self.history = [j for j in self.history if j.state in ("queued", "running")]

    def active(self) -> list[Download]:
        out = [self.current] if self.current and self.current.state == "running" else []
        out += [j for j in self.queue if j.state == "queued"]
        return out

    def is_busy(self) -> bool:
        return bool(self.active())

    # ------------------------------------------------ 工作线程
    def _loop(self):
        while True:
            job = None
            with self._lock:
                while self.queue:
                    candidate = self.queue.popleft()
                    if candidate.state == "queued":
                        job = candidate
                        break
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            try:
                self._run(job)
            except Exception as exc:                 # 兜底，绝不让线程死掉
                job.state = "failed"
                job.error = str(exc)
                self.app.log_event("ERROR", f"download worker: {exc}")
                self.app.on_download_event("failed", job)

    def _run(self, job: Download):
        self.current = job
        job.state = "running"
        job.started = time.time()
        job.meter = RateMeter()
        job.last_progress = time.time()
        self._cancel.clear()
        self.app.on_download_event("started", job)

        client = self.app.client
        ok = False
        if job.mode == "mirror" and job.base:
            ok = self._run_mirror(job)
            self._finish(job, ok)
            return
        try:
            for chunk in client.pull_stream(job.full):
                if self._cancel.is_set():
                    break
                total = chunk.get("total") or 0
                done = chunk.get("completed") or 0
                digest = chunk.get("digest") or ""
                if digest:
                    job.layers.add(digest)
                if done and done != job.done:
                    job.last_progress = time.time()
                    job.stalled = False
                if total:
                    job.total, job.done = total, done
                    job.rate = job.meter.update(done, digest)
                status = chunk.get("status") or ""
                if status:
                    job.status = status
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]))
            ok = not self._cancel.is_set()
        except Exception as exc:
            job.error = str(exc)
            ok = False

        if ok:
            ok = self._verify_installed(job)

        self._finish(job, ok)

    def _finish(self, job: Download, ok: bool):
        """统一收尾：核实安装、更新状态、通知界面。"""
        job.finished = time.time()
        if job.state == "cancelled":
            self.app.on_download_event("cancelled", job)
        elif ok:
            job.state = "done"
            job.done = job.total or job.done
            self.app.cfg.setdefault("download", {})["pending_model"] = ""
            self.app.save_cfg()
            self.app.log_event("INFO", f"download done: {job.full}")
            self.app.on_download_event("done", job)
        else:
            job.state = "failed"
            self.app.log_event("ERROR", f"download failed: {job.full} - {job.error}")
            self.app.on_download_event("failed", job)

        self.history.append(job)
        del self.history[:-30]
        self.current = None

    def _run_mirror(self, job: Download) -> bool:
        """用自研多线程引擎从指定源下载（支持镜像与断点续传）。"""
        from .mirror_dl import MirrorDownloader

        self.app.log_event(
            "INFO", f"mirror download: {job.full} via {job.base} x{job.threads} threads")
        engine = MirrorDownloader(self.app, job.name, job.tag, job.base, job.threads)
        job.engine = engine
        last_done = {"v": -1}

        def tick():
            snap = engine.snapshot()
            job.total = snap["total"]
            job.done = snap["done"]
            job.rate = snap["rate"]
            job.status = snap["status"]
            if snap["done"] != last_done["v"]:
                last_done["v"] = snap["done"]
                job.last_progress = time.time()
                job.stalled = False

        try:
            ok = engine.run(tick)
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            return False
        if self._cancel.is_set():
            job.state = "cancelled"
            return False
        if not ok:
            job.error = engine.error or "下载失败"
            return False
        if not self._verify_installed(job):
            return False
        return True

    def _verify_installed(self, job: Download) -> bool:
        """回查 /api/tags 确认服务端真的装上了。"""
        try:
            names = [m.get("name") for m in (self.app.client.tags() or [])]
        except Exception:
            names = []
        if job.full not in names and job.name not in names:
            job.error = self.app.t("dl.not_installed")
            return False
        return True

    # ------------------------------------------------ 停滞检测（界面轮询调用）
    def check_stall(self) -> Download | None:
        job = self.current
        if not job or job.state != "running":
            return None
        if time.time() - job.last_progress > STALL_SECONDS:
            if not job.stalled:
                job.stalled = True
                self.app.log_event("WARN", f"download stalled: {job.full}")
            return job
        return None
