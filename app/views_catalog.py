"""AI 下载器主界面：左侧全量模型列表，右侧该模型的全部版本与下载。

设计：
  * 目录来自 catalog.Catalog（ollama.com/library 全量 + 社区补充），本地缓存，秒开；
  * 搜索是**本地过滤**（242+ 条内存里筛，输入即出结果），不联网；
  * 版本列表按需抓取并缓存（qwen3 有 58 个、llama3.2 有 63 个版本）；
  * 每个版本显示精确体积、量化方式、是否已下载，可直接下载/取消；
  * 下载进度、速度、剩余时间实时刷新。
"""
from __future__ import annotations

import threading
import tkinter as tk

from . import theme as th
from .catalog import CAP_LABELS, installed_state
from .i18n import t as T
from .ollama_api import human_size
from .widgets import PillButton, ProgressBar, ScrollFrame, Segmented

PAGE = 60            # 每次渲染多少个模型（242 个全渲染也可以，但分批更流畅）


class Badge(tk.Label):
    """小徽章（能力标签 / 参数量）。"""

    def __init__(self, parent, text: str, fg: str, bg: str):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=th.F(7),
                         padx=6, pady=1)


class CatalogView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=th.c("card"))
        self.app = app
        self.selected: str = ""
        self.limit = PAGE
        self._tag_jobs: dict[str, dict] = {}
        self._version_rows: list[tuple] = []
        self._rows: dict[str, dict] = {}
        self._models: list[dict] = []
        self._pending_models: list[dict] = []
        self._batch_i = 0
        self._batch_jobs: list = []
        self._render_job = None
        self._active_name = ""
        self._build()

    # ============================================================ 构建
    def _build(self):
        # ---------------- 顶部工具栏
        bar = tk.Frame(self, bg=th.c("card"))
        bar.pack(fill="x", padx=14, pady=(12, 6))

        self.title = tk.Label(bar, text=T("cat.title"), bg=th.c("card"),
                              fg=th.c("text"), font=th.F(13, "bold"), anchor="w")
        self.title.pack(side="left")

        self.refresh_btn = PillButton(bar, T("cat.refresh"), command=self.refresh_catalog,
                                      kind="ghost", height=32, radius=9, font=th.F(9),
                                      bg=th.c("card"))
        self.refresh_btn.pack(side="right")
        self.meta = tk.Label(bar, text="", bg=th.c("card"), fg=th.c("text_faint"),
                             font=th.F(8), anchor="e")
        self.meta.pack(side="right", padx=(0, 10))

        # ---------------- 搜索 + 过滤
        filters = tk.Frame(self, bg=th.c("card"))
        filters.pack(fill="x", padx=14, pady=(0, 8))

        box = tk.Frame(filters, bg=th.c("card_alt"), padx=10, pady=6)
        box.pack(side="left", fill="x", expand=True)
        tk.Label(box, text="🔍", bg=th.c("card_alt"), fg=th.c("text_faint"),
                 font=th.F(10)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._schedule_render())
        self.search = tk.Entry(box, textvariable=self.search_var, bd=0,
                               bg=th.c("card_alt"), fg=th.c("text"),
                               insertbackground=th.c("accent"), font=th.F(10),
                               highlightthickness=0)
        self.search.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.cap_seg = Segmented(
            filters,
            [("", T("cat.cap_all")), ("vision", T("cat.cap_vision")),
             ("tools", T("cat.cap_tools")), ("thinking", T("cat.cap_thinking")),
             ("embedding", T("cat.cap_embed"))],
            command=lambda _v: self._render_models(), height=30, radius=8,
            seg_width=62, bg=th.c("card"))
        self.cap_seg.pack(side="left", padx=(10, 0))

        self.sort_seg = Segmented(
            filters,
            [("popular", T("cat.sort_popular")), ("name", T("cat.sort_name")),
             ("updated", T("cat.sort_updated"))],
            command=lambda _v: self._render_models(), height=30, radius=8,
            seg_width=54, bg=th.c("card"))
        self.sort_seg.pack(side="left", padx=(10, 0))
        self.sort_seg.set_value("popular")

        # ---------------- 主体：左列表 / 右版本
        body = tk.Frame(self, bg=th.c("card"))
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        body.columnconfigure(0, weight=3, uniform="c")
        body.columnconfigure(1, weight=4, uniform="c")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=th.c("card_alt"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.list_scroll = ScrollFrame(left, bg_key="card_alt")
        self.list_scroll.pack(fill="both", expand=True, padx=2, pady=2)
        self.more_btn = PillButton(left, T("cat.more"), command=self._show_more,
                                   kind="ghost", height=30, radius=8, font=th.F(9),
                                   bg=th.c("card_alt"))

        right = tk.Frame(body, bg=th.c("card_alt"))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.detail = right
        self._build_detail(right)

    def _build_detail(self, parent):
        head = tk.Frame(parent, bg=th.c("card_alt"), padx=14, pady=12)
        head.pack(fill="x")
        self.d_name = tk.Label(head, text=T("cat.pick"), bg=th.c("card_alt"),
                               fg=th.c("text"), font=th.F(13, "bold"), anchor="w")
        self.d_name.pack(fill="x")
        self.d_badges = tk.Frame(head, bg=th.c("card_alt"))
        self.d_badges.pack(fill="x", pady=(6, 0))
        self.d_desc = tk.Label(head, text="", bg=th.c("card_alt"), fg=th.c("text_muted"),
                               font=th.F(9), anchor="w", justify="left", wraplength=430)
        self.d_desc.pack(fill="x", pady=(6, 0))

        row = tk.Frame(parent, bg=th.c("card_alt"), padx=14)
        row.pack(fill="x", pady=(0, 4))
        self.d_versions = tk.Label(row, text="", bg=th.c("card_alt"), fg=th.c("text"),
                                   font=th.F(10, "bold"), anchor="w")
        self.d_versions.pack(side="left")
        self.d_tag_btn = PillButton(row, T("cat.reload_tags"),
                                    command=lambda: self._load_tags(force=True),
                                    kind="ghost", height=26, radius=7, font=th.F(8),
                                    bg=th.c("card_alt"))
        self.d_tag_btn.pack(side="right")
        self.d_download_latest = PillButton(row, T("cat.download_latest"),
                                            command=self._download_latest, kind="primary",
                                            height=28, radius=8, font=th.F(9),
                                            bg=th.c("card_alt"))
        self.d_download_latest.pack(side="right", padx=(0, 8))

        self.tag_scroll = ScrollFrame(parent, bg_key="card_alt")
        self.tag_scroll.pack(fill="both", expand=True, padx=6, pady=(4, 8))
        self.d_status = tk.Label(parent, text="", bg=th.c("card_alt"),
                                 fg=th.c("text_faint"), font=th.F(8), anchor="w",
                                 justify="left", wraplength=440)
        self.d_status.pack(fill="x", padx=14, pady=(0, 10))

    # ============================================================ 目录
    def on_show(self):
        if not self.app.catalog.models:
            self.refresh_catalog()
        else:
            self._render_models()
            if self.app.catalog.is_stale():
                self.refresh_catalog(silent=True)

    def refresh_catalog(self, silent: bool = False):
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        self.refresh_btn.set_enabled(False)
        if not silent:
            self.meta.configure(text=T("cat.loading"))
        self.app.log_event("INFO", "refresh catalog")

        def work():
            return self.app.catalog.refresh()

        def done(result, error):
            self._refreshing = False
            self.refresh_btn.set_enabled(True)
            if error or not result:
                self.meta.configure(text=T("cat.failed", str(error)[:60]),
                                    fg=th.c("err"))
                if not silent:
                    self.app.toast(T("cat.failed", str(error)[:60]))
                return
            self.meta.configure(
                text=T("cat.meta", result["count"], self.app.catalog.age_text()),
                fg=th.c("text_faint"))
            self.app.log_event("INFO", f"catalog: {result['count']} models")
            self._render_models()
            if self.selected:
                self._load_tags(force=True)

        self.app.run_async(work, done)

    # 流畅度：连续输入只在停顿后渲染一次；行分批创建，避免一次性卡住界面
    def _schedule_render(self, delay: int = 200):
        if getattr(self, "_render_job", None):
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
        self._render_job = self.after(delay, self._render_models)

    def _render_models(self):
        self._render_job = None
        for job in getattr(self, "_batch_jobs", []):
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._batch_jobs = []

        cap = self.cap_seg.get_value()
        sort = self.sort_seg.get_value()
        models = self.app.catalog.filter(self.search_var.get(), cap=cap, sort=sort)
        self._models = models
        self.list_scroll.clear()
        self._rows = {}

        if not models:
            tk.Label(self.list_scroll.inner,
                     text=T("cat.empty") if self.app.catalog.models else T("cat.no_cache"),
                     bg=th.c("card_alt"), fg=th.c("text_faint"), font=th.F(10),
                     justify="left", wraplength=320).pack(padx=12, pady=24, anchor="w")
            self.more_btn.pack_forget()
            self.meta.configure(text=T("cat.meta", 0, self.app.catalog.age_text()))
            return

        self._pending_models = models[: self.limit]
        self._batch_i = 0
        self._render_batch()

    def _render_batch(self, size: int = 18):
        """分批创建行：每批 18 行，批间让出事件循环，滚动与输入不卡。"""
        installed = self.app.installed
        end = min(self._batch_i + size, len(self._pending_models))
        for model in self._pending_models[self._batch_i:end]:
            self._model_row(model, installed)
        self._batch_i = end
        if self._batch_i < len(self._pending_models):
            self._batch_jobs.append(self.after(1, self._render_batch))
            return
        total = len(self._models)
        if total > self.limit:
            self.more_btn.set_text(T("cat.more_n", total - self.limit))
            self.more_btn.pack(fill="x", padx=6, pady=(4, 8))
        else:
            self.more_btn.pack_forget()
        self.meta.configure(
            text=T("cat.meta_shown", min(self.limit, total), total,
                   self.app.catalog.age_text()))
        if self.selected and self.selected in self._rows:
            self._set_row_active(self.selected, True)

    def _show_more(self):
        self.limit += PAGE
        self._render_models()

    def _set_row_active(self, name: str, active: bool):
        """只改这一行的颜色（不再整表重建）——顺带修掉选中时方块变紫的问题。"""
        row = self._rows.get(name)
        if not row:
            return
        bg = th.c("card")
        for widget in row["parts"]:
            try:
                widget.configure(bg=bg)
            except Exception:
                pass
        try:
            row["bar"].configure(bg=th.c("accent") if active else bg)
            row["title"].configure(fg=th.c("accent") if active else th.c("text"))
        except Exception:
            pass

    def _model_row(self, model: dict, installed: dict):
        """一行模型卡片。

        选中态**不再整块填充淡紫色**（那会让能力徽章看起来像紫色方块），
        改成左侧一条强调色细条 + 名称变色，视觉更干净。
        徽章也合并成两个标签（能力、参数量），每行控件数从 ~9 降到 ~5，滚动更顺。
        """
        name = model.get("name", "")
        active = (name == self.selected)
        bg = th.c("card")
        card = tk.Frame(self.list_scroll.inner, bg=bg)
        card.pack(fill="x", padx=4, pady=2)

        bar = tk.Frame(card, bg=th.c("accent") if active else bg, width=3)
        bar.pack(side="left", fill="y")

        inner = tk.Frame(card, bg=bg, padx=10, pady=8)
        inner.pack(side="left", fill="both", expand=True)

        top = tk.Frame(inner, bg=bg)
        top.pack(fill="x")
        title = tk.Label(top, text=name, bg=bg,
                         fg=th.c("accent") if active else th.c("text"),
                         font=th.F(10, "bold"), anchor="w")
        title.pack(side="left")
        if any(k.split(":")[0] == name for k in installed):
            tk.Label(top, text=T("cat.installed"), bg=bg, fg=th.c("ok"),
                     font=th.F(7, "bold")).pack(side="left", padx=(8, 0))
        if model.get("pulls"):
            tk.Label(top, text=T("cat.pulls", f"{model['pulls']:,}"), bg=bg,
                     fg=th.c("text_faint"), font=th.F(7)).pack(side="right")

        zh = self.app.cfg.get("lang", "zh") == "zh"
        caps_text = " · ".join(CAP_LABELS.get(c, (c, c))[0 if zh else 1]
                               for c in (model.get("caps") or [])[:4])
        sizes_text = " · ".join((model.get("sizes") or [])[:6])
        meta_text = "　".join(x for x in (caps_text, sizes_text) if x)
        meta = tk.Label(inner, text=meta_text, bg=bg, fg=th.c("accent") if caps_text
                        else th.c("text_faint"), font=th.F(8), anchor="w")
        meta.pack(fill="x", pady=(3, 0))

        desc = model.get("description") or ""
        desc_label = None
        if desc:
            desc_label = tk.Label(inner, text=desc[:110], bg=bg, fg=th.c("text_faint"),
                                  font=th.F(8), anchor="w", justify="left", wraplength=300)
            desc_label.pack(fill="x", pady=(3, 0))

        parts = [card, inner, top, meta]
        if desc_label is not None:
            parts.append(desc_label)
        self._rows[name] = {"card": card, "bar": bar, "title": title, "parts": parts}

        for widget in (card, inner, top, title, meta) + ((desc_label,) if desc_label else ()):
            widget.bind("<Button-1>", lambda _e, n=name: self.select(n))

    # ============================================================ 版本
    def select(self, name: str):
        if name == self.selected:
            return
        self.selected = name
        model = next((m for m in self.app.catalog.models if m.get("name") == name), {})
        self.d_name.configure(text=name)
        self.d_desc.configure(text=(model.get("description") or "")[:400])
        for child in self.d_badges.winfo_children():
            child.destroy()
        for cap in model.get("caps") or []:
            label = CAP_LABELS.get(cap, (cap, cap))[0 if self.app.cfg.get("lang", "zh") == "zh" else 1]
            Badge(self.d_badges, label, th.c("accent"), th.c("card_alt")).pack(
                side="left", padx=(0, 4))
        for size in model.get("sizes") or []:
            Badge(self.d_badges, size, th.c("text_muted"), th.c("card_alt")).pack(
                side="left", padx=(0, 4))
        self.tag_scroll.clear()
        self.d_versions.configure(text=T("cat.loading_tags"))
        self.d_status.configure(text="")
        # 只更新变化的两行，避免整表重建造成的卡顿
        previous = getattr(self, "_active_name", "")
        if previous and previous != name:
            self._set_row_active(previous, False)
        self._set_row_active(name, True)
        self._active_name = name
        self._load_tags()

    def _load_tags(self, force: bool = False):
        if not self.selected:
            return
        name = self.selected
        self.d_tag_btn.set_enabled(False)

        def work():
            return self.app.catalog.get_tags(name, force=force)

        def done(result, error):
            self.d_tag_btn.set_enabled(True)
            if name != self.selected:
                return
            if error or not result:
                self.d_versions.configure(text=T("cat.tags_failed", str(error)[:50]))
                return
            self._render_tags(name, result)

        self.app.run_async(work, done)

    def _render_tags(self, name: str, data: dict):
        tags = data.get("tags") or []
        sizes = data.get("sizes") or {}
        self.tag_scroll.clear()
        self._version_rows = []
        self.d_versions.configure(text=T("cat.versions", len(tags)))
        if not tags:
            tk.Label(self.tag_scroll.inner, text=T("cat.tags_empty"),
                     bg=th.c("card_alt"), fg=th.c("text_faint"),
                     font=th.F(9)).pack(padx=12, pady=18, anchor="w")
            return
        installed = self.app.installed
        for tag in tags:
            self._version_row(name, tag, sizes.get(tag, ""), installed)

    def _version_row(self, name: str, tag: str, size_text: str, installed: dict):
        state = installed_state(installed, name, tag)
        row = tk.Frame(self.tag_scroll.inner, bg=th.c("card"), padx=10, pady=7)
        row.pack(fill="x", padx=4, pady=2)

        line = tk.Frame(row, bg=th.c("card"))
        line.pack(fill="x")
        tk.Label(line, text=tag, bg=th.c("card"), fg=th.c("text"),
                 font=th.F(9, "bold"), anchor="w").pack(side="left")
        if state == "exact":
            tk.Label(line, text=T("cat.installed"), bg=th.c("card"), fg=th.c("ok"),
                     font=th.F(7, "bold")).pack(side="left", padx=(6, 0))
        elif state == "base":
            tk.Label(line, text=T("cat.partial"), bg=th.c("card"), fg=th.c("warn"),
                     font=th.F(7)).pack(side="left", padx=(6, 0))
        if size_text:
            tk.Label(line, text=size_text, bg=th.c("card"), fg=th.c("text_muted"),
                     font=th.F(8)).pack(side="right", padx=(8, 0))

        btn = PillButton(line, T("cat.redownload") if state == "exact" else T("cat.download"),
                         command=lambda n=name, tg=tag, sz=size_text: self.download(n, tg, sz),
                         kind="ghost" if state == "exact" else "primary",
                         height=26, radius=7, font=th.F(8), bg=th.c("card"),
                         min_width=64)
        btn.pack(side="right")

        bar = ProgressBar(row, height=5, bg=th.c("card"))
        info = tk.Label(row, text="", bg=th.c("card"), fg=th.c("text_faint"),
                        font=th.F(7), anchor="w")
        self._version_rows.append((name, tag, row, btn, bar, info, size_text))

    # ============================================================ 下载
    def download(self, name: str, tag: str, size_text: str = ""):
        """下载前先让用户选择线程数 / 下载源（用户明确要求）。"""
        options = self.app.ask_download(self.winfo_toplevel(), name, tag, size_text)
        if not options:
            return
        self.app.downloads.enqueue(name, tag, **options)
        self.app.toast(T("cat.queued", f"{name}:{tag}"))
        self.refresh_downloads()

    def _download_latest(self):
        if self.selected:
            self.download(self.selected, "latest")

    def refresh_downloads(self):
        """刷新版本行上的进度（由主窗口的定时器调用）。"""
        if not self._version_rows:
            return
        jobs = {}
        for job in ([self.app.downloads.current] if self.app.downloads.current else []) + \
                list(self.app.downloads.history):
            if job is not None:
                jobs[job.full] = job
        for name, tag, row, btn, bar, info, size_text in self._version_rows:
            job = jobs.get(f"{name}:{tag}")
            if not job:
                if bar.winfo_ismapped():
                    bar.pack_forget()
                    info.configure(text="")
                    btn.set_text(T("cat.download"))
                continue
            if not bar.winfo_ismapped():
                bar.pack(fill="x", pady=(5, 0))
                info.pack(fill="x")
            if job.state == "running":
                bar.set_value(job.percent)
                info.configure(text=job.progress_text(T), fg=th.c("text_muted"))
                btn.set_text(T("cat.cancel"))
                btn.set_kind("danger")
            elif job.state == "queued":
                bar.set_value(0.0)
                info.configure(text=T("dl.queued"), fg=th.c("text_faint"))
                btn.set_text(T("cat.cancel"))
                btn.set_kind("danger")
            elif job.state == "done":
                bar.set_value(1.0)
                info.configure(text=job.progress_text(T), fg=th.c("ok"))
                btn.set_text(T("cat.installed_btn"))
                btn.set_kind("ghost")
                self.app.refresh_installed()
            else:
                info.configure(text=job.error or T("dl.cancelled"), fg=th.c("err"))
                btn.set_text(T("cat.download"))
                btn.set_kind("primary")

    # ============================================================ 主题 / 语言
    def apply_theme(self):
        for widget in (self, self.detail):
            widget.configure(bg=th.c("card"))
        self.list_scroll.apply_theme("card_alt")
        self.tag_scroll.apply_theme("card_alt")
        for widget in (self.search, self.meta, self.title, self.d_name, self.d_desc,
                       self.d_versions, self.d_status, self.d_badges):
            try:
                widget.configure(bg=th.c("card_alt") if widget in (
                    self.d_name, self.d_desc, self.d_versions, self.d_status,
                    self.d_badges) else th.c("card"))
            except Exception:
                pass
        self.search.configure(fg=th.c("text"), insertbackground=th.c("accent"))
        self.title.configure(fg=th.c("text"))
        self.d_name.configure(fg=th.c("text"))
        self.d_desc.configure(fg=th.c("text_muted"))
        self.d_status.configure(fg=th.c("text_faint"))
        for widget in (self.refresh_btn, self.d_tag_btn, self.d_download_latest,
                       self.more_btn):
            widget.apply_theme(th.c("card"))
        for widget in (self.cap_seg, self.sort_seg):
            widget.apply_theme(th.c("card"))
        self._render_models()
        if self.selected:
            self._load_tags()

    def apply_texts(self):
        self.title.configure(text=T("cat.title"))
        self.refresh_btn.set_text(T("cat.refresh"))
        self.more_btn.set_text(T("cat.more"))
        self.d_tag_btn.set_text(T("cat.reload_tags"))
        self.d_download_latest.set_text(T("cat.download_latest"))
        self.cap_seg.set_options([("", T("cat.cap_all")), ("vision", T("cat.cap_vision")),
                                  ("tools", T("cat.cap_tools")),
                                  ("thinking", T("cat.cap_thinking")),
                                  ("embedding", T("cat.cap_embed"))], keep_value=True)
        self.sort_seg.set_options([("popular", T("cat.sort_popular")),
                                   ("name", T("cat.sort_name")),
                                   ("updated", T("cat.sort_updated"))], keep_value=True)
        self._render_models()
