"""各功能面板：模型管理 / 服务控制 / 设置（外观·背景·参数）/ 日志。"""
from __future__ import annotations

import datetime as _dt
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog

from . import __version__
from . import config as cfgmod
from . import theme as th
from .backgrounds import HAS_PIL, PRESET_IDS
from .i18n import LANG_LABELS, LANGS, t as T
from .ollama_api import OllamaError, human_size, read_tail
from .widgets import (PillButton, ProgressBar, ScrollFrame, Segmented, Slider, StatusDot,
                      Toggle, alert, confirm, round_rect)


def fmt_time(value: str) -> str:
    """把 Ollama 返回的 ISO 时间转成本地可读格式。"""
    if not value:
        return "-"
    try:
        text = value.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(text)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value[:16].replace("T", " ")


class Panel(tk.Frame):
    title_key = ""

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.c("card"), bd=0, highlightthickness=0)
        self.app = app
        self._build()

    def _build(self):
        raise NotImplementedError

    def apply_theme(self):
        pass

    def apply_texts(self):
        pass

    def on_show(self):
        pass

    def on_hide(self):
        pass

    # 便捷：主题化 Entry
    def _entry(self, parent, width=0, font=None):
        entry = tk.Entry(parent, bd=0, relief="flat", bg=th.c("card_alt"), fg=th.c("text"),
                         insertbackground=th.c("accent"), font=font or th.F(10),
                         highlightthickness=1, highlightbackground=th.c("border"),
                         highlightcolor=th.c("accent"))
        if width:
            entry.configure(width=width)
        return entry


# ============================================================ 模型
class ModelsView(Panel):
    title_key = "models.title"

    def _build(self):
        self.pulling = False
        self.pull_queue: queue.Queue = queue.Queue()
        self.loaded_models: list = []

        head = tk.Frame(self, bg=th.c("card"))
        head.pack(fill="x", padx=18, pady=(16, 8))
        self.title = tk.Label(head, text=T("models.title"), bg=th.c("card"), fg=th.c("text"),
                              font=th.F(15, "bold"))
        self.title.pack(side="left")
        self.refresh_btn = PillButton(head, T("models.refresh"), command=self.refresh,
                                      kind="ghost", height=30, radius=9)
        self.refresh_btn.pack(side="right")
        self.add_btn = PillButton(head, T("models.browse"), command=self.browse_catalog,
                                  kind="primary", height=30, radius=9)
        self.add_btn.pack(side="right", padx=(0, 8))
        self.clean_btn = PillButton(head, T("models.clean"), command=self.clean_unused,
                                    kind="ghost", height=30, radius=9)
        self.clean_btn.pack(side="right", padx=(0, 8))
        self.aidb_btn = PillButton(head, T("models.aidb"), command=self.export_aidb,
                                   kind="ghost", height=30, radius=9)
        self.aidb_btn.pack(side="right", padx=(0, 8))

        pull_row = tk.Frame(self, bg=th.c("card"))
        pull_row.pack(fill="x", padx=18)
        self.pull_entry = self._entry(pull_row)
        self.pull_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.pull_entry.bind("<Return>", lambda _e: self.pull())
        self.pull_btn = PillButton(pull_row, T("models.pull"), command=self.pull,
                                   kind="primary", height=36, radius=9, min_width=92)
        self.pull_btn.pack(side="right", padx=(8, 0))

        self.progress_box = tk.Frame(self, bg=th.c("card"))
        self.progress = ProgressBar(self.progress_box, height=8, bg=th.c("card"))
        self.progress.pack(fill="x", pady=(10, 4))
        self.progress_label = tk.Label(self.progress_box, text="", bg=th.c("card"),
                                       fg=th.c("text_muted"), font=th.F(8), anchor="w")
        self.progress_label.pack(fill="x")

        # 未完成下载的提示条（继续下载 / 删除残留）
        self.dl_banner = tk.Frame(self, bg=th.c("card_alt"), padx=14, pady=10)
        self.dl_banner_label = tk.Label(self.dl_banner, text="", bg=th.c("card_alt"),
                                        fg=th.c("warn"), font=th.F(9), anchor="w",
                                        justify="left", wraplength=560)
        self.dl_banner_label.pack(fill="x")
        banner_row = tk.Frame(self.dl_banner, bg=th.c("card_alt"))
        banner_row.pack(fill="x", pady=(8, 0))
        self.dl_continue_btn = PillButton(banner_row, T("download.continue"),
                                          command=self.continue_download, kind="primary",
                                          height=30, radius=9, font=th.F(9),
                                          bg=th.c("card_alt"))
        self.dl_continue_btn.pack(side="left")
        self.dl_clean_btn = PillButton(banner_row, T("download.clean"),
                                       command=self.clean_partials, kind="ghost",
                                       height=30, radius=9, font=th.F(9),
                                       bg=th.c("card_alt"))
        self.dl_clean_btn.pack(side="left", padx=(8, 0))
        self.dl_repair_btn = PillButton(banner_row, T("download.repair"),
                                        command=self.repair_download, kind="ghost",
                                        height=30, radius=9, font=th.F(9),
                                        bg=th.c("card_alt"))
        self.dl_repair_btn.pack(side="left", padx=(8, 0))

        # 下载位置不一致 / 磁盘上有服务未加载的模型时的提示条
        self.lib_banner = tk.Frame(self, bg=th.c("card_alt"), padx=14, pady=10)
        self.lib_banner_label = tk.Label(self.lib_banner, text="", bg=th.c("card_alt"),
                                         fg=th.c("warn"), font=th.F(9), anchor="w",
                                         justify="left", wraplength=620)
        self.lib_banner_label.pack(fill="x")
        lib_row = tk.Frame(self.lib_banner, bg=th.c("card_alt"))
        lib_row.pack(fill="x", pady=(8, 0))
        self.lib_switch_btn = PillButton(lib_row, T("lib.switch_restart"),
                                         command=self.switch_library_service,
                                         kind="primary", height=30, radius=9,
                                         font=th.F(9), bg=th.c("card_alt"))
        self.lib_switch_btn.pack(side="left")
        self.lib_scan_btn = PillButton(lib_row, T("lib.rescan"), command=self.rescan_library,
                                       kind="ghost", height=30, radius=9, font=th.F(9),
                                       bg=th.c("card_alt"))
        self.lib_scan_btn.pack(side="left", padx=(8, 0))

        self.scroll = ScrollFrame(self, bg_key="card")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(6, 12))

    # ------------------------------------------------ 下载位置
    def refresh_library(self):
        """绑定位置 / 服务目录变化后刷新提示条与列表。"""
        app = self.app
        disk_only = [k for k, v in (app.installed or {}).items()
                     if v.get("on_disk") and not v.get("served")]
        mismatch = app.library_mismatch()
        if not mismatch and not disk_only:
            self.lib_banner.pack_forget()
        else:
            if mismatch:
                text = T("lib.mismatch", app.server_dir, app.library_bound() or "-")
            else:
                text = T("lib.disk_only", len(disk_only))
            if disk_only and not mismatch:
                text += "　" + "、".join(disk_only[:4])
            self.lib_banner_label.configure(text=text)
            self.lib_switch_btn.set_enabled(mismatch)
            self.lib_banner.pack(fill="x", padx=18, pady=(8, 0), before=self.scroll)
        self.refresh()

    def switch_library_service(self):
        self.app.restart_service_for_library()

    def rescan_library(self):
        self.app.scan_library(notify=True)

    # ------------------------------------------------ 数据
    def on_show(self):
        self.refresh()
        self.refresh_library()

    def open_gallery(self):
        """（保留兼容）跳到「AI 模型库」页。"""
        self.browse_catalog()

    def browse_catalog(self):
        self.app.show_view("catalog")

    def redownload(self, name: str, tag: str = "latest"):
        """按名称重新下载任意模型（含目录里没有的社区模型）。"""
        options = self.app.ask_download(self.winfo_toplevel(), name, tag)
        if not options:
            return
        self.app.downloads.enqueue(name, tag, **options)
        self.app.toast(T("cat.queued", f"{name}:{tag}"))

    def refresh(self):
        client = self.app.client
        self.app.run_async(lambda: {"tags": client.tags(), "ps": client.ps()}, self._on_data)

    def _on_data(self, result, error):
        if error:
            self.app.log_event("ERROR", f"models: {error}")
            self.scroll.clear()
            tk.Label(self.scroll.inner, text=str(error), bg=th.c("card"), fg=th.c("err"),
                     font=th.F(10), wraplength=520, justify="left").pack(padx=12, pady=18, anchor="w")
            self._refresh_download_banner()
            return
        self.loaded_models = result.get("ps") or []
        self._render(result.get("tags") or [])
        self._refresh_download_banner()

    # ------------------------------------------------ 未完成的下载
    def _refresh_download_banner(self):
        """有未完成下载分片时，给出「继续下载 / 删除残留」入口。"""
        from .storage import ModelStorage

        storage = ModelStorage(self.app.cfg.get("models_dir", ""))
        partials = storage.partials()
        if not partials or self.app.downloads.is_busy():
            self.dl_banner.pack_forget()
            return
        pending = (self.app.cfg.get("download") or {}).get("pending_model") or ""
        size = ModelStorage.human(sum(p["size"] for p in partials))
        text = T("download.incomplete", len(partials), size)
        if pending:
            text += "　" + T("download.pending_hint", pending)
        self.dl_banner_label.configure(text=text)
        self.dl_continue_btn.set_enabled(bool(pending))
        self.dl_banner.pack(fill="x", padx=18, pady=(10, 0), before=self.scroll)

    def continue_download(self):
        """从断点继续下载上次未完成的模型（Ollama 会自动续传）。"""
        pending = (self.app.cfg.get("download") or {}).get("pending_model") or ""
        if not pending:
            self.app.toast(T("download.no_pending"))
            return
        self.dl_banner.pack_forget()
        base, _, tag = pending.partition(":")
        options = self.app.ask_download(self.winfo_toplevel(), base, tag or "latest")
        if not options:
            return
        self.app.downloads.enqueue(base, tag or "latest", **options)
        self.app.toast(T("download.resuming", pending))

    # ------------------------------------------------ support files 数据库（C++ 工具）
    def export_aidb(self):
        """调用 aidb.exe，把已安装模型的 support files 抽成数据库。"""
        import subprocess

        conf = self.app.cfg.get("aidb") or {}
        tool = conf.get("tool") or ""
        db_file = conf.get("db") or ""
        if not tool or not os.path.exists(tool):
            self.app.toast(T("models.aidb_missing", tool or "aidb.exe"))
            self.app.log_event("ERROR", f"aidb tool not found: {tool}")
            return
        self.aidb_btn.set_enabled(False)
        self.app.toast(T("models.aidb_running"))
        models_dir = self.app.cfg.get("models_dir") or ""

        def work():
            proc = subprocess.run(
                [tool, "build", "--models-dir", models_dir, "-o", db_file],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

        def done(result, error):
            self.aidb_btn.set_enabled(True)
            if error or not result:
                self.app.toast(T("models.aidb_failed", str(error)[:60]))
                return
            code, output = result
            summary = ""
            for line in (output or "").splitlines():
                if line.startswith("完成") or line.startswith("数据库"):
                    summary += line.strip() + "  "
            if code == 0:
                self.app.toast(T("models.aidb_done", summary.strip() or db_file))
                self.app.log_event("INFO", f"aidb: {summary.strip()}")
            else:
                self.app.toast(T("models.aidb_failed", (output or "").strip()[:80]))
                self.app.log_event("ERROR", f"aidb failed ({code}): {output[:200]}")

        self.app.run_async(work, done)

    def clean_partials(self):
        """删除所有未完成下载的残留分片。"""
        from .storage import ModelStorage

        storage = ModelStorage(self.app.cfg.get("models_dir", ""))
        partials = storage.partials()
        if not partials:
            self.app.toast(T("models.clean_none"))
            return
        size = ModelStorage.human(sum(p["size"] for p in partials))
        if not confirm(self.winfo_toplevel(), T("download.clean_confirm", size),
                       T("download.clean")):
            return
        removed = storage.remove_partials()
        # 分片被 Ollama 占着删不掉时（服务端句柄泄漏），自动重启服务再删一次
        if storage.partials():
            self.app.log_event("WARN", "partials locked, restarting ollama service")
            self.app.toast(T("download.locked_restart"))
            try:
                self.app.svc.stop()
                time.sleep(1.5)
            except Exception:
                pass
            extra = storage.remove_partials()
            removed = {"removed": removed["removed"] + extra["removed"],
                       "freed": removed["freed"] + extra["freed"]}
            try:
                self.app.svc.start()
                self.app.svc.wait_up(45)
            except Exception as exc:
                self.app.log_event("ERROR", f"restart service: {exc}")
        freed = ModelStorage.human(removed["freed"])
        self.app.cfg.setdefault("download", {})["pending_model"] = ""
        self.app.save_cfg()
        self.app.toast(T("download.cleaned", freed))
        self.app.log_event("INFO", f"clean partials: {removed['removed']} files, {freed}")
        self._refresh_download_banner()

    def repair_download(self):
        """服务端卡死时的恢复：停服务 → 删除损坏分片 → 起服务 → 重新下载。"""
        from .storage import ModelStorage

        pending = (self.app.cfg.get("download") or {}).get("pending_model") or ""
        storage = ModelStorage(self.app.cfg.get("models_dir", ""))
        size = ModelStorage.human(storage.partials_size())
        if not confirm(self.winfo_toplevel(), T("download.repair_confirm", size),
                       T("download.repair")):
            return
        self.pulling = True
        self.pull_btn.set_enabled(False)
        self.progress_box.pack(fill="x", pady=(10, 0))
        self.progress.set_value(0.0)
        self.progress_label.configure(text=T("download.repairing"))
        svc = self.app.svc

        def work():
            info = {}
            try:
                svc.stop()
                time.sleep(1.5)
                info["stopped"] = True
            except Exception as exc:
                info["stop_error"] = str(exc)
            info["removed"] = storage.remove_partials()
            info["left"] = len(storage.partials())
            try:
                svc.start()
                info["up"] = svc.wait_up(45)
            except Exception as exc:
                info["start_error"] = str(exc)
                info["up"] = False
            return info

        def done(result, error):
            self.pulling = False
            self.pull_btn.set_enabled(True)
            result = result or {}
            freed = ModelStorage.human((result.get("removed") or {}).get("freed", 0))
            self.app.log_event(
                "INFO", f"repair: removed {freed}, left {result.get('left')}, "
                        f"service up={result.get('up')}")
            self._refresh_download_banner()
            if not result.get("up"):
                self.progress_label.configure(text=T("svc.start_failed", "timeout"))
                self.app.toast(T("svc.start_failed", "timeout"))
                return
            self.app.toast(T("download.repair_done", freed))
            if pending:
                self.pull_entry.delete(0, "end")
                self.pull_entry.insert(0, pending)
                self.pull()
            else:
                self.progress_box.pack_forget()

        self.app.run_async(work, done)

    def _render(self, models: list):
        self.scroll.clear()
        current = self.app.current_model
        if not models:
            tk.Label(self.scroll.inner, text=T("models.none"), bg=th.c("card"),
                     fg=th.c("text_faint"), font=th.F(10)).pack(padx=12, pady=24, anchor="w")
        for model in sorted(models, key=lambda m: m.get("name", "")):
            self._model_card(model, model.get("name") == current)

        if self.loaded_models:
            tk.Label(self.scroll.inner, text=T("models.loaded_title"), bg=th.c("card"),
                     fg=th.c("text_muted"), font=th.F(10, "bold"), anchor="w").pack(
                fill="x", padx=8, pady=(14, 4))
            for loaded in self.loaded_models:
                self._loaded_card(loaded)

    def _model_card(self, model: dict, is_current: bool):
        card = tk.Frame(self.scroll.inner, bg=th.c("card_alt"), padx=14, pady=12)
        card.pack(fill="x", padx=6, pady=5)
        row = tk.Frame(card, bg=th.c("card_alt"))
        row.pack(fill="x")
        tk.Label(row, text=model.get("name", "?"), bg=th.c("card_alt"), fg=th.c("text"),
                 font=th.F(11, "bold")).pack(side="left")

        name = model.get("name", "")
        PillButton(row, T("models.delete_full"), kind="ghost", height=28, radius=8,
                   command=lambda n=name: self.delete_model(n), font=th.F(9)).pack(side="right")
        base, _, tag = name.partition(":")
        PillButton(row, T("models.redownload"), kind="ghost", height=28, radius=8,
                   command=lambda b=base, tg=(tag or "latest"): self.redownload(b, tg),
                   font=th.F(9)).pack(side="right", padx=(0, 8))

        meta = f"{T('models.size')} {human_size(model.get('size', 0))}    " \
               f"{T('models.modified')} {fmt_time(model.get('modified_at', ''))}"
        tk.Label(card, text=meta, bg=th.c("card_alt"), fg=th.c("text_muted"),
                 font=th.F(9)).pack(anchor="w", pady=(8, 0))

        params = ((model.get("details") or {}).get("parameter_size") or "")
        quant = ((model.get("details") or {}).get("quantization_level") or "")
        if params or quant:
            tk.Label(card, text=f"{params} · {quant}".strip(" ·"), bg=th.c("card_alt"),
                     fg=th.c("text_faint"), font=th.F(8)).pack(anchor="w", pady=(2, 0))

    def _loaded_card(self, loaded: dict):
        card = tk.Frame(self.scroll.inner, bg=th.c("card_alt"), padx=14, pady=10)
        card.pack(fill="x", padx=6, pady=4)
        vram = loaded.get("size_vram") or 0
        where = "GPU" if vram else "CPU"
        tk.Label(card, text=loaded.get("name", "?"), bg=th.c("card_alt"), fg=th.c("text"),
                 font=th.F(10, "bold")).pack(anchor="w")
        text = (f"{T('models.processor')} {where}    "
                f"{T('models.context')} {loaded.get('context_length', '-')}    "
                f"{T('models.size')} {human_size(loaded.get('size', 0))}    "
                f"{T('models.expires')} {fmt_time(loaded.get('expires_at', ''))}")
        tk.Label(card, text=text, bg=th.c("card_alt"), fg=th.c("text_muted"),
                 font=th.F(9)).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------ 操作
    def pull(self):
        """按名称下载任意模型（含目录里没有的社区模型）。"""
        name = self.pull_entry.get().strip()
        if not name:
            return
        base, _, tag = name.partition(':')
        options = self.app.ask_download(self.winfo_toplevel(), base, tag or 'latest')
        if not options:
            return
        self.app.downloads.enqueue(base, tag or 'latest', **options)
        self.app.toast(T('cat.queued', name if ':' in name else name + ':latest'))
        self.pull_entry.delete(0, 'end')
        self.app.log_event('INFO', f'pull by name: {name}')

    def delete_model(self, name: str):
        """完全删除：先按磁盘清单算出能释放多少空间，确认后连数据一起清掉。"""
        from .storage import ModelStorage

        client = self.app.client
        storage = ModelStorage(self.app.cfg.get("models_dir", ""))

        def plan():
            digests = client.tag_digests()
            digest = digests.get(name, "")
            size = 0
            for model in client.tags():
                if model.get("name") == name:
                    size = model.get("size", 0)
            plan_data = (storage.plan_delete(digest) if digest else
                         {"files": [], "freed": 0, "shared_size": 0, "shared_count": 0})
            return {"plan": plan_data, "size": size}

        def done(result, error):
            if error or not result:
                self.app.toast(T("chat.error", error))
                return
            plan_data = result["plan"]
            if plan_data.get("shared_count"):
                message = T("models.delete_detail", name=name,
                            size=ModelStorage.human(result["size"]),
                            freed=ModelStorage.human(plan_data["freed"]),
                            shared=ModelStorage.human(plan_data["shared_size"]))
            else:
                message = T("models.delete_detail_noshare", name=name,
                            size=ModelStorage.human(result["size"]),
                            freed=ModelStorage.human(plan_data["freed"]))
            if not confirm(self.winfo_toplevel(), message, T("models.delete_title")):
                return
            self._do_delete(name, result)

        self.app.run_async(plan, done)

    def _do_delete(self, name: str, result: dict):
        from .storage import ModelStorage

        client = self.app.client
        storage = ModelStorage(self.app.cfg.get("models_dir", ""))
        self.app.toast(T("models.deleting", name))

        def work():
            api_error = ""
            try:
                client.delete(name)
            except Exception as exc:
                api_error = str(exc)
            removed = storage.remove(result["plan"].get("files") or [])
            return {"removed": removed, "api_error": api_error}

        def done(payload, err):
            if err or not payload:
                self.app.toast(T("chat.error", err))
                return
            removed = payload["removed"]
            freed = ModelStorage.human(removed["freed"])
            self.app.toast(T("models.delete_done", name=name, freed=freed))
            self.app.log_event(
                "INFO",
                f"delete {name}: {removed['removed']} files, freed {freed}"
                + (f" (api: {payload['api_error']})" if payload["api_error"] else ""))
            if removed["errors"]:
                self.app.log_event("WARN", f"delete errors: {removed['errors'][:3]}")
            self.refresh()

        self.app.run_async(work, done)

    def clean_unused(self):
        """清理没有任何模型引用的数据文件（例如误删模型后的残留）。"""
        from .storage import ModelStorage

        client = self.app.client
        storage = ModelStorage(self.app.cfg.get("models_dir", ""))

        def work():
            try:
                digests = set(client.tag_digests().values())
                online = True
            except Exception:
                digests, online = set(), False
            return {"plan": storage.plan_orphans(digests if online else None),
                    "online": online}

        def done(result, error):
            if error or not result:
                self.app.toast(T("chat.error", error))
                return
            plan_data = result["plan"]
            files = list(plan_data.get("files") or []) + \
                list(plan_data.get("orphan_manifests") or [])
            if not files:
                self.app.toast(T("models.clean_none"))
                return
            extra = ""
            if plan_data.get("orphan_manifests"):
                extra = T("models.clean_manifests", len(plan_data["orphan_manifests"]))
            if plan_data.get("skipped_recent"):
                extra += ("\n" if extra else "") + \
                    T("models.clean_skip", count=plan_data["skipped_recent"])
            message = T("models.clean_detail", count=len(files),
                        size=ModelStorage.human(plan_data["freed"]), extra=extra)
            if not confirm(self.winfo_toplevel(), message, T("models.clean_title")):
                return
            removed = ModelStorage.remove(files)
            self.app.toast(T("models.clean_done", count=removed["removed"],
                             size=ModelStorage.human(removed["freed"])))
            self.app.log_event("INFO", f"clean unused: {removed['removed']} files, "
                                       f"{ModelStorage.human(removed['freed'])}")
            self.refresh()

        self.app.run_async(work, done)

    # ------------------------------------------------ 主题 / 语言
    def apply_theme(self):
        self.configure(bg=th.c("card"))
        self.title.configure(bg=th.c("card"), fg=th.c("text"))
        self.scroll.apply_theme("card")
        self.progress_box.configure(bg=th.c("card"))
        self.progress.apply_theme(th.c("card"))
        self.progress_label.configure(bg=th.c("card"), fg=th.c("text_muted"))
        self.refresh_btn.apply_theme(th.c("card"))
        self.add_btn.apply_theme(th.c("card"))
        self.pull_btn.apply_theme(th.c("card"))
        self.pull_entry.configure(bg=th.c("card_alt"), fg=th.c("text"),
                                  insertbackground=th.c("accent"),
                                  highlightbackground=th.c("border"), highlightcolor=th.c("accent"))
        self.pull_entry.master.configure(bg=th.c("card"))
        self.refresh()

    def apply_texts(self):
        self.title.configure(text=T("models.title"))
        self.refresh_btn.set_text(T("models.refresh"))
        self.add_btn.set_text(T("models.add"))
        self.pull_btn.set_text(T("models.pull"))
        self.refresh()


# ============================================================ 服务
class ServiceView(Panel):
    title_key = "svc.title"

    def _build(self):
        self._busy = False
        head = tk.Frame(self, bg=th.c("card"))
        head.pack(fill="x", padx=18, pady=(16, 8))
        self.title = tk.Label(head, text=T("svc.title"), bg=th.c("card"), fg=th.c("text"),
                              font=th.F(15, "bold"))
        self.title.pack(side="left")

        status_card = tk.Frame(self, bg=th.c("card_alt"), padx=18, pady=16)
        status_card.pack(fill="x", padx=18)
        self.status_card = status_card
        left = tk.Frame(status_card, bg=th.c("card_alt"))
        left.pack(side="left")
        dot_row = tk.Frame(left, bg=th.c("card_alt"))
        dot_row.pack(anchor="w")
        self.dot = StatusDot(dot_row, "busy", 14, bg=th.c("card_alt"))
        self.dot.pack(side="left", padx=(0, 8))
        self.status_label = tk.Label(dot_row, text=T("svc.checking"), bg=th.c("card_alt"),
                                     fg=th.c("text"), font=th.F(13, "bold"))
        self.status_label.pack(side="left")
        self.version_label = tk.Label(left, text="", bg=th.c("card_alt"), fg=th.c("text_muted"),
                                      font=th.F(9))
        self.version_label.pack(anchor="w", pady=(8, 0))

        btns = tk.Frame(status_card, bg=th.c("card_alt"))
        btns.pack(side="right")
        self.start_btn = PillButton(btns, T("svc.start"), command=self.start_service,
                                    kind="primary", height=34, radius=9, min_width=96)
        self.start_btn.pack(side="left")
        self.stop_btn = PillButton(btns, T("svc.stop"), command=self.stop_service,
                                   kind="ghost", height=34, radius=9, min_width=96)
        self.stop_btn.pack(side="left", padx=8)
        self.restart_btn = PillButton(btns, T("svc.restart"), command=self.restart_service,
                                      kind="ghost", height=34, radius=9, min_width=96)
        self.restart_btn.pack(side="left")

        info_card = tk.Frame(self, bg=th.c("card"), padx=18, pady=10)
        info_card.pack(fill="x", padx=18, pady=(12, 0))
        self.info_card = info_card
        self.info_values: dict[str, tk.Label] = {}
        rows = [
            ("endpoint", lambda: self.app.cfg.get("host", "")),
            ("version", lambda: self._version_text),
            ("bin", lambda: self.app.cfg.get("ollama_dir", "")),
            ("models_dir", lambda: self.app.cfg.get("models_dir", "")),
            ("disk_used", lambda: self._disk_text),
            ("model_count", lambda: self._count_text),
        ]
        self._info_row_keys = [k for k, _ in rows]
        for idx, (key, getter) in enumerate(rows):
            name = tk.Label(info_card, text="", bg=th.c("card"), fg=th.c("text_muted"),
                            font=th.F(9), anchor="w")
            name.grid(row=idx, column=0, sticky="w", pady=3, padx=(0, 16))
            value = tk.Label(info_card, text="", bg=th.c("card"), fg=th.c("text"),
                             font=th.F(9), anchor="w", justify="left")
            value.grid(row=idx, column=1, sticky="w", pady=3)
            self.info_values[key] = value
            self.info_names = getattr(self, "info_names", {})
            self.info_names[key] = name
            self._info_getters = getattr(self, "_info_getters", {})
            self._info_getters[key] = getter

        proc_head = tk.Frame(self, bg=th.c("card"))
        proc_head.pack(fill="x", padx=18, pady=(16, 4))
        self.proc_title = tk.Label(proc_head, text=T("svc.procs"), bg=th.c("card"),
                                   fg=th.c("text_muted"), font=th.F(10, "bold"))
        self.proc_title.pack(side="left")

        self.proc_table = tk.Frame(self, bg=th.c("card"), padx=18)
        self.proc_table.pack(fill="both", expand=True, pady=(0, 16))
        self._version_text = "-"
        self._disk_text = "-"
        self._count_text = "-"
        self._proc_widgets: list = []

    # ------------------------------------------------ 刷新
    def on_show(self):
        self.tick()
        self.refresh_info()

    def refresh_info(self):
        client = self.app.client
        svc = self.app.svc
        models_dir = self.app.cfg.get("models_dir", "")

        def work():
            info = {"version": "-", "count": 0, "disk": 0}
            try:
                info["version"] = (client.version(timeout=3) or {}).get("version", "-")
            except Exception:
                pass
            try:
                tags = client.tags()
                info["count"] = len(tags)
            except Exception:
                pass
            info["disk"] = svc.dir_size(models_dir)
            return info

        def done(result, error):
            if error or not result:
                return
            self._version_text = result["version"]
            self._count_text = str(result["count"])
            self._disk_text = human_size(result["disk"])
            self._apply_info()

        self.app.run_async(work, done)

    def _apply_info(self):
        for key, getter in self._info_getters.items():
            try:
                self.info_values[key].configure(text=str(getter()))
            except Exception:
                pass

    def tick(self):
        """由主窗口定时调用：更新状态灯与进程表。"""
        state = self.app.status
        if state == "ok":
            self.dot.set_state("ok")
            self.status_label.configure(text=T("svc.running"), fg=th.c("ok"))
        elif state == "err":
            self.dot.set_state("err")
            self.status_label.configure(text=T("svc.stopped"), fg=th.c("err"))
        else:
            self.dot.set_state("busy")
            self.status_label.configure(text=T("svc.checking"), fg=th.c("text_muted"))
        self.version_label.configure(
            text=f"{T('svc.version')} {self.app.server_version or '-'}")
        self._apply_info()
        self._render_procs()

    def _render_procs(self):
        for widget in self.proc_table.winfo_children():
            widget.destroy()
        procs = self.app.svc.list_procs()
        if not procs:
            tk.Label(self.proc_table, text=T("svc.no_proc"), bg=th.c("card"),
                     fg=th.c("text_faint"), font=th.F(9)).pack(anchor="w", pady=8)
            return
        header = tk.Frame(self.proc_table, bg=th.c("card"))
        header.pack(fill="x")
        for text, width, anchor in ((T("svc.proc_name"), 22, "w"), (T("svc.proc_pid"), 10, "w"),
                                    (T("svc.proc_mem"), 12, "e"), (T("svc.proc_cpu"), 10, "e")):
            tk.Label(header, text=text, bg=th.c("card"), fg=th.c("text_faint"),
                     font=th.F(8), width=width, anchor=anchor).pack(side="left")
        for proc in procs:
            row = tk.Frame(self.proc_table, bg=th.c("card"))
            row.pack(fill="x", pady=2)
            tk.Label(row, text=proc["name"], bg=th.c("card"), fg=th.c("text"), font=th.F(9),
                     width=22, anchor="w").pack(side="left")
            tk.Label(row, text=str(proc["pid"]), bg=th.c("card"), fg=th.c("text_muted"),
                     font=th.F(9), width=10, anchor="w").pack(side="left")
            tk.Label(row, text=f"{proc['mem_mb']:.0f} MB", bg=th.c("card"), fg=th.c("text_muted"),
                     font=th.F(9), width=12, anchor="e").pack(side="left")
            tk.Label(row, text=f"{proc['cpu']:.1f} %", bg=th.c("card"), fg=th.c("text_muted"),
                     font=th.F(9), width=10, anchor="e").pack(side="left")

    # ------------------------------------------------ 操作
    def _set_busy(self, busy: bool):
        self._busy = busy
        for btn in (self.start_btn, self.stop_btn, self.restart_btn):
            btn.set_enabled(not busy)

    def start_service(self):
        if self._busy:
            return
        self._set_busy(True)
        svc = self.app.svc

        def work():
            try:
                svc.start()
            except OllamaError as exc:
                text = str(exc)
                if text.startswith("NOT_FOUND::"):
                    path = text.split("::", 1)[1]
                    return {"error": T("svc.no_exe", path)}
                return {"error": text}
            ok = svc.wait_up(45)
            return {"ok": ok}

        def done(result, error):
            self._set_busy(False)
            if error or (result or {}).get("error"):
                message = error or result["error"]
                self.app.toast(T("svc.start_failed", message))
                self.app.log_event("ERROR", f"start: {message}")
            elif result.get("ok"):
                self.app.toast(T("svc.started"))
                self.app.log_event("INFO", "service started")
            else:
                self.app.toast(T("svc.start_failed", "timeout"))
            self.app.refresh_status_now()
            self.refresh_info()

        self.app.run_async(work, done)

    def stop_service(self):
        if self._busy:
            return
        if not confirm(self.winfo_toplevel(), T("svc.stop_confirm")):
            return
        self._set_busy(True)
        svc = self.app.svc

        def done(_res, _err):
            self._set_busy(False)
            self.app.toast(T("svc.stopped_msg"))
            self.app.log_event("INFO", "service stopped")
            self.app.refresh_status_now()
            self.refresh_info()

        self.app.run_async(lambda: svc.stop(), done)

    def restart_service(self):
        if self._busy:
            return
        self._set_busy(True)
        self.app.toast(T("svc.restarting"))
        svc = self.app.svc

        def work():
            svc.stop()
            import time as _t

            _t.sleep(1.2)
            try:
                svc.start()
            except OllamaError as exc:
                return {"error": str(exc)}
            return {"ok": svc.wait_up(60)}

        def done(result, error):
            self._set_busy(False)
            if error or (result or {}).get("error"):
                self.app.toast(T("svc.start_failed", error or result["error"]))
            else:
                self.app.toast(T("svc.started"))
                self.app.log_event("INFO", "service restarted")
            self.app.refresh_status_now()
            self.refresh_info()

        self.app.run_async(work, done)

    # ------------------------------------------------ 主题 / 语言
    def apply_theme(self):
        self.configure(bg=th.c("card"))
        self.title.configure(bg=th.c("card"), fg=th.c("text"))
        self.status_card.configure(bg=th.c("card_alt"))
        for child in self.status_card.winfo_children():
            child.configure(bg=th.c("card_alt"))
            for sub in child.winfo_children():
                if sub is not self.dot:
                    sub.configure(bg=th.c("card_alt"))
        self.dot.apply_theme(th.c("card_alt"))
        self.version_label.configure(bg=th.c("card_alt"), fg=th.c("text_muted"))
        for btn in (self.start_btn, self.stop_btn, self.restart_btn):
            btn.apply_theme(th.c("card_alt"))
        self.info_card.configure(bg=th.c("card"))
        for widget in list(self.info_values.values()) + list(self.info_names.values()):
            widget.configure(bg=th.c("card"))
        self.proc_table.configure(bg=th.c("card"))
        self.proc_title.configure(bg=th.c("card"), fg=th.c("text_muted"))
        self.proc_title.master.configure(bg=th.c("card"))
        self.tick()

    def apply_texts(self):
        self.title.configure(text=T("svc.title"))
        self.start_btn.set_text(T("svc.start"))
        self.stop_btn.set_text(T("svc.stop"))
        self.restart_btn.set_text(T("svc.restart"))
        self.proc_title.configure(text=T("svc.procs"))
        for key, label in self.info_names.items():
            label.configure(text=T(f"svc.{key}") if key != "bin" else T("svc.bin"))
        self.tick()


# ============================================================ 设置
class SettingsView(Panel):
    title_key = "set.title"

    def _build(self):
        self.scroll = ScrollFrame(self, bg_key="card")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=12)
        body = self.scroll.inner
        body.configure(padx=8)

        self.title = tk.Label(body, text=T("set.title"), bg=th.c("card"), fg=th.c("text"),
                              font=th.F(15, "bold"))
        self.title.pack(anchor="w", padx=8, pady=(4, 10))

        self._build_appearance(body)
        self._build_background(body)
        self._build_library(body)
        self._build_about(body)

    # ------------------------------------------------ 小节容器
    def _section(self, parent, title: str) -> tk.Frame:
        card = tk.Frame(parent, bg=th.c("card_alt"), padx=16, pady=14)
        card.pack(fill="x", padx=6, pady=6)
        label = tk.Label(card, text=title, bg=th.c("card_alt"), fg=th.c("text"),
                         font=th.F(11, "bold"))
        label.pack(anchor="w", pady=(0, 10))
        card.title_label = label          # type: ignore[attr-defined]
        return card

    def _row(self, parent, key: str) -> tk.Frame:
        """一行「标签 + 控件」；key 为 i18n 键，切换语言时自动重译。"""
        row = tk.Frame(parent, bg=th.c("card_alt"))
        row.pack(fill="x", pady=4)
        name = tk.Label(row, text=T(key), bg=th.c("card_alt"), fg=th.c("text_muted"),
                        font=th.F(9), width=18, anchor="w")
        name.pack(side="left")
        row.name_label = name             # type: ignore[attr-defined]
        name._i18n_key = key              # type: ignore[attr-defined]
        return row

    # ------------------------------------------------ 外观
    def _build_appearance(self, parent):
        card = self._section(parent, T("set.appearance"))
        self.appearance_card = card
        row = self._row(card, "set.theme")
        self.theme_seg = Segmented(row, [("light", T("set.theme_light")), ("dark", T("set.theme_dark"))],
                                   command=self._on_theme, height=32, radius=9, seg_width=86,
                                   bg=th.c("card_alt"))
        self.theme_seg.pack(side="left")
        self.theme_seg.set_value(th.current())

        row2 = self._row(card, "set.language")
        self.lang_seg = Segmented(row2, [(code, LANG_LABELS[code]) for code in LANGS],
                                  command=self._on_lang, height=32, radius=9, seg_width=86,
                                  bg=th.c("card_alt"))
        self.lang_seg.pack(side="left")

    def _on_theme(self, mode: str):
        th.set_mode(mode)
        self.app.cfg["theme"] = mode
        self.app.save_cfg()

    def _on_lang(self, lang: str):
        from . import i18n

        i18n.set_lang(lang)
        self.app.cfg["lang"] = lang
        self.app.save_config_and_refresh()

    # ------------------------------------------------ 背景
    def _build_background(self, parent):
        card = self._section(parent, T("set.background"))
        self.bg_card = card

        if not HAS_PIL:
            tk.Label(card, text=T("set.bg_need_pillow"), bg=th.c("card_alt"), fg=th.c("warn"),
                     font=th.F(9), wraplength=520, justify="left").pack(anchor="w", pady=(0, 8))

        top = tk.Frame(card, bg=th.c("card_alt"))
        top.pack(fill="x")
        self.bg_preview = tk.Label(top, bg=th.c("card_alt"), bd=0)
        self.bg_preview.pack(side="left")
        info = tk.Frame(top, bg=th.c("card_alt"))
        info.pack(side="left", padx=(14, 0), fill="x", expand=True)
        self.bg_name = tk.Label(info, text="", bg=th.c("card_alt"), fg=th.c("text"),
                                font=th.F(10, "bold"), anchor="w")
        self.bg_name.pack(anchor="w")
        self.bg_path = tk.Label(info, text="", bg=th.c("card_alt"), fg=th.c("text_faint"),
                                font=th.F(8), anchor="w", wraplength=380, justify="left")
        self.bg_path.pack(anchor="w", pady=(4, 8))
        btns = tk.Frame(info, bg=th.c("card_alt"))
        btns.pack(anchor="w")
        self.bg_choose_btn = PillButton(btns, T("set.bg_choose"), command=self.choose_bg,
                                        kind="primary", height=30, radius=9,
                                        bg=th.c("card_alt"), font=th.F(9))
        self.bg_choose_btn.pack(side="left")
        self.bg_clear_btn = PillButton(btns, T("set.bg_clear"), command=self.clear_bg,
                                       kind="ghost", height=30, radius=9,
                                       bg=th.c("card_alt"), font=th.F(9))
        self.bg_clear_btn.pack(side="left", padx=8)

        # 模糊 / 压暗
        for key, label, maximum, getter in (
            ("blur", "set.bg_blur", 40, lambda: self.app.bg.blur),
            ("dim", "set.bg_dim", 85, lambda: self.app.bg.dim),
        ):
            row = self._row(card, label)
            slider = Slider(row, 0, maximum, getter(), command=lambda v, k=key: self._on_bg_slider(k, v),
                            width=220, bg=th.c("card_alt"))
            slider.pack(side="left")
            value_label = tk.Label(row, text=str(int(getter())), bg=th.c("card_alt"),
                                   fg=th.c("text"), font=th.F(9), width=4, anchor="e")
            value_label.pack(side="left", padx=(10, 0))
            if key == "blur":
                self.blur_slider, self.blur_value = slider, value_label
            else:
                self.dim_slider, self.dim_value = slider, value_label

        # 内置渐变
        self.preset_title = tk.Label(card, text=T("set.bg_preset"), bg=th.c("card_alt"),
                                     fg=th.c("text_muted"), font=th.F(9))
        self.preset_title._i18n_key = "set.bg_preset"     # type: ignore[attr-defined]
        self.preset_title.pack(anchor="w", pady=(12, 6))
        self.preset_row = tk.Frame(card, bg=th.c("card_alt"))
        self.preset_row.pack(fill="x")
        self._preset_widgets = {}
        self._render_presets()

        # 背景库
        self.lib_title = tk.Label(card, text=T("set.bg_library"), bg=th.c("card_alt"),
                                  fg=th.c("text_muted"), font=th.F(9))
        self.lib_title._i18n_key = "set.bg_library"       # type: ignore[attr-defined]
        self.lib_title.pack(anchor="w", pady=(14, 6))
        self.lib_row = tk.Frame(card, bg=th.c("card_alt"))
        self.lib_row.pack(fill="x")
        self.lib_hint = tk.Label(card, text=T("set.bg_library_empty"), bg=th.c("card_alt"),
                                 fg=th.c("text_faint"), font=th.F(8))
        self.lib_hint._i18n_key = "set.bg_library_empty"  # type: ignore[attr-defined]
        self.lib_hint.pack(anchor="w", pady=(6, 0))
        self._render_library()
        self._sync_bg_labels()

    def _render_presets(self):
        for child in self.preset_row.winfo_children():
            child.destroy()
        self._preset_widgets.clear()
        for preset_id in PRESET_IDS:
            holder = tk.Frame(self.preset_row, bg=th.c("card_alt"), padx=3, pady=3)
            holder.pack(side="left", padx=(0, 6), pady=2)
            photo = self.app.bg.preset_swatch(preset_id)
            label = tk.Label(holder, image=photo, bd=0, bg=th.c("card_alt"), cursor="hand2")
            label.image = photo           # 保持引用
            label.pack()
            label.bind("<Button-1>", lambda _e, pid=preset_id: self.choose_preset(pid))
            name = tk.Label(holder, text=self.app.bg.preset_label(preset_id, self.app.cfg.get("lang", "zh")),
                            bg=th.c("card_alt"), fg=th.c("text_faint"), font=th.F(7))
            name.pack()
            self._preset_widgets[preset_id] = (holder, photo)

    def _render_library(self):
        for child in self.lib_row.winfo_children():
            child.destroy()
        library = self.app.bg.library
        self.lib_hint.pack_forget()
        if not library:
            self.lib_hint.pack(anchor="w", pady=(6, 0))
            return
        for path in library:
            holder = tk.Frame(self.lib_row, bg=th.c("card_alt"), padx=3, pady=3)
            holder.pack(side="left", padx=(0, 8), pady=2)
            photo = self.app.bg.thumbnail(path)
            label = tk.Label(holder, image=photo, bd=0, bg=th.c("card_alt"), cursor="hand2")
            label.image = photo
            label.pack()
            label.bind("<Button-1>", lambda _e, p=path: self.choose_bg_path(p))
            name = os.path.basename(path)
            caption = tk.Label(holder, text=name[:14], bg=th.c("card_alt"), fg=th.c("text_faint"),
                               font=th.F(7))
            caption.pack()
            tk.Label(holder, text=T("set.bg_remove"), bg=th.c("card_alt"), fg=th.c("err"),
                     font=th.F(7), cursor="hand2").pack()
            holder.winfo_children()[-1].bind(
                "<Button-1>", lambda _e, p=path: self.remove_bg_path(p))

    def _sync_bg_labels(self):
        bg = self.app.bg
        preset_name = bg.preset_label(bg.preset, self.app.cfg.get("lang", "zh"))
        if bg.source:
            self.bg_name.configure(text=os.path.basename(bg.source))
            self.bg_path.configure(text=bg.source)
            photo = bg.thumbnail(bg.source, (160, 94))
        else:
            self.bg_name.configure(text=T("set.bg_preset") + f" · {preset_name}")
            self.bg_path.configure(text="-")
            photo = bg.preset_swatch(bg.preset, (160, 94))
        self.bg_preview.configure(image=photo)
        self.bg_preview.image = photo
        self.blur_slider.set_value(bg.blur)
        self.dim_slider.set_value(bg.dim)
        self.blur_value.configure(text=str(bg.blur))
        self.dim_value.configure(text=str(bg.dim))

    def choose_bg(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title=T("set.bg_choose"),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"), ("All files", "*.*")],
        )
        if path:
            self.choose_bg_path(path)

    def choose_bg_path(self, path: str):
        self.app.bg.set_source(path)
        self.app.apply_background()
        self.app.save_cfg()
        self._sync_bg_labels()
        self._render_presets()
        self._render_library()
        self.app.toast(T("set.bg_applied", os.path.basename(path)))

    def clear_bg(self):
        self.app.bg.set_source("")
        self.app.apply_background()
        self.app.save_cfg()
        self._sync_bg_labels()
        self._render_presets()
        self._render_library()

    def choose_preset(self, preset_id: str):
        self.app.bg.set_preset(preset_id)
        self.app.apply_background()
        self.app.save_cfg()
        self._sync_bg_labels()

    def remove_bg_path(self, path: str):
        self.app.bg.remove_from_library(path)
        self.app.apply_background()
        self.app.save_cfg()
        self._sync_bg_labels()
        self._render_library()

    def _on_bg_slider(self, key: str, value: float):
        if key == "blur":
            self.app.bg.set_blur(value)
            self.blur_value.configure(text=str(int(value)))
        else:
            self.app.bg.set_dim(value)
            self.dim_value.configure(text=str(int(value)))
        self.app.apply_background()
        self.app.save_cfg()

    # ------------------------------------------------ 联网搜索
    def _build_library(self, parent):
        """下载位置（模型库绑定）。"""
        card = self._section(parent, T("lib.section"))
        self.lib_card = card

        self.lib_path = tk.Label(card, text="", bg=th.c("card_alt"), fg=th.c("text"),
                                 font=th.F(9, "bold"), anchor="w", justify="left",
                                 wraplength=560)
        self.lib_path.pack(fill="x")
        self.lib_info = tk.Label(card, text="", bg=th.c("card_alt"), fg=th.c("text_muted"),
                                 font=th.F(8), anchor="w", justify="left",
                                 wraplength=560)
        self.lib_info.pack(fill="x", pady=(4, 0))
        self.lib_server = tk.Label(card, text="", bg=th.c("card_alt"),
                                   fg=th.c("text_faint"), font=th.F(8), anchor="w",
                                   justify="left", wraplength=560)
        self.lib_server.pack(fill="x", pady=(4, 0))

        row = tk.Frame(card, bg=th.c("card_alt"))
        row.pack(fill="x", pady=(10, 0))
        PillButton(row, T("lib.choose"), command=self.choose_library, kind="primary",
                   height=30, radius=9, font=th.F(9), bg=th.c("card_alt")).pack(side="left")
        self.lib_restart_btn = PillButton(row, T("lib.switch_restart"),
                                          command=lambda: self.app.restart_service_for_library(),
                                          kind="ghost", height=30, radius=9, font=th.F(9),
                                          bg=th.c("card_alt"))
        self.lib_restart_btn.pack(side="left", padx=(8, 0))
        self.lib_rescan_btn = PillButton(row, T("lib.rescan"),
                                         command=lambda: self.app.scan_library(notify=True),
                                         kind="ghost", height=30, radius=9, font=th.F(9),
                                         bg=th.c("card_alt"))
        self.lib_rescan_btn.pack(side="left", padx=(8, 0))

        self.lib_history_row = tk.Frame(card, bg=th.c("card_alt"))
        self.lib_history_row.pack(fill="x", pady=(8, 0))

    def choose_library(self):
        from tkinter import filedialog

        current = self.app.cfg.get("models_dir") or ""
        path = filedialog.askdirectory(
            title=T("lib.choose_title"), initialdir=current or None,
            mustexist=False)
        if not path:
            return
        self.app.bind_library(path, restart=True)

    def refresh_library(self):
        if not hasattr(self, "lib_path"):
            return
        app = self.app
        lib = getattr(app, "library", None)
        path = app.cfg.get("models_dir") or ""
        self.lib_path.configure(text=path or T("lib.none"))
        if lib is not None:
            self.lib_info.configure(text=lib.summary())
        server = getattr(app, "server_dir", "") or ""
        if not server:
            self.lib_server.configure(text=T("lib.server_unknown"), fg=th.c("text_faint"))
        elif app.library_mismatch():
            self.lib_server.configure(
                text=T("lib.mismatch", server, path or "-"), fg=th.c("warn"))
        else:
            self.lib_server.configure(text=T("lib.server_match", server), fg=th.c("ok"))
        for child in self.lib_history_row.winfo_children():
            child.destroy()
        history = app.cfg.get("library_history") or []
        if history:
            tk.Label(self.lib_history_row, text=T("lib.history"), bg=th.c("card_alt"),
                     fg=th.c("text_faint"), font=th.F(8)).pack(side="left")
            for item in history[:4]:
                short = item if len(item) <= 34 else "…" + item[-33:]
                PillButton(self.lib_history_row, short, kind="ghost", height=26,
                           radius=7, font=th.F(8), bg=th.c("card_alt"),
                           command=lambda p=item: app.bind_library(p, restart=True)
                           ).pack(side="left", padx=(6, 0))

    def _build_about(self, parent):
        card = self._section(parent, T("set.about"))
        self.about_card = card
        self.about_label = tk.Label(card, text=T("set.about_text", v=__version__),
                                    bg=th.c("card_alt"), fg=th.c("text_muted"), font=th.F(9),
                                    justify="left")
        self.about_label.pack(anchor="w")
        self.config_label = tk.Label(card, text=f"{T('set.config_path')}: {cfgmod.CONFIG_FILE}",
                                     bg=th.c("card_alt"), fg=th.c("text_faint"), font=th.F(8),
                                     wraplength=560, justify="left")
        self.config_label.pack(anchor="w", pady=(8, 8))
        row = tk.Frame(card, bg=th.c("card_alt"))
        row.pack(anchor="w")
        PillButton(row, T("set.open_dir"), kind="ghost", height=30, radius=9,
                   bg=th.c("card_alt"), font=th.F(9),
                   command=lambda: self._open_dir(str(cfgmod.CONFIG_DIR))).pack(side="left")
        PillButton(row, T("svc.models_dir"), kind="ghost", height=30, radius=9,
                   bg=th.c("card_alt"), font=th.F(9),
                   command=lambda: self._open_dir(self.app.cfg.get("models_dir", ""))).pack(
            side="left", padx=8)

    def _open_dir(self, path: str):
        try:
            if path and os.path.exists(path):
                os.startfile(path)       # noqa: S606
            else:
                self.app.toast(T("chat.error", path))
        except Exception as exc:
            self.app.toast(str(exc))

    # ------------------------------------------------ 主题 / 语言
    def apply_theme(self):
        self.configure(bg=th.c("card"))
        self.scroll.apply_theme("card")
        self.title.configure(bg=th.c("card"), fg=th.c("text"))
        for card in self.scroll.inner.winfo_children():
            if not isinstance(card, tk.Frame):
                continue
            card.configure(bg=th.c("card_alt"))
            title_label = getattr(card, "title_label", None)
            if title_label is not None:
                title_label.configure(bg=th.c("card_alt"), fg=th.c("text"))
            for row in card.winfo_children():
                if isinstance(row, tk.Frame):
                    row.configure(bg=th.c("card_alt"))
                    name_label = getattr(row, "name_label", None)
                    if name_label is not None:
                        name_label.configure(bg=th.c("card_alt"), fg=th.c("text_muted"))
                    for child in row.winfo_children():
                        try:
                            child.configure(bg=th.c("card_alt"))
                        except Exception:
                            pass
                else:
                    try:
                        row.configure(bg=th.c("card_alt"))
                    except Exception:
                        pass
        for widget in (self.bg_preview, self.bg_name, self.bg_path, self.preset_row,
                       self.lib_row, self.lib_hint):
            widget.configure(bg=th.c("card_alt"))
        self.bg_name.configure(fg=th.c("text"))
        self.bg_path.configure(fg=th.c("text_faint"))
        self.lib_hint.configure(fg=th.c("text_faint"))
        for widget in (self.lib_path, self.lib_info, self.lib_server,
                       self.lib_history_row):
            widget.configure(bg=th.c("card_alt"))
        for widget in (self.lib_restart_btn, self.lib_rescan_btn):
            widget.apply_theme(th.c("card_alt"))
        for btn in (self.bg_choose_btn, self.bg_clear_btn):
            btn.apply_theme(th.c("card_alt"))
        self.blur_slider.apply_theme(th.c("card_alt"))
        self.dim_slider.apply_theme(th.c("card_alt"))
        self._render_presets()
        self._render_library()
        self._sync_bg_labels()

    def apply_texts(self):
        self.title.configure(text=T("set.title"))
        self.appearance_card.title_label.configure(text=T("set.appearance"))
        self.bg_card.title_label.configure(text=T("set.background"))
        self.about_card.title_label.configure(text=T("set.about"))
        # 分段控件与行标签需要重译
        self.theme_seg.set_options([("light", T("set.theme_light")),
                                    ("dark", T("set.theme_dark"))], keep_value=True)
        self.lang_seg.set_options([(code, LANG_LABELS[code]) for code in LANGS], keep_value=True)
        for card in self.scroll.inner.winfo_children():
            for row in card.winfo_children() if isinstance(card, tk.Frame) else []:
                name_label = getattr(row, "name_label", None)
                key = getattr(name_label, "_i18n_key", None) if name_label else None
                if key:
                    name_label.configure(text=T(key))
        self.bg_choose_btn.set_text(T("set.bg_choose"))
        self.bg_clear_btn.set_text(T("set.bg_clear"))
        self.about_label.configure(text=T("set.about_text", v=__version__))
        self.config_label.configure(text=f"{T('set.config_path')}: {cfgmod.CONFIG_FILE}")
        self._sync_bg_labels()
        self._render_presets()

    def on_show(self):
        self.theme_seg.set_value(th.current())
        self.lang_seg.set_value(self.app.cfg.get("lang", "zh"))
        self.refresh_library()
        self._sync_bg_labels()
        self._render_presets()
        self._render_library()


class LogsView(Panel):
    """日志页：应用日志 + Ollama 服务日志，可复制 / 清空。"""

    def _build(self):
        self.source = "app"
        head = tk.Frame(self, bg=th.c("card"))
        head.pack(fill="x", padx=18, pady=(16, 8))
        self.title = tk.Label(head, text=T("log.title"), bg=th.c("card"), fg=th.c("text"),
                              font=th.F(15, "bold"))
        self.title.pack(side="left")
        self.refresh_btn = PillButton(head, T("log.refresh"), command=self.reload, kind="ghost",
                                      height=30, radius=9)
        self.refresh_btn.pack(side="right")
        self.clear_btn = PillButton(head, T("log.clear"), command=self.clear_app_log,
                                    kind="ghost", height=30, radius=9)
        self.clear_btn.pack(side="right", padx=(0, 8))
        self.copy_btn = PillButton(head, T("log.copy"), command=self.copy_all, kind="ghost",
                                   height=30, radius=9)
        self.copy_btn.pack(side="right", padx=(0, 8))

        self.switcher = Segmented(self, [("app", T("log.app")), ("server", T("log.server"))],
                                  command=self._on_switch, height=34, radius=10, seg_width=120,
                                  bg=th.c("card"))
        self.switcher.pack(anchor="w", padx=18, pady=(0, 8))

        holder = tk.Frame(self, bg=th.c("card_alt"), padx=2, pady=2)
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.text = tk.Text(holder, wrap="none", bd=0, relief="flat", bg=th.c("card_alt"),
                            fg=th.c("text"), font=th.MONO(9), padx=12, pady=10,
                            insertbackground=th.c("accent"), highlightthickness=0)
        scroll = tk.Scrollbar(holder, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")

    def _on_switch(self, value: str):
        self.source = value
        self.reload()

    def on_show(self):
        self.reload()

    def reload(self):
        if self.source == "app":
            content = self.app.event_log.text()
        else:
            path = os.path.join(self.app.cfg.get("ollama_dir", ""), "serve.err.log")
            content = read_tail(path) or ""
            if not content.strip():
                content = f"({path})"
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content or T("log.empty"))
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear_app_log(self):
        self.app.event_log.clear()
        self.app.toast(T("log.cleared"))
        self.reload()

    def copy_all(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end").strip())
        self.app.toast(T("chat.copied"))

    def apply_theme(self):
        self.configure(bg=th.c("card"))
        self.title.configure(bg=th.c("card"), fg=th.c("text"))
        for btn in (self.refresh_btn, self.clear_btn, self.copy_btn):
            btn.apply_theme(th.c("card"))
        self.switcher.apply_theme(th.c("card"))
        self.text.configure(bg=th.c("card_alt"), fg=th.c("text"),
                            insertbackground=th.c("accent"))
        self.text.master.configure(bg=th.c("card_alt"))

    def apply_texts(self):
        self.title.configure(text=T("log.title"))
        self.refresh_btn.set_text(T("log.refresh"))
        self.clear_btn.set_text(T("log.clear"))
        self.copy_btn.set_text(T("log.copy"))
        self.switcher.set_options([("app", T("log.app")), ("server", T("log.server"))])
        self.reload()
