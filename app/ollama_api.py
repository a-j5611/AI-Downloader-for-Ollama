"""Ollama 接口封装与服务进程管理（纯标准库 + ctypes，无第三方依赖）。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from ctypes import wintypes

from . import config as cfgmod

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

PROC_NAMES = ("ollama.exe", "llama-server.exe", "ollama app.exe")


class OllamaError(Exception):
    pass


# ============================================================ HTTP 客户端
class OllamaClient:
    def __init__(self, host: str = "http://127.0.0.1:11434"):
        self.host = (host or "http://127.0.0.1:11434").rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def _json(self, path: str, method: str = "GET", payload: dict | None = None, timeout: float = 10):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self._url(path), data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except Exception:
                pass
            raise OllamaError(detail or f"HTTP {exc.code}") from None
        except Exception as exc:
            raise OllamaError(str(exc)) from None
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    # -------------------------------------------------- 基础接口
    def version(self, timeout: float = 3) -> dict:
        return self._json("/api/version", timeout=timeout) or {}

    def is_up(self, timeout: float = 2) -> bool:
        try:
            self.version(timeout=timeout)
            return True
        except Exception:
            return False

    def tags(self) -> list:
        return (self._json("/api/tags", timeout=20) or {}).get("models", []) or []

    def ps(self) -> list:
        return (self._json("/api/ps", timeout=10) or {}).get("models", []) or []

    def delete(self, name: str) -> None:
        self._json("/api/delete", method="DELETE", payload={"model": name}, timeout=60)

    def show(self, name: str) -> dict:
        return self._json("/api/show", method="POST", payload={"model": name}, timeout=30) or {}

    def capabilities(self, name: str) -> list:
        """模型能力，例如 ['completion','tools','thinking','vision']。"""
        try:
            caps = self.show(name).get("capabilities")
            return list(caps) if isinstance(caps, list) else []
        except Exception:
            return []

    def has_vision(self, name: str) -> bool:
        return "vision" in self.capabilities(name)

    def tag_digests(self) -> dict:
        """{模型名: 清单 digest}"""
        out = {}
        try:
            for model in self.tags():
                if model.get("name"):
                    out[model["name"]] = model.get("digest", "")
        except Exception:
            pass
        return out

    # -------------------------------------------------- 流式接口
    def _stream(self, path: str, payload: dict, timeout: float = 900):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url(path), data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # 把服务端的真实原因读出来（否则只看到 "HTTP Error 500"）
            detail = ""
            try:
                body = exc.read().decode("utf-8", "replace")
                detail = (json.loads(body) or {}).get("error") or body
            except Exception:
                detail = ""
            raise OllamaError(detail.strip() or f"HTTP {exc.code}") from None
        try:
            for raw in resp:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def pull_stream(self, name: str):
        """产出 {'status','completed','total','digest','error'}"""
        return self._stream("/api/pull", {"model": name, "stream": True}, timeout=3600)

    def chat_stream(self, model: str, messages: list, options: dict | None = None):
        payload = {"model": model, "messages": messages, "stream": True}
        if options:
            payload["options"] = options
        return self._stream("/api/chat", payload, timeout=1800)

    def warmup(self, model: str, timeout: float = 600) -> None:
        """让服务端先把模型加载好。

        刚切换模型时立刻发对话请求，服务端要同时卸载旧模型、加载新模型，
        偶发 500（内部调度冲突/内存瞬时不足）。先预热一次可以避免。
        """
        try:
            self._json("/api/generate",
                       method="POST",
                       payload={"model": model, "prompt": "", "stream": False,
                                "keep_alive": "5m", "options": {"num_predict": 1}},
                       timeout=timeout)
        except Exception:
            pass


# ============================================================ Windows 进程信息
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _psapi():
    return ctypes.WinDLL("psapi", use_last_error=True)


def enumerate_processes() -> list[tuple[int, str]]:
    """返回 [(pid, exe_name), ...]"""
    out: list[tuple[int, str]] = []
    try:
        k32 = _k32()
        TH32CS_SNAPPROCESS = 0x00000002
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return out
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        try:
            ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                out.append((int(entry.th32ProcessID), str(entry.szExeFile)))
                ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snapshot)
    except Exception:
        pass
    return out


def _proc_mem_cpu(pid: int):
    """返回 (内存MB, CPU秒)；失败返回 (0.0, 0.0)"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        k32 = _k32()
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 0.0, 0.0
        try:
            mem_mb = 0.0
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
            if _psapi().GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                mem_mb = counters.WorkingSetSize / (1024 * 1024)

            creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
            cpu = 0.0
            if k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                   ctypes.byref(kernel), ctypes.byref(user)):
                def to_sec(ft):
                    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
                cpu = to_sec(kernel) + to_sec(user)
            return mem_mb, cpu
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return 0.0, 0.0


# ============================================================ 服务管理
class ServiceManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._cpu_prev: dict[int, tuple[float, float]] = {}
        self._started_pid: int | None = None

    # -------------------------------------------------- 状态
    def client(self) -> OllamaClient:
        return OllamaClient(self.cfg.get("host", "http://127.0.0.1:11434"))

    def is_up(self, timeout: float = 2) -> bool:
        return self.client().is_up(timeout=timeout)

    # -------------------------------------------------- 启停
    def exe_path(self) -> str:
        return cfgmod.ollama_exe(self.cfg)

    def start(self) -> int:
        exe = self.exe_path()
        if not os.path.exists(exe):
            raise OllamaError(f"NOT_FOUND::{exe}")
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(self.cfg.get("models_dir") or "")
        env["OLLAMA_IGPU_ENABLE"] = "0"
        flags = CREATE_NO_WINDOW
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
        log_path = os.path.join(str(self.cfg.get("ollama_dir", "")), "serve.err.log")
        try:
            stderr = open(log_path, "ab", buffering=0)
            stdout = stderr
        except Exception:
            stdout = stderr = subprocess.DEVNULL
        proc = subprocess.Popen(
            [exe, "serve"],
            cwd=str(self.cfg.get("ollama_dir") or None),
            env=env,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
        )
        self._started_pid = proc.pid
        return proc.pid

    def stop(self) -> None:
        for name in ("ollama app.exe", "ollama.exe", "llama-server.exe"):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", name],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
            except Exception:
                pass
        self._cpu_prev.clear()

    def restart(self) -> int:
        self.stop()
        time.sleep(1.5)
        return self.start()

    def wait_up(self, timeout: float = 45) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_up(timeout=1.5):
                return True
            time.sleep(0.8)
        return False

    # -------------------------------------------------- 进程信息
    def list_procs(self) -> list[dict]:
        ncpu = os.cpu_count() or 1
        now = time.time()
        rows: list[dict] = []
        for pid, name in enumerate_processes():
            if name.lower() not in PROC_NAMES:
                continue
            mem_mb, cpu_s = _proc_mem_cpu(pid)
            prev = self._cpu_prev.get(pid)
            cpu_pct = 0.0
            if prev is not None:
                d_cpu = cpu_s - prev[0]
                d_wall = max(now - prev[1], 1e-6)
                if d_cpu >= 0:
                    cpu_pct = max(0.0, min(100.0, d_cpu / (d_wall * ncpu) * 100.0))
            self._cpu_prev[pid] = (cpu_s, now)
            rows.append({"name": name, "pid": pid, "mem_mb": mem_mb, "cpu": cpu_pct})
        rows.sort(key=lambda r: (-r["mem_mb"], r["name"]))
        return rows

    @staticmethod
    def dir_size(path: str) -> int:
        total = 0
        try:
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except Exception:
            pass
        return total


# ============================================================ 小工具
class RateMeter:
    """按采样增量估算「当前」速率。

    不能直接用 累计字节/总耗时：
      * 续传时累计字节包含上次已下载的部分，会算出虚高的速度；
      * 那是平均速度，与任务栏显示的网络瞬时速率对不上。
    这里用两次采样的增量除以间隔并做指数平滑；分片切换（digest 变化）时自动重置。
    """

    def __init__(self, alpha: float = 0.4, min_dt: float = 0.4):
        self.alpha = alpha
        self.min_dt = min_dt
        self._key = None
        self._bytes = None
        self._time = None
        self.rate = 0.0

    def update(self, completed: float, key: str = "", now: float | None = None) -> float:
        now = time.time() if now is None else now
        completed = float(completed or 0)
        if key != self._key:                 # 换分片了，进度会归零，重置
            self._key = key
            self._bytes, self._time = completed, now
            self.rate = 0.0
            return 0.0
        if self._bytes is None:
            self._bytes, self._time = completed, now
            return 0.0
        dt = now - self._time
        if dt < self.min_dt:                 # 采样过密，沿用上次结果
            return self.rate
        delta = completed - self._bytes
        self._bytes, self._time = completed, now
        if delta < 0:                        # 进度回退，视为重置
            self.rate = 0.0
            return 0.0
        instant = delta / dt
        self.rate = instant if self.rate <= 0 else (
            self.alpha * instant + (1 - self.alpha) * self.rate)
        return self.rate

    def reset(self):
        self._key = None
        self._bytes = None
        self._time = None
        self.rate = 0.0


def human_size(num_bytes: float) -> str:
    try:
        num_bytes = float(num_bytes)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024.0
    return "-"


def read_tail(path: str, max_bytes: int = 200_000) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()
            data = fh.read()
        return data.decode("utf-8", "replace")
    except Exception:
        return ""
