"""AI Downloader 启动入口（.pyw 无控制台窗口）。

用法：
    pythonw AIDownloader.pyw
也可以双击同目录的「启动 AI Downloader.bat」或快捷方式。
"""
from __future__ import annotations

import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

APP_TITLE = "AI Downloader"


def _set_dpi_awareness() -> None:
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 每显示器 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _report(exc: BaseException) -> None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from app import config as cfgmod

        cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (cfgmod.CONFIG_DIR / "error.log").write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, f"启动失败：\n\n{exc}\n\n详细信息见 error.log")
        root.destroy()
    except Exception:
        sys.stderr.write(text)


MUTEX_NAME = "AIDownloader_SingleInstance"


def _acquire_single_instance():
    """已有实例时返回 None，否则返回互斥体句柄（需保持引用）。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return None
        return handle
    except Exception:
        return True          # 无法判断时放行，不影响使用


def _focus_existing() -> bool:
    """把已在运行的窗口拉到前台并还原（最小化时）。"""
    try:
        import ctypes
        import time

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            return False
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return True
    except Exception:
        return False


def main() -> None:
    _set_dpi_awareness()
    guard = _acquire_single_instance()
    if guard is None:
        _focus_existing()
        return
    from app.main_window import run

    run()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:      # noqa: BLE001
        _report(exc)
        raise SystemExit(1)
