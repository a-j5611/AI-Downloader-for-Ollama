"""下载设置弹窗：选择线程数（任意自定义）、下载源（可换镜像）、并支持测速。

用户要求：**下载 AI 前弹窗让用户选择使用任意自定义线程下载**。
这里把线程数、下载源、测速、以及"是否每次询问"都放进弹窗。
"""
from __future__ import annotations

import threading
import tkinter as tk

from . import theme as th
from .i18n import t as T
from .mirror_dl import DEFAULT_SOURCES, probe_speed
from .ollama_api import human_size
from .widgets import PillButton, ProgressBar, Slider, Toggle

THREAD_PRESETS = (1, 2, 4, 8, 16)
MAX_THREADS = 32


class DownloadDialog(tk.Toplevel):
    """返回值：result = None（取消）或 dict(threads, base, source, mode, remember)。"""

    def __init__(self, parent, app, name: str, tag: str, size: int = 0):
        super().__init__(parent)
        self.app = app
        self.name, self.tag = name, tag
        self.size = size
        self.result: dict | None = None
        self._speed_jobs: list = []
        self._testing = False

        cfg = app.cfg.setdefault("download", {})
        self.sources = list(DEFAULT_SOURCES)
        for extra in cfg.get("sources") or []:
            if extra.get("base") and all(extra["base"] != s["base"] for s in self.sources):
                self.sources.append(extra)
        self._speed: dict[str, float] = {}

        self.withdraw()
        self.title(T("dlg.title", f"{name}:{tag}"))
        self.configure(bg=th.c("card"))
        self.resizable(False, False)
        self.transient(parent)
        self._build(cfg)
        self.update_idletasks()
        self._center(parent)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    # ============================================================ 构建
    def _build(self, cfg: dict):
        body = tk.Frame(self, bg=th.c("card"), padx=24, pady=20)
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=th.c("card"))
        head.pack(fill="x")
        tk.Label(head, text=T("dlg.title", f"{self.name}:{self.tag}"), bg=th.c("card"),
                 fg=th.c("text"), font=th.F(13, "bold"), anchor="w").pack(side="left")
        if self.size:
            tk.Label(head, text=human_size(self.size), bg=th.c("card"),
                     fg=th.c("text_muted"), font=th.F(10)).pack(side="right")

        tk.Label(body, text=T("dlg.hint"), bg=th.c("card"), fg=th.c("text_faint"),
                 font=th.F(8), anchor="w", justify="left", wraplength=520).pack(
            fill="x", pady=(6, 14))

        # ---------------- 线程数
        tk.Label(body, text=T("dlg.threads"), bg=th.c("card"), fg=th.c("text"),
                 font=th.F(10, "bold"), anchor="w").pack(fill="x")
        row = tk.Frame(body, bg=th.c("card"))
        row.pack(fill="x", pady=(6, 2))
        self.thread_var = tk.IntVar(value=int(cfg.get("threads") or 2))
        self.slider = Slider(row, 1, MAX_THREADS, self.thread_var.get(),
                             command=self._on_threads, width=240,
                             resolution=1, bg=th.c("card"))
        self.slider.pack(side="left")
        self.thread_entry = tk.Entry(row, width=4, bd=0, relief="flat",
                                     bg=th.c("card_alt"), fg=th.c("text"),
                                     insertbackground=th.c("accent"), font=th.F(11, "bold"),
                                     justify="center", highlightthickness=1,
                                     highlightbackground=th.c("border"),
                                     highlightcolor=th.c("accent"))
        self.thread_entry.pack(side="left", padx=(10, 6), ipady=5)
        self.thread_entry.insert(0, str(self.thread_var.get()))
        self.thread_entry.bind("<Return>", lambda _e: self._commit_entry())
        self.thread_entry.bind("<FocusOut>", lambda _e: self._commit_entry())
        tk.Label(row, text=T("dlg.threads_unit"), bg=th.c("card"), fg=th.c("text_muted"),
                 font=th.F(9)).pack(side="left")

        presets = tk.Frame(body, bg=th.c("card"))
        presets.pack(fill="x", pady=(4, 0))
        for n in THREAD_PRESETS:
            PillButton(presets, f"{n}", kind="ghost", height=26, radius=7, font=th.F(9),
                       min_width=44, bg=th.c("card"),
                       command=lambda v=n: self._set_threads(v)).pack(side="left", padx=(0, 6))
        tk.Label(presets, text=T("dlg.threads_tip"), bg=th.c("card"),
                 fg=th.c("text_faint"), font=th.F(8)).pack(side="left", padx=(6, 0))

        # ---------------- 下载源
        tk.Label(body, text=T("dlg.source"), bg=th.c("card"), fg=th.c("text"),
                 font=th.F(10, "bold"), anchor="w").pack(fill="x", pady=(16, 0))
        self.src_row = tk.Frame(body, bg=th.c("card"))
        self.src_row.pack(fill="x", pady=(6, 0))
        self.src_var = tk.StringVar(value=cfg.get("source") or self.sources[0]["base"])
        self.src_menu = tk.OptionMenu(self.src_row, self.src_var,
                                      *[s["base"] for s in self.sources],
                                      command=lambda _v: self._refresh_speed_text())
        self.src_menu.configure(bg=th.c("card_alt"), fg=th.c("text"), bd=0,
                                highlightthickness=0, activebackground=th.c("chip"),
                                font=th.F(9), anchor="w")
        self.src_menu["menu"].configure(bg=th.c("card_alt"), fg=th.c("text"),
                                        font=th.F(9), bd=0)
        self.src_menu.pack(side="left", fill="x", expand=True, ipady=3)

        self.test_btn = PillButton(self.src_row, T("dlg.test"), command=self.run_speed_test,
                                   kind="ghost", height=30, radius=8, font=th.F(9),
                                   bg=th.c("card"))
        self.test_btn.pack(side="left", padx=(8, 0))

        custom = tk.Frame(body, bg=th.c("card"))
        custom.pack(fill="x", pady=(6, 0))
        self.custom_entry = tk.Entry(custom, bd=0, relief="flat", bg=th.c("card_alt"),
                                     fg=th.c("text"), insertbackground=th.c("accent"),
                                     font=th.F(9), highlightthickness=1,
                                     highlightbackground=th.c("border"),
                                     highlightcolor=th.c("accent"))
        self.custom_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.custom_entry.insert(0, T("dlg.custom_placeholder"))
        self.custom_entry.bind("<FocusIn>", self._clear_placeholder)
        PillButton(custom, T("dlg.use_custom"), kind="ghost", height=30, radius=8,
                   font=th.F(9), bg=th.c("card"),
                   command=self._use_custom).pack(side="left", padx=(8, 0))

        self.speed_label = tk.Label(body, text=T("dlg.speed_none"), bg=th.c("card"),
                                    fg=th.c("text_faint"), font=th.F(8), anchor="w",
                                    justify="left", wraplength=520)
        self.speed_label.pack(fill="x", pady=(8, 0))

        # ---------------- 方式 + 记住
        opt = tk.Frame(body, bg=th.c("card"))
        opt.pack(fill="x", pady=(14, 0))
        self.native_toggle = Toggle(opt, bool(cfg.get("native")),
                                    command=self._on_native, bg=th.c("card"))
        self.native_toggle.pack(side="left")
        tk.Label(opt, text=T("dlg.native"), bg=th.c("card"), fg=th.c("text_muted"),
                 font=th.F(9)).pack(side="left", padx=(6, 18))
        self.remember_toggle = Toggle(opt, bool(cfg.get("remember", True)),
                                      bg=th.c("card"))
        self.remember_toggle.pack(side="left")
        tk.Label(opt, text=T("dlg.remember"), bg=th.c("card"), fg=th.c("text_muted"),
                 font=th.F(9)).pack(side="left", padx=(6, 0))

        # ---------------- 按钮
        btns = tk.Frame(body, bg=th.c("card"))
        btns.pack(fill="x", pady=(18, 0))
        PillButton(btns, T("cancel"), command=self._cancel, kind="ghost",
                   bg=th.c("card")).pack(side="right")
        PillButton(btns, T("dlg.start"), command=self._ok, kind="primary",
                   bg=th.c("card")).pack(side="right", padx=(0, 10))

    # ============================================================ 交互
    def _center(self, parent):
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            self.geometry(f"+{px + max(0, (pw - self.winfo_width()) // 2)}"
                          f"+{py + max(0, (ph - self.winfo_height()) // 3)}")
        except Exception:
            pass

    def _on_threads(self, value):
        n = max(1, min(MAX_THREADS, int(value)))
        self.thread_var.set(n)
        try:
            self.thread_entry.delete(0, "end")
            self.thread_entry.insert(0, str(n))
        except Exception:
            pass

    def _set_threads(self, n: int):
        self.slider.set_value(n, silent=False)
        self._on_threads(n)

    def _commit_entry(self):
        try:
            n = int(self.thread_entry.get().strip() or "2")
        except Exception:
            n = self.thread_var.get()
        n = max(1, min(MAX_THREADS, n))
        self.slider.set_value(n)
        self._on_threads(n)

    def _clear_placeholder(self, _e=None):
        if self.custom_entry.get() == T("dlg.custom_placeholder"):
            self.custom_entry.delete(0, "end")

    def _use_custom(self):
        base = self.custom_entry.get().strip()
        if not base or base == T("dlg.custom_placeholder"):
            return
        if not base.startswith("http"):
            base = "https://" + base
        base = base.rstrip("/")
        if all(s["base"] != base for s in self.sources):
            self.sources.append({"name": base, "base": base})
            menu = self.src_menu["menu"]
            menu.delete(0, "end")
            for s in self.sources:
                menu.add_command(label=s["base"],
                                 command=lambda v=s["base"]: self.src_var.set(v))
        self.src_var.set(base)
        self._refresh_speed_text()
        self.app.toast(T("dlg.custom_added", base))

    def _on_native(self, value: bool):
        self.test_btn.set_enabled(not value)
        self.src_menu.configure(state="disabled" if value else "normal")

    def _current_base(self) -> str:
        return self.src_var.get().strip() or self.sources[0]["base"]

    def _refresh_speed_text(self):
        base = self._current_base()
        if base in self._speed:
            self.speed_label.configure(
                text=T("dlg.speed_one", base, f"{self._speed[base]/1024/1024:.2f} MB/s"),
                fg=th.c("ok"))
        else:
            self.speed_label.configure(text=T("dlg.speed_none"), fg=th.c("text_faint"))

    # ============================================================ 测速
    def run_speed_test(self):
        if self._testing:
            return
        self._testing = True
        self.test_btn.set_enabled(False)
        self.progress = ProgressBar(self.speed_label.master, height=5, bg=th.c("card"))
        self.progress.pack(fill="x", pady=(6, 0))
        self.progress.set_value(0)
        self.speed_label.configure(text=T("dlg.testing"))

        targets = [s["base"] for s in self.sources]
        results: dict[str, dict] = {}
        lock = threading.Lock()
        done = {"n": 0}

        def worker(base):
            res = probe_speed(base, seconds=5)
            with lock:
                results[base] = res
                done["n"] += 1

        def tick():
            with lock:
                n, total = done["n"], len(targets)
                snapshot = dict(results)
            if not self.winfo_exists():
                return
            self.progress.set_value(n / total)
            lines = []
            for base in targets:
                res = snapshot.get(base)
                if res is None:
                    lines.append(f"· {base}  …")
                elif res.get("ok"):
                    rate = res["rate"] / 1024 / 1024
                    self._speed[base] = res["rate"]
                    lines.append(f"· {base}  {rate:.2f} MB/s"
                                 + ("" if res.get("range") else T("dlg.no_range")))
                else:
                    lines.append(f"· {base}  {T('dlg.failed')} {res.get('error','')}")
            self.speed_label.configure(text="\n".join(lines), fg=th.c("text_muted"))
            if n < total:
                self.after(400, tick)
            else:
                best = max((b for b, r in snapshot.items() if r.get("ok")),
                           key=lambda b: snapshot[b]["rate"], default="")
                if best:
                    self.src_var.set(best)
                    self.speed_label.configure(
                        text="\n".join(lines) + "\n" + T("dlg.best", best),
                        fg=th.c("ok"))
                self.test_btn.set_enabled(True)
                self._testing = False
                self.progress.pack_forget()

        threads = [threading.Thread(target=worker, args=(b,), daemon=True) for b in targets]
        for t in threads:
            t.start()
        self.after(400, tick)

    # ============================================================ 结果
    def _ok(self):
        self._commit_entry()
        base = self._current_base()
        threads = max(1, min(MAX_THREADS, self.thread_var.get()))
        cfg = self.app.cfg.setdefault("download", {})
        if self.remember_toggle.get_value():
            cfg["threads"] = threads
            cfg["source"] = base
            cfg["native"] = bool(self.native_toggle.get_value())
            cfg["remember"] = True
            known = [s["base"] for s in DEFAULT_SOURCES]
            if base not in known:
                cfg.setdefault("sources", []).append({"name": base, "base": base})
        cfg["remember"] = bool(self.remember_toggle.get_value())
        self.app.save_cfg()
        self.result = {"threads": threads, "base": base,
                       "mode": "native" if self.native_toggle.get_value() else "mirror",
                       "remember": self.remember_toggle.get_value()}
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.result = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask_download_options(parent, app, name: str, tag: str, size: int = 0):
    """打开弹窗；返回设置 dict 或 None。"""
    dlg = DownloadDialog(parent, app, name, tag, size)
    parent.wait_window(dlg)
    return dlg.result
