"""主窗口：背景画布 + 圆角卡片布局 + 侧边导航 + 顶栏 + 视图切换 + 状态轮询。"""
from __future__ import annotations

import datetime as _dt
import os
import queue
import sys
import threading
import tkinter as tk

from . import APP_NAME, __version__
from . import config as cfgmod
from . import theme as th
from .backgrounds import BackgroundManager, HAS_PIL
from . import i18n
from .catalog import Catalog, installed_map
from .library import ModelLibrary, human as lib_human, running_models_dir, same_dir
from .downloads import DownloadManager
from .i18n import set_lang, t as T
from .ollama_api import OllamaClient, ServiceManager
from .panels import LogsView, ModelsView, ServiceView, SettingsView
from .views_catalog import CatalogView
from .widgets import (ProgressBar, PillButton, ScrollFrame, StatusDot, round_rect,
                      style_scrollbar, text_width)

def parse_size(text: str) -> int:
    """把 "6.1GB" 这样的文本转成字节数（仅用于弹窗展示）。"""
    import re as _re

    match = _re.match(r"\s*([\d.]+)\s*([KMGT]?)B?\s*$", (text or "").upper())
    if not match:
        return 0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0
    unit = match.group(2)
    return int(value * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
                        "T": 1024 ** 4}.get(unit, 1))


M = 22          # 外边距（留出背景可见区域）
GAP = 14        # 卡片间距
NAV_W = 238     # 侧栏宽度
TOP_H = 64      # 顶栏高度
PAD = 7         # 内容与卡片边缘的间隙（露出圆角）
RADIUS = 16


# ============================================================ 事件日志
class EventLog:
    def __init__(self, cap: int = 800):
        self.cap = cap
        self.items: list[str] = []

    def add(self, level: str, message: str) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self.items.append(f"[{stamp}] {level:<5} {message}")
        if len(self.items) > self.cap:
            self.items = self.items[-self.cap:]

    def text(self) -> str:
        return "\n".join(self.items)

    def clear(self) -> None:
        self.items.clear()


# ============================================================ 图标
def draw_icon(cv: tk.Canvas, kind: str, cx: float, cy: float, color: str, s: float = 16) -> None:
    w = 2
    if kind == "chat":
        cv.create_rectangle(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 6,
                            outline=color, width=w)
        cv.create_line(cx - s / 6, cy + s / 6, cx - s / 6, cy + s / 2, fill=color, width=w)
        cv.create_line(cx - s / 6, cy + s / 2, cx + s / 8, cy + s / 6, fill=color, width=w)
    elif kind == "models":
        for i in range(3):
            top = cy - s / 2 + i * (s / 3)
            cv.create_rectangle(cx - s / 2, top, cx + s / 2, top + s / 5,
                                outline=color, width=w)
    elif kind == "service":
        cv.create_arc(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2, start=35, extent=290,
                      style="arc", outline=color, width=w)
        cv.create_line(cx, cy - s / 2 - 1, cx, cy, fill=color, width=w)
    elif kind == "settings":
        for i, frac in enumerate((0.3, 0.65, 0.45)):
            y = cy - s / 2 + i * (s / 2.4)
            cv.create_line(cx - s / 2, y, cx + s / 2, y, fill=color, width=w)
            cv.create_oval(cx - s / 2 + frac * s - 2.5, y - 2.5,
                           cx - s / 2 + frac * s + 2.5, y + 2.5, fill=color, outline="")
    elif kind == "logs":
        for i, frac in enumerate((1.0, 0.7, 0.85, 0.5)):
            y = cy - s / 2 + i * (s / 3.2)
            cv.create_line(cx - s / 2, y, cx - s / 2 + s * frac, y, fill=color, width=w)


# ============================================================ 侧栏导航项
class NavItem(tk.Canvas):
    def __init__(self, parent, key: str, icon: str, text: str, command=None,
                 width: int = NAV_W - 2 * PAD - 8, height: int = 44):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0,
                         bg=th.c("card"))
        self.key = key
        self.icon = icon
        self._text = text
        self._command = command
        self._active = False
        self._hover = False
        self._cw, self._ch = width, height
        self.bind("<Button-1>", lambda _e: self._command and self._command(self.key))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._redraw()

    def _on_enter(self, _e=None):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._redraw()

    def set_active(self, active: bool):
        if active != self._active:
            self._active = active
            self._redraw()

    def set_text(self, text: str):
        self._text = text
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w, h = self._cw, self._ch
        if self._active:
            round_rect(self, 0, 2, w, h - 2, 10, fill=th.c("chip"), outline="")
            self.create_rectangle(0, h / 2 - 9, 3, h / 2 + 9, fill=th.c("accent"), outline="")
        elif self._hover:
            round_rect(self, 0, 2, w, h - 2, 10, fill=th.c("card_hover"), outline="")
        color = th.c("accent") if self._active else (
            th.c("text") if self._hover else th.c("text_muted"))
        draw_icon(self, self.icon, 24, h / 2, color, 15)
        self.create_text(48, h / 2 + 1, text=self._text, anchor="w", fill=color,
                         font=th.F(10, "bold" if self._active else "normal"))

    def apply_theme(self, bg: str):
        self.configure(bg=bg)
        self._redraw()


# ============================================================ 状态轮询
class StatusPoller(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            host = self.app.cfg.get("host", "http://127.0.0.1:11434")
            client = OllamaClient(host)
            try:
                info = client.version(timeout=2.5)
                self.app.status = "ok"
                self.app.server_version = info.get("version", "")
            except Exception:
                self.app.status = "err"
                self.app.server_version = ""
            self._stop.wait(3.0)


# ============================================================ 主窗口
class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = cfgmod.load()
        set_lang(self.cfg.get("lang", "zh"))
        th.set_mode(self.cfg.get("theme", "dark"))

        self.event_log = EventLog()
        self.bg = BackgroundManager(self.cfg)
        self.svc = ServiceManager(self.cfg)
        self.status = "busy"
        self.server_version = ""
        self.current_view_key = "catalog"
        self._results: queue.Queue = queue.Queue()
        self._bg_photo = None
        self._card_rects: list = []
        self.catalog = Catalog(cfgmod.data_dir())
        self.downloads = DownloadManager(self)
        self.installed: dict = {}
        self.library = ModelLibrary(self.cfg.get("models_dir") or "")
        self.disk_models: dict = {}
        self.server_dir: str = ""
        self._dl_events: list = []
        self._dl_dirty = False

        self.title(APP_NAME)
        self.configure(bg=th.c("root"))
        self.minsize(980, 660)
        self._setup_geometry()
        th.init_fonts(self)
        style_scrollbar(self._style())

        self._build_widgets()
        self._bind_events()
        self._apply_all_theme()
        self.show_view("catalog")

        self.run_async(self._probe_status, None)
        self._poller = StatusPoller(self)
        self._poller.start()
        self.after(120, self._pump)
        self.after(700, self._tick)
        self.after(300, self.refresh_installed)
        self.after(400, self.scan_library)
        self.after(1200, lambda: self.views["catalog"].on_show())
        self.event_log.add("INFO", f"{APP_NAME} {__version__} started")
        self.event_log.add("INFO", f"models dir: {self.cfg.get('models_dir')}")

    # ------------------------------------------------ 初始化
    def _style(self):
        from tkinter import ttk

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        return style

    def _setup_geometry(self):
        win = self.cfg.get("window", {})
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(win.get("w") or 1360), sw - 60)
        h = min(int(win.get("h") or 860), sh - 80)
        x = win.get("x")
        y = win.get("y")
        if x is None or y is None:
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
        else:
            # 位置保护：窗口被拖到屏幕外时（多显示器拔掉、坐标异常）重新摆回可见区域
            vx, vy = self.winfo_vrootx(), self.winfo_vrooty()
            vw, vh = self.winfo_vrootwidth(), self.winfo_vrootheight()
            x = max(vx - w + 140, min(int(x), vx + vw - 140))
            y = max(vy, min(int(y), vy + vh - 90))
        self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self._apply_icon()

    def _apply_icon(self):
        if not HAS_PIL:
            return
        try:
            from PIL import Image, ImageDraw, ImageTk

            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill="#2f6feb")
            draw.ellipse((20, 16, 44, 40), fill="#ffffff")
            draw.rectangle((28, 34, 36, 50), fill="#ffffff")
            self._icon_photo = ImageTk.PhotoImage(img)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_widgets(self):
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=th.c("root"))
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.nav_frame = tk.Frame(self, bg=th.c("card"), bd=0, highlightthickness=0)
        self.top_frame = tk.Frame(self, bg=th.c("card"), bd=0, highlightthickness=0)
        self.content = tk.Frame(self, bg=th.c("card"), bd=0, highlightthickness=0)

        # ---- 侧栏
        head = tk.Frame(self.nav_frame, bg=th.c("card"))
        head.pack(fill="x", padx=8, pady=(14, 10))
        self.app_title = tk.Label(head, text=APP_NAME, bg=th.c("card"), fg=th.c("text"),
                                  font=th.F(11, "bold"), anchor="w")
        self.app_title.pack(fill="x")
        self.app_sub = tk.Label(head, text=T("app.subtitle"), bg=th.c("card"),
                                fg=th.c("text_faint"), font=th.F(8), anchor="w")
        self.app_sub.pack(fill="x", pady=(2, 0))

        self.nav_items: dict[str, NavItem] = {}
        for key, icon in (("catalog", "models"), ("installed", "models"),
                          ("service", "service"), ("settings", "settings"),
                          ("logs", "logs")):
            item = NavItem(self.nav_frame, key, icon, T(f"nav.{key}"), command=self.show_view)
            item.pack(fill="x", padx=4, pady=2)
            self.nav_items[key] = item

        self.nav_footer = tk.Frame(self.nav_frame, bg=th.c("card"))
        self.nav_footer.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self.nav_dot = StatusDot(self.nav_footer, "busy", 10, bg=th.c("card"))
        self.nav_dot.pack(side="left", padx=(0, 6))
        self.nav_status = tk.Label(self.nav_footer, text=T("top.offline"), bg=th.c("card"),
                                   fg=th.c("text_faint"), font=th.F(8), anchor="w")
        self.nav_status.pack(side="left")
        self.ver_label = tk.Label(self.nav_footer, text=f"v{__version__}", bg=th.c("card"),
                                  fg=th.c("text_faint"), font=th.F(8))
        self.ver_label.pack(side="right")

        # ---- 下载指示条（侧栏底部，任何视图都能看到进度）
        self.dl_box = tk.Frame(self.nav_frame, bg=th.c("card_alt"), padx=10, pady=8)
        self.dl_head = tk.Frame(self.dl_box, bg=th.c("card_alt"))
        self.dl_head.pack(fill="x")
        self.dl_name = tk.Label(self.dl_head, text="", bg=th.c("card_alt"),
                                fg=th.c("text"), font=th.F(8, "bold"), anchor="w")
        self.dl_name.pack(side="left", fill="x", expand=True)
        self.dl_cancel = tk.Label(self.dl_head, text="✕", bg=th.c("card_alt"),
                                  fg=th.c("text_faint"), font=th.F(9), cursor="hand2")
        self.dl_cancel.pack(side="right")
        self.dl_cancel.bind("<Button-1>", lambda _e: self.cancel_current_download())
        self.dl_bar = ProgressBar(self.dl_box, height=5, bg=th.c("card_alt"))
        self.dl_bar.pack(fill="x", pady=(5, 3))
        self.dl_info = tk.Label(self.dl_box, text="", bg=th.c("card_alt"),
                                fg=th.c("text_faint"), font=th.F(7), anchor="w",
                                justify="left", wraplength=190)
        self.dl_info.pack(fill="x")

        # ---- 顶栏
        self.view_title = tk.Label(self.top_frame, text="", bg=th.c("card"), fg=th.c("text"),
                                   font=th.F(15, "bold"))
        self.view_title.pack(side="left", padx=(4, 14))
        self.model_chip = PillButton(self.top_frame, "",
                                     command=lambda: self.show_view("installed"),
                                     kind="ghost", height=32, radius=9, font=th.F(9))
        self.model_chip.pack(side="left")

        self.theme_btn = PillButton(self.top_frame, "", command=self.toggle_theme, kind="ghost",
                                    height=32, radius=9, min_width=64)
        self.theme_btn.pack(side="right")
        self.lang_btn = PillButton(self.top_frame, "", command=self.toggle_lang, kind="ghost",
                                   height=32, radius=9, min_width=64)
        self.lang_btn.pack(side="right", padx=(0, 8))
        self.bg_btn = PillButton(self.top_frame, "", command=self._quick_background, kind="ghost",
                                 height=32, radius=9)
        self.bg_btn.pack(side="right", padx=(0, 8))
        self.dot = StatusDot(self.top_frame, "busy", 12, bg=th.c("card"))
        self.dot.pack(side="right", padx=(0, 10))
        self.status_label = tk.Label(self.top_frame, text=T("top.offline"), bg=th.c("card"),
                                     fg=th.c("text_muted"), font=th.F(9))
        self.status_label.pack(side="right")

        # ---- 内容区
        self.views: dict[str, tk.Frame] = {}
        self.views["catalog"] = CatalogView(self.content, self)
        self.views["installed"] = ModelsView(self.content, self)
        self.views["service"] = ServiceView(self.content, self)
        self.views["settings"] = SettingsView(self.content, self)
        self.views["logs"] = LogsView(self.content, self)

        self.toast_label = tk.Label(self.content, text="", bg=th.c("card_alt"), fg=th.c("text"),
                                    font=th.F(9), padx=14, pady=8)

    def _bind_events(self):
        self.bind("<Configure>", self._on_configure)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._relayout_job = None

    # ------------------------------------------------ 布局
    def _on_configure(self, event):
        if event.widget is not self:
            return
        if self._relayout_job:
            self.after_cancel(self._relayout_job)
        self._relayout_job = self.after(60, self.relayout)

    def relayout(self):
        self._relayout_job = None
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 50 or h < 50:
            return
        left = M + NAV_W + GAP
        self._card_rects = [
            (M, M, M + NAV_W, h - M),
            (left, M, w - M, M + TOP_H),
            (left, M + TOP_H + GAP, w - M, h - M),
        ]
        self._render_background()
        self._draw_cards()

        self.nav_frame.place(x=M + PAD, y=M + PAD,
                             width=NAV_W - 2 * PAD, height=h - 2 * M - 2 * PAD)
        self.top_frame.place(x=left + PAD, y=M + PAD,
                             width=w - M - left - 2 * PAD, height=TOP_H - 2 * PAD)
        self.content.place(x=left + PAD, y=M + TOP_H + GAP + PAD,
                           width=w - M - left - 2 * PAD,
                           height=h - M - (M + TOP_H + GAP) - 2 * PAD)

    def _render_background(self):
        size = (self.winfo_width(), self.winfo_height())
        photo = self.bg.render(size)
        self.canvas.delete("bgimg")
        if photo is not None:
            self._bg_photo = photo
            self.canvas.create_image(0, 0, image=photo, anchor="nw", tags="bgimg")
            self.canvas.tag_lower("bgimg")
            self._bg_warned = None
        else:
            self._bg_photo = None
            self.canvas.configure(bg=th.c("root"))
            # 背景图读取失败时给出一次提示（否则界面只是静默变成纯色）
            src = self.bg.source
            if src and size[0] > 40 and size[1] > 40 and getattr(self, "_bg_warned", None) != src:
                self._bg_warned = src
                self.event_log.add("WARN", f"background image could not be loaded: {src}")
                self.toast(T("set.bg_load_failed", src))

    def _draw_cards(self):
        self.canvas.delete("card")
        for (x1, y1, x2, y2) in self._card_rects:
            round_rect(self.canvas, x1 + 2, y1 + 4, x2 + 2, y2 + 4, RADIUS,
                       fill=th.c("shadow"), outline="", tags="card")
            round_rect(self.canvas, x1, y1, x2, y2, RADIUS,
                       fill=th.c("card"), outline="", tags="card")
        # 背景图在 _render_background 里已置于最底层；
        # 若再执行 tag_lower("card", "bgimg") 反而会把卡片压到壁纸下面，
        # 使卡片描边那一圈露出壁纸（看似杂散笔画）。
        if self.canvas.find_withtag("bgimg"):
            self.canvas.tag_raise("card", "bgimg")

    def _on_wheel(self, event):
        widget = None
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return None
        while widget is not None:
            if getattr(widget, "_is_scroll_area", False):
                widget.scroll_by(-1 if event.delta > 0 else 1)
                return "break"
            widget = getattr(widget, "master", None)
        return None

    # ------------------------------------------------ 视图
    def show_view(self, key: str):
        if key not in self.views:
            return
        for view in self.views.values():
            view.pack_forget()
        view = self.views[key]
        view.pack(fill="both", expand=True)
        self.current_view_key = key
        for nav_key, item in self.nav_items.items():
            item.set_active(nav_key == key)
        self.view_title.configure(text=T(f"nav.{key}"))
        if hasattr(view, "on_show"):
            try:
                view.on_show()
            except Exception as exc:
                self.event_log.add("ERROR", f"on_show {key}: {exc}")
        self.event_log.add("INFO", f"view -> {key}")

    def _quick_background(self):
        self.show_view("settings")

    # ------------------------------------------------ 已安装 / 下载
    def refresh_installed(self):
        """刷新「已安装」缓存，并让目录页据此标注已下载状态。"""
        def work():
            return installed_map(self.client)

        def done(result, error):
            if error or result is None:
                result = {}
            # 合并磁盘扫描结果：服务没加载但磁盘上存在的模型也要认出来
            merged = dict(self.disk_models)
            for key, value in result.items():
                if key in merged:
                    merged[key] = {**merged[key], **value, "on_disk": True,
                                   "served": True}
                else:
                    merged[key] = {**value, "served": True}
            self.installed = merged
            view = self.views.get("installed")
            if view is not None and hasattr(view, "refresh"):
                try:
                    view.refresh()
                except Exception:
                    pass
            catalog = self.views.get("catalog")
            if catalog is not None and hasattr(catalog, "refresh_downloads"):
                try:
                    catalog.refresh_downloads()
                except Exception:
                    pass

        self.run_async(work, done)

    def refresh_models_view(self):
        self.refresh_installed()

    # ------------------------------------------------ 下载位置（模型库绑定）
    def scan_library(self, notify: bool = False):
        """扫描绑定的下载位置 + 探测服务实际使用的目录。"""
        def work():
            lib = ModelLibrary(self.cfg.get("models_dir") or "")
            status = lib.scan()
            return {"lib": lib, "status": status, "server": running_models_dir()}

        def done(result, error):
            if error or not result:
                self.log_event("ERROR", f"scan library: {error}")
                return
            self.library = result["lib"]
            self.disk_models = self.library.disk_map()
            self.server_dir = result["server"]
            self.refresh_installed()
            for key in ("installed", "settings"):
                view = self.views.get(key)
                if view is not None and hasattr(view, "refresh_library"):
                    try:
                        view.refresh_library()
                    except Exception as exc:
                        self.log_event("ERROR", f"library view: {exc}")
            if notify:
                status = result["status"]
                self.toast(T("lib.scanned", status["model_count"],
                             lib_human(status["total_size"])))
            self.log_event(
                "INFO", f"library {self.library.path} -> {len(self.disk_models)} models, "
                        f"server dir = {self.server_dir or '?'}")

        self.run_async(work, done)

    def library_bound(self) -> str:
        return str(self.library.path) if self.library and self.library.exists() else ""

    def library_mismatch(self) -> bool:
        """服务实际用的目录与绑定的下载位置是否不一致。"""
        if not self.server_dir or not self.library_bound():
            return False
        return not same_dir(self.server_dir, self.library.path)

    def bind_library(self, path: str, restart: bool = True):
        """绑定下载位置；可选立刻重启服务让它生效。"""
        path = (path or "").strip()
        if not path:
            return
        lib = ModelLibrary(path)
        if not lib.exists():
            self.toast(T("lib.not_found", path))
            return
        if not lib.looks_valid():
            self.toast(T("lib.not_valid", path))
            return
        self.cfg["models_dir"] = path
        history = [p for p in (self.cfg.get("library_history") or [])
                   if not same_dir(p, path)]
        history.insert(0, path)
        self.cfg["library_history"] = history[:6]
        self.save_cfg()
        self.library = lib
        self.log_event("INFO", f"bind library -> {path}")
        self.toast(T("lib.bound", path))
        if restart:
            self.restart_service_for_library()
        else:
            self.scan_library(notify=True)
        for view in self.views.values():
            if hasattr(view, "refresh_library"):
                try:
                    view.refresh_library()
                except Exception:
                    pass

    def restart_service_for_library(self):
        """重启 Ollama，让它改用绑定的下载位置（OLLAMA_MODELS）。"""
        self.toast(T("lib.restarting"))
        svc = self.svc

        def work():
            try:
                svc.stop()
                time.sleep(1.5)
            except Exception as exc:
                self.log_event("WARN", f"stop service: {exc}")
            info = {}
            try:
                svc.start()
                info["up"] = svc.wait_up(45)
            except Exception as exc:
                info["error"] = str(exc)
                info["up"] = False
            return info

        def done(result, error):
            info = result or {}
            if info.get("up"):
                self.toast(T("lib.switched", self.cfg.get("models_dir", "")))
                self.log_event("INFO", f"service restarted with {self.cfg.get('models_dir')}")
            else:
                self.toast(T("lib.switch_failed", info.get("error", "timeout")))
                self.log_event("ERROR", f"restart for library failed: {info}")
            self.after(600, lambda: self.scan_library(notify=True))
            self.after(900, self.refresh_status_now)

        self.run_async(work, done)

    def on_download_event(self, kind: str, job):
        """下载管理器回调（在**后台线程**里触发）。

        Tk 只能在主线程操作，所以这里只把事件放进队列，
        真正的界面更新统一交给主线程的 _tick（每 1.5 秒）+ _pump 处理。
        """
        self._dl_dirty = True
        try:
            self._dl_events.append((kind, job))
        except Exception:
            pass

    def _drain_download_events(self):
        """主线程消费下载事件（完成提示、刷新列表）。"""
        events = getattr(self, "_dl_events", None)
        if not events:
            return
        while events:
            kind, job = events.pop(0)
            if kind == "done":
                self.toast(T("dl.done_toast", job.full))
                self.refresh_installed()
            elif kind == "failed":
                self.toast(job.error or T("dl.failed"))
            elif kind == "cancelled":
                self.toast(T("dl.cancelled"))
        self.refresh_installed()

    def cancel_current_download(self):
        job = self.downloads.current
        if job is not None:
            self.downloads.cancel(job)
            self.toast(T("dl.cancelled"))

    def _update_download_strip(self):
        """侧栏下载条 + 停滞提示（每 1.5 秒调用一次）。"""
        jobs = self.downloads.active()
        job = self.downloads.current if self.downloads.current and \
            self.downloads.current.state == "running" else None
        if job is None and jobs:
            job = jobs[0]
        if job is None:
            if self.dl_box.winfo_ismapped():
                self.dl_box.pack_forget()
            return
        if not self.dl_box.winfo_ismapped():
            self.dl_box.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self.dl_name.configure(text=job.full)
        self.dl_bar.set_value(job.percent)
        self.dl_info.configure(text=job.progress_text(T),
                               fg=th.c("err") if job.stalled else th.c("text_faint"))
        stalled = self.downloads.check_stall()
        if stalled is not None:
            self.dl_info.configure(fg=th.c("err"))
            self.event_log.add("WARN", T("dl.stalled", job.full))

    def ask_download(self, parent, name: str, tag: str, size_text: str = "") -> dict | None:
        """弹出下载设置窗（线程数 / 下载源 / 测速）。返回设置或 None。"""
        from .dl_dialog import ask_download_options

        size = parse_size(size_text)
        try:
            return ask_download_options(parent, self, name, tag, size)
        except Exception as exc:
            self.log_event("ERROR", f"download dialog: {exc}")
            # 弹窗异常时不要挡住下载：回退到配置里的默认设置
            cfg = self.cfg.get("download") or {}
            return {"threads": int(cfg.get("threads") or 2),
                    "base": cfg.get("source") or "",
                    "mode": "native" if cfg.get("native") else "mirror",
                    "remember": True}

    def t(self, key: str, *args):
        return T(key, *args)

    def log(self, level: str, message: str):
        self.log_event(level, message)

    # ------------------------------------------------ 配置 / 异常
    def save_cfg(self):
        """把内存中的配置写回磁盘（按钮回调里大量调用）。"""
        try:
            cfgmod.save(self.cfg)
        except Exception as exc:
            self.event_log.add("ERROR", f"save config: {exc}")

    def report_callback_exception(self, exc, val, tb):
        """Tk 控件回调里的异常默认被静默吞掉（pythonw 无控制台），
        这里改为写入事件日志 + error.log 并弹提示，避免再出现“点了没反应”。"""
        import traceback

        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            self.event_log.add("ERROR", f"UI callback: {val}")
        except Exception:
            pass
        try:
            cfgmod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(cfgmod.CONFIG_DIR / "error.log", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass
        try:
            self.toast(f"内部错误 / Internal error: {val}")
        except Exception:
            pass
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass

    # ------------------------------------------------ 主题 / 语言
    def toggle_theme(self):
        mode = th.toggle()
        self.cfg["theme"] = mode
        self._apply_all_theme()
        self.save_cfg()

    def toggle_lang(self):
        from . import i18n

        new = "en" if i18n.current() == "zh" else "zh"
        i18n.set_lang(new)
        self.cfg["lang"] = new
        self.apply_texts()
        self.save_cfg()

    def save_config_and_refresh(self):
        self.apply_texts()
        self.save_cfg()

    def _apply_all_theme(self):
        style_scrollbar(self._style())
        self.configure(bg=th.c("root"))
        self.canvas.configure(bg=th.c("root"))
        for frame in (self.nav_frame, self.top_frame, self.content):
            frame.configure(bg=th.c("card"))
        self.app_title.configure(bg=th.c("card"), fg=th.c("text"))
        self.app_sub.configure(bg=th.c("card"), fg=th.c("text_faint"))
        self.nav_footer.configure(bg=th.c("card"))
        self.nav_status.configure(bg=th.c("card"), fg=th.c("text_faint"))
        self.ver_label.configure(bg=th.c("card"), fg=th.c("text_faint"))
        self.nav_dot.apply_theme(th.c("card"))
        for item in self.nav_items.values():
            item.apply_theme(th.c("card"))
        self.dl_box.configure(bg=th.c("card_alt"))
        for widget in (self.dl_head, self.dl_bar):
            try:
                widget.configure(bg=th.c("card_alt"))
            except Exception:
                pass
        self.dl_name.configure(bg=th.c("card_alt"), fg=th.c("text"))
        self.dl_cancel.configure(bg=th.c("card_alt"), fg=th.c("text_faint"))
        self.dl_info.configure(bg=th.c("card_alt"), fg=th.c("text_faint"))
        self.dl_bar.apply_theme(th.c("card_alt"))
        self.view_title.configure(bg=th.c("card"), fg=th.c("text"))
        self.status_label.configure(bg=th.c("card"), fg=th.c("text_muted"))
        self.dot.apply_theme(th.c("card"))
        for btn in (self.model_chip, self.theme_btn, self.lang_btn, self.bg_btn):
            btn.apply_theme(th.c("card"))
        self.toast_label.configure(bg=th.c("card_alt"), fg=th.c("text"))
        for view in self.views.values():
            try:
                view.apply_theme()
            except Exception as exc:
                self.event_log.add("ERROR", f"theme: {exc}")
        self.apply_texts()
        # 兜底：把深层容器里残留的旧主题颜色统一迁移到当前主题
        try:
            fixed = th.migrate_colors(self)
            if fixed:
                self.event_log.add("INFO", f"theme colors migrated: {fixed}")
        except Exception as exc:
            self.event_log.add("ERROR", f"migrate: {exc}")
        self._render_background()
        self._draw_cards()

    def apply_texts(self):
        self.app_sub.configure(text=T("app.subtitle"))
        for key, item in self.nav_items.items():
            item.set_text(T(f"nav.{key}"))
        self.view_title.configure(text=T(f"nav.{self.current_view_key}"))
        self.model_chip.set_text(self._model_chip_text())
        self.theme_btn.set_text(T("set.theme_light") if th.is_dark() else T("set.theme_dark"))
        self.lang_btn.set_text("EN" if self.cfg.get("lang") == "zh" else "中文")
        self.bg_btn.set_text(T("top.bg"))
        for view in self.views.values():
            try:
                view.apply_texts()
            except Exception as exc:
                self.event_log.add("ERROR", f"texts: {exc}")
        # 兜底：重译所有带 _i18n_key 标记的深层控件
        try:
            i18n.retranslate(self)
        except Exception as exc:
            self.event_log.add("ERROR", f"retranslate: {exc}")

    def _model_chip_text(self) -> str:
        """顶栏胶囊：显示已安装数量（点击进入「已安装」）。"""
        return T("top.installed", len(self.installed))

    # ------------------------------------------------ 背景
    def apply_background(self):
        self._render_background()
        self._draw_cards()

    # ------------------------------------------------ 状态
    @property
    def client(self) -> OllamaClient:
        return OllamaClient(self.cfg.get("host", "http://127.0.0.1:11434"))

    @property
    def current_model(self) -> str:
        return self.cfg.get("model", "")

    def set_model(self, name: str):
        self.cfg["model"] = name
        self.save_cfg()
        self.model_chip.set_text(self._model_chip_text())
        self.event_log.add("INFO", f"model -> {name}")
        self.toast(f"{T('models.in_use')}: {name}")

    def _probe_status(self):
        try:
            info = self.client.version(timeout=3)
            return info.get("version", "")
        except Exception:
            return None

    def refresh_status_now(self):
        self.status = "busy"
        self.run_async(self._probe_status, lambda res, err: self._update_status_ui())

    def _tick(self):
        self._update_status_ui()
        try:
            self._drain_download_events()
            self._update_download_strip()
        except Exception as exc:
            self.event_log.add("ERROR", f"download strip: {exc}")
        if getattr(self, "_dl_dirty", False):
            self._dl_dirty = False
            catalog = self.views.get("catalog")
            if catalog is not None:
                try:
                    catalog.refresh_downloads()
                except Exception:
                    pass
        view = self.views.get(self.current_view_key)
        if isinstance(view, ServiceView):
            try:
                view.tick()
            except Exception:
                pass
        self.after(1500, self._tick)

    def _update_status_ui(self):
        state = self.status
        if state == "ok":
            self.dot.set_state("ok")
            self.nav_dot.set_state("ok")
            self.status_label.configure(text=T("top.online"), fg=th.c("ok"))
            self.nav_status.configure(text=T("top.online"), fg=th.c("ok"))
        elif state == "err":
            self.dot.set_state("err")
            self.nav_dot.set_state("err")
            self.status_label.configure(text=T("top.offline"), fg=th.c("err"))
            self.nav_status.configure(text=T("top.offline"), fg=th.c("err"))
        else:
            self.dot.set_state("busy")
            self.nav_dot.set_state("busy")
            self.status_label.configure(text=T("svc.checking"), fg=th.c("text_muted"))
            self.nav_status.configure(text=T("svc.checking"), fg=th.c("text_faint"))
        self.model_chip.set_text(self._model_chip_text())

    # ------------------------------------------------ 异步任务
    def run_async(self, fn, callback=None):
        def worker():
            try:
                result = fn()
                error = None
            except Exception as exc:
                result, error = None, exc
            self._results.put((callback, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                callback, result, error = self._results.get_nowait()
                if callback is not None:
                    try:
                        callback(result, error)
                    except Exception as exc:
                        self.event_log.add("ERROR", f"callback: {exc}")
        except queue.Empty:
            pass
        self.after(120, self._pump)

    # ------------------------------------------------ 提示 / 日志
    def toast(self, message: str, ms: int = 2600):
        self.toast_label.configure(text=message)
        self.toast_label.place(relx=0.5, rely=1.0, anchor="s", y=-18)
        self.toast_label.lift()
        if getattr(self, "_toast_job", None):
            try:
                self.after_cancel(self._toast_job)
            except Exception:
                pass
        self._toast_job = self.after(ms, self.toast_label.place_forget)

    def log_event(self, level: str, message: str):
        self.event_log.add(level, message)

    # ------------------------------------------------ 关闭
    def on_close(self):
        try:
            self._poller.stop()
        except Exception:
            pass
        self.cfg["window"] = {
            "w": self.winfo_width(), "h": self.winfo_height(),
            "x": self.winfo_x(), "y": self.winfo_y(),
        }
        self.cfg["theme"] = th.current()
        save = cfgmod.save
        save(self.cfg)
        self.destroy()


def run():
    app = MainWindow()
    app.mainloop()
