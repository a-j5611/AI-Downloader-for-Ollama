"""自绘 UI 组件：圆角按钮 / 分段控件 / 开关 / 状态灯 / 滚动容器 / 进度条 / 弹窗。"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import theme as th
from .i18n import t as T

_font_cache: dict = {}


def _font(spec):
    key = tuple(spec) if isinstance(spec, (tuple, list)) else spec
    if key not in _font_cache:
        _font_cache[key] = tkfont.Font(font=spec if not isinstance(spec, str) else (spec,))
    return _font_cache[key]


def text_width(text: str, spec) -> int:
    try:
        return _font(spec).measure(text)
    except Exception:
        return len(text) * 8


def _corner_arc(cx, cy, r, start_deg, end_deg, steps=8):
    """生成一段圆弧上的点（角度制，屏幕坐标 y 向下）。"""
    import math

    pts = []
    for i in range(steps + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        pts.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """画圆角矩形，返回 item id。

    用**密集圆弧多边形**（每角 8 段）而不是稀疏样条：
    Canvas 没有抗锯齿，点越密轮廓越平顺，边缘看起来就不"硬"了。
    """
    r = max(0.0, min(float(r), (x2 - x1) / 2, (y2 - y1) / 2))
    if r <= 0.5:
        return canvas.create_polygon(
            [x1, y1, x2, y1, x2, y2, x1, y2], smooth=False, **kwargs)
    pts = []
    pts += _corner_arc(x1 + r, y1 + r, r, 180, 270)      # 左上
    pts += _corner_arc(x2 - r, y1 + r, r, 270, 360)      # 右上
    pts += _corner_arc(x2 - r, y2 - r, r, 0, 90)         # 右下
    pts += _corner_arc(x1 + r, y2 - r, r, 90, 180)       # 左下
    return canvas.create_polygon(pts, smooth=False, **kwargs)


def soft_round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, fill: str, bg: str,
                    outline: str = "", width: int = 1, soften: float = 0.45):
    """带柔化边缘的圆角矩形。

    先画一圈「填充色 ↔ 背景色」的混色环（比本体大 1px），
    再画本体，轮廓与背景之间就有了过渡，不再是生硬的色块切线。
    """
    if bg and bg.lower() != fill.lower():
        ring = th.blend(fill, bg, soften)
        round_rect(canvas, x1 - 0.6, y1 - 0.6, x2 + 0.6, y2 + 0.6, r + 0.6,
                   fill=ring, outline="")
    return round_rect(canvas, x1, y1, x2, y2, r, fill=fill, outline=outline,
                      width=width)


def _parent_bg(widget) -> str:
    try:
        return widget.cget("background")
    except Exception:
        try:
            return widget.cget("bg")
        except Exception:
            return th.c("card")


# ============================================================ 圆角按钮
class PillButton(tk.Canvas):
    def __init__(self, parent, text: str = "", command=None, kind: str = "primary",
                 height: int = 34, radius: int = 10, min_width: int = 0,
                 font=None, pad: int = 16, bg: str | None = None):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, height=height, highlightthickness=0, bd=0, bg=self._bg_owner)
        self._text = text
        self._command = command
        self._kind = kind
        self._radius = radius
        self._font = font or th.F(10, "bold" if kind == "primary" else "normal")
        self._pad = pad
        self._min_width = min_width
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._ch = height
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._redraw()

    # ------------------------------------------------ 内部
    def _colors(self):
        """返回 (填充色, 文字色, 描边色)；Windows 11 按钮语义：

        primary  = 强调色实心按钮
        secondary= control 填充 + 描边（默认）
        ghost    = 平时透明、悬停才显出 control 填充（Win11 subtle button）
        danger   = 错误色实心
        """
        if not self._enabled:
            return th.c("control"), th.c("text_faint"), th.c("border")
        kind = self._kind
        if kind == "primary":
            fill = th.c("accent_hover") if self._hover else (
                th.c("accent_pressed") if self._pressed else th.c("accent"))
            return fill, th.c("accent_text"), fill
        if kind == "danger":
            fill = th.c("err") if not self._hover else th.shade(th.c("err"), 0.12)
            return fill, "#FFFFFF", fill
        if kind == "ghost":
            if self._pressed:
                return th.c("card_alt"), th.c("text"), th.c("card_alt")
            if self._hover:
                return th.c("control_hover"), th.c("text"), th.c("control_hover")
            return self._bg_owner, th.c("text"), self._bg_owner
        if kind == "flat":
            return self._bg_owner, th.c("text"), self._bg_owner
        # secondary
        fill = th.c("control_hover") if (self._hover or self._pressed) else th.c("control")
        return fill, th.c("text"), th.c("border")

    def _redraw(self):
        self.delete("all")
        bg, fg, stroke = self._colors()
        # 宽度由文字实测宽度决定（不能用 <Configure> 回写，否则文字会被裁掉）
        w = max(text_width(self._text, self._font) + self._pad * 2, self._min_width, 28)
        h = self._ch
        if self._kind != "flat":
            radius = min(self._radius, 6)          # 略微放宽圆角，视觉更柔和
            if self._kind in ("primary", "danger"):
                soft_round_rect(self, 1, 1, w - 1, h - 1, radius, fill=bg,
                                bg=self._bg_owner)
            else:
                # 次要/幽灵按钮：柔化过渡 + 1px 描边
                soft_round_rect(self, 1, 1, w - 1, h - 1, radius, fill=bg,
                                bg=self._bg_owner,
                                outline=stroke if stroke != self._bg_owner else "", width=1)
        self.create_text(w / 2, h / 2 + 1, text=self._text, fill=fg, font=self._font)
        self.configure(width=w)

    def _on_enter(self, _e=None):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()

    def _on_click(self, _e=None):
        self._pressed = False
        self._redraw()
        if self._enabled and self._command:
            self._command()

    # ------------------------------------------------ 公开
    def set_text(self, text: str):
        self._text = text
        self._redraw()

    def set_kind(self, kind: str):
        self._kind = kind
        self._redraw()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._redraw()

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 分段控件
class Segmented(tk.Canvas):
    def __init__(self, parent, options: list, command=None, height: int = 34,
                 radius: int = 10, seg_width: int = 92, bg: str | None = None):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, height=height, highlightthickness=0, bd=0, bg=self._bg_owner)
        self._segs = list(options)      # [(value, label), ...]
        self._value = self._segs[0][0] if self._segs else None
        self._command = command
        self._radius = radius
        self._seg_w = seg_width
        self._ch = height
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        n = max(len(self._segs), 1)
        w = self._seg_w * n
        h = self._ch
        radius = min(self._radius, 5)
        round_rect(self, 0, 0, w, h, radius, fill=th.c("control"), outline=th.c("border"))

        for i, (value, label) in enumerate(self._segs):
            x1 = i * self._seg_w
            x2 = x1 + self._seg_w
            active = value == self._value
            if active:
                soft_round_rect(self, x1 + 2, 2, x2 - 2, h - 2, max(3, radius - 1),
                                fill=th.c("accent"), bg=th.c("control"), soften=0.5)
            # 标签过长会溢出到相邻格，这里自动截断加省略号
            font = th.F(10, "bold" if active else "normal")
            avail = self._seg_w - 14
            text = label
            if text_width(text, font) > avail:
                while text and text_width(text + "…", font) > avail:
                    text = text[:-1]
                text = (text + "…") if text else ""
            self.create_text((x1 + x2) / 2, h / 2 + 1, text=text,
                             fill=th.c("accent_text") if active else th.c("text"),
                             font=font)
        self.configure(width=w)

    def _on_click(self, event):
        n = max(len(self._segs), 1)
        idx = int(event.x // self._seg_w)
        idx = max(0, min(n - 1, idx))
        value = self._segs[idx][0]
        if value != self._value:
            self._value = value
            self._redraw()
            if self._command:
                self._command(value)

    # ------------------------------------------------ 公开
    def set_value(self, value, silent: bool = True):
        if value == self._value:
            return
        self._value = value
        self._redraw()
        if not silent and self._command:
            self._command(value)

    def get_value(self):
        return self._value

    def set_options(self, options: list, keep_value: bool = True):
        self._segs = list(options)
        values = [v for v, _ in self._segs]
        if not keep_value or self._value not in values:
            self._value = values[0] if values else None
        self._redraw()

    def set_labels(self, labels: list):
        self._segs = [(v, lab) for (v, _), lab in zip(self._segs, labels)]
        self._redraw()

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 开关
class Toggle(tk.Canvas):
    def __init__(self, parent, value: bool = False, command=None,
                 width: int = 46, height: int = 26, bg: str | None = None):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0,
                         bg=self._bg_owner)
        self._value = bool(value)
        self._command = command
        self._cw, self._ch = width, height
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w, h = self._cw, self._ch
        if self._value:
            soft_round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=th.c("accent"),
                            bg=self._bg_owner, outline="", soften=0.5)
            knob = "#FFFFFF"
        else:
            round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=th.c("control"),
                       outline=th.c("border"))
            knob = th.c("text_faint")
        r = h / 2 - 4
        cx = (w - h / 2) if self._value else (h / 2)
        cy = h / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=knob,
                         outline="" if self._value else th.c("text_faint"))

    def _on_click(self, _e=None):
        self._value = not self._value
        self._redraw()
        if self._command:
            self._command(self._value)

    def set_value(self, value: bool, silent: bool = True):
        self._value = bool(value)
        self._redraw()
        if not silent and self._command:
            self._command(self._value)

    def get_value(self) -> bool:
        return self._value

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 状态灯
class StatusDot(tk.Canvas):
    def __init__(self, parent, state: str = "err", size: int = 14, bg: str | None = None):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, width=size, height=size, highlightthickness=0, bd=0, bg=self._bg_owner)
        self._state = state
        self._size = size
        self._redraw()

    def set_state(self, state: str):
        if state != self._state:
            self._state = state
            self._redraw()

    def _redraw(self):
        self.delete("all")
        s = self._size
        color = {"ok": th.c("ok"), "err": th.c("err"), "busy": th.c("warn")}.get(self._state, th.c("text_faint"))
        pad = 2
        self.create_oval(pad, pad, s - pad, s - pad, fill=color, outline="")
        if self._state == "ok":
            self.create_oval(pad - 1, pad - 1, s - pad + 1, s - pad + 1, outline="", fill="")

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 细滚动条样式
def style_scrollbar(style: ttk.Style) -> None:
    # Win11 滚动条：细、无箭头、悬停变深
    style.configure(
        "DS.Vertical.TScrollbar",
        gripcount=0, background=th.c("scroll"), darkcolor=th.c("scroll"),
        lightcolor=th.c("scroll"), troughcolor=th.c("card"), bordercolor=th.c("card"),
        arrowcolor=th.c("card"), relief="flat", arrowsize=1, width=8,
        borderwidth=0,
    )
    style.map(
        "DS.Vertical.TScrollbar",
        background=[("active", th.c("text_faint")), ("pressed", th.c("accent"))],
        arrowcolor=[("active", th.c("card"))],
    )


# ============================================================ 滚动容器
class ScrollFrame(tk.Frame):
    """内嵌 canvas 的纵向滚动容器，内容加到 self.inner。"""

    def __init__(self, parent, bg_key: str = "card", padding: int = 0):
        super().__init__(parent, bg=th.c(bg_key), bd=0, highlightthickness=0)
        self._bg_key = bg_key
        self.canvas = tk.Canvas(self, bg=th.c(bg_key), highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", style="DS.Vertical.TScrollbar",
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=th.c(bg_key), bd=0, highlightthickness=0)
        self._win = self.canvas.create_window((padding, padding), window=self.inner, anchor="nw")
        self._padding = padding
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self._is_scroll_area = True         # 供全局滚轮路由识别

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=max(1, event.width - self._padding * 2))
        self._on_inner()

    def scroll_by(self, units: int):
        self.canvas.yview_scroll(units, "units")

    def scroll_top(self):
        self.canvas.yview_moveto(0)

    def scroll_bottom(self):
        self.canvas.yview_moveto(1.0)

    def at_bottom(self, tol: float = 0.02) -> bool:
        try:
            return self.canvas.yview()[1] >= 1.0 - tol
        except Exception:
            return True

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()

    def apply_theme(self, bg_key: str | None = None):
        if bg_key:
            self._bg_key = bg_key
        color = th.c(self._bg_key)
        self.configure(bg=color)
        self.canvas.configure(bg=color)
        self.inner.configure(bg=color)


# ============================================================ 进度条
class ProgressBar(tk.Canvas):
    def __init__(self, parent, height: int = 8, bg: str | None = None, radius: int = 4):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, height=height, highlightthickness=0, bd=0, bg=self._bg_owner)
        self._value = 0.0
        self._ch = height
        self._radius = radius
        self.bind("<Configure>", lambda e: self._redraw())
        self._redraw()

    def set_value(self, value: float):
        self._value = max(0.0, min(1.0, float(value)))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 10)
        h = self._ch
        r = min(h / 2, self._radius)
        round_rect(self, 0, 0, w, h, r, fill=th.c("trough"), outline="")
        fill_w = int(w * self._value)
        if fill_w > 2:
            soft_round_rect(self, 0, 0, fill_w, h, min(r, fill_w / 2),
                            fill=th.c("accent"), bg=th.c("trough"), soften=0.5)

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 滑条
class Slider(tk.Canvas):
    """Canvas 自绘滑条（可点击/拖拽），比 tk.Scale 更贴合主题。"""

    def __init__(self, parent, from_: float = 0, to: float = 100, value: float = 0,
                 command=None, width: int = 240, height: int = 26,
                 resolution: float = 1, bg: str | None = None):
        self._bg_owner = bg or _parent_bg(parent)
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0,
                         bg=self._bg_owner)
        self._from = float(from_)
        self._to = float(to)
        self._res = float(resolution) or 1.0
        self._value = self._clamp(float(value))
        self._command = command
        self._cw = width
        self._ch = height
        self._dragging = False
        self._pad = 10
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    # --------------------------------------------- 内部
    def _clamp(self, v: float) -> float:
        v = max(self._from, min(self._to, v))
        steps = round((v - self._from) / self._res)
        return max(self._from, min(self._to, self._from + steps * self._res))

    def _pos_to_value(self, x: float) -> float:
        span = max(self._cw - self._pad * 2, 1)
        ratio = (x - self._pad) / span
        ratio = max(0.0, min(1.0, ratio))
        return self._clamp(self._from + ratio * (self._to - self._from))

    def _value_to_x(self) -> float:
        span = self._cw - self._pad * 2
        rng = (self._to - self._from) or 1
        return self._pad + (self._value - self._from) / rng * span

    def _redraw(self):
        self.delete("all")
        h = self._ch
        cy = h / 2
        x1, x2 = self._pad, self._cw - self._pad
        round_rect(self, x1, cy - 2, x2, cy + 2, 2, fill=th.c("trough"), outline="")
        kx = self._value_to_x()
        if kx > x1 + 1:
            round_rect(self, x1, cy - 2, kx, cy + 2, 2, fill=th.c("accent"), outline="")
        r = 9 if self._dragging else 8
        # 滑块：强调色圆点 + 一圈卡片色描边（Win11 滑条的样子）
        self.create_oval(kx - r - 2, cy - r - 2, kx + r + 2, cy + r + 2,
                         fill=th.c("card"), outline=th.c("border"))
        self.create_oval(kx - r, cy - r, kx + r, cy + r,
                         fill=th.c("accent"), outline=th.c("accent"))

    def _on_configure(self, event):
        if abs(event.width - self._cw) > 1:
            self._cw = event.width
            self._redraw()

    def _on_press(self, event):
        self._dragging = True
        self._value = self._pos_to_value(event.x)
        self._redraw()
        if self._command:
            self._command(self._value)

    def _on_drag(self, event):
        if not self._dragging:
            return
        new = self._pos_to_value(event.x)
        if abs(new - self._value) >= self._res / 2:
            self._value = new
            self._redraw()
            if self._command:
                self._command(self._value)

    def _on_release(self, _e=None):
        self._dragging = False

    # --------------------------------------------- 公开
    def set_value(self, value, silent: bool = True):
        self._value = self._clamp(float(value))
        self._redraw()
        if not silent and self._command:
            self._command(self._value)

    def get_value(self) -> float:
        return self._value

    def apply_theme(self, bg: str | None = None):
        if bg:
            self._bg_owner = bg
        self.configure(bg=self._bg_owner)
        self._redraw()


# ============================================================ 弹窗
class Modal(tk.Toplevel):
    """主题化确认 / 提示弹窗。result 为 True/False。"""

    def __init__(self, parent, title: str, message: str, kind: str = "confirm",
                 ok_text: str | None = None, cancel_text: str | None = None):
        super().__init__(parent)
        self.result = False
        self.withdraw()
        self.title(title)
        self.configure(bg=th.c("card"))
        self.resizable(False, False)
        self.transient(parent)
        wrap = 380
        body = tk.Frame(self, bg=th.c("card"), padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=th.c("card"), fg=th.c("text"),
                 font=th.F(12, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=message, bg=th.c("card"), fg=th.c("text_muted"),
                 font=th.F(10), justify="left", wraplength=wrap, anchor="w").pack(fill="x", pady=(10, 18))
        row = tk.Frame(body, bg=th.c("card"))
        row.pack(fill="x")
        if kind == "confirm":
            PillButton(row, cancel_text or T("cancel"), command=self._cancel, kind="ghost").pack(side="right")
            PillButton(row, ok_text or T("ok"), command=self._ok, kind="primary").pack(side="right", padx=(0, 10))
        else:
            PillButton(row, ok_text or T("ok"), command=self._ok, kind="primary").pack(side="right")
        self.update_idletasks()
        self._center(parent)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.focus_force()
        self.wait_window(self)

    def _center(self, parent):
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _ok(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()


def confirm(parent, message: str, title: str | None = None) -> bool:
    return Modal(parent, title or T("confirm"), message, kind="confirm").result


def alert(parent, message: str, title: str | None = None) -> None:
    Modal(parent, title or T("info"), message, kind="info").result


class TextPrompt(tk.Toplevel):
    """主题化的单行文本输入弹窗（用于重命名等）。result 为 str 或 None。"""

    def __init__(self, parent, title: str, message: str = "", initial: str = "",
                 ok_text: str | None = None, cancel_text: str | None = None):
        super().__init__(parent)
        self.result: str | None = None
        self.withdraw()
        self.title(title)
        self.configure(bg=th.c("card"))
        self.resizable(False, False)
        self.transient(parent)

        body = tk.Frame(self, bg=th.c("card"), padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=th.c("card"), fg=th.c("text"),
                 font=th.F(12, "bold"), anchor="w").pack(fill="x")
        if message:
            tk.Label(body, text=message, bg=th.c("card"), fg=th.c("text_muted"),
                     font=th.F(9), justify="left", wraplength=360, anchor="w").pack(
                fill="x", pady=(8, 0))
        self.entry = tk.Entry(body, bd=0, relief="flat", bg=th.c("card_alt"),
                              fg=th.c("text"), insertbackground=th.c("accent"),
                              font=th.F(10), highlightthickness=1,
                              highlightbackground=th.c("border"),
                              highlightcolor=th.c("accent"), width=34)
        self.entry.pack(fill="x", ipady=7, pady=(12, 16))
        self.entry.insert(0, initial or "")
        self.entry.select_range(0, "end")

        row = tk.Frame(body, bg=th.c("card"))
        row.pack(fill="x")
        PillButton(row, cancel_text or T("cancel"), command=self._cancel, kind="ghost",
                   bg=th.c("card")).pack(side="right")
        PillButton(row, ok_text or T("ok"), command=self._ok, kind="primary",
                   bg=th.c("card")).pack(side="right", padx=(0, 10))

        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            self.geometry(f"+{px + max(0, (pw - self.winfo_width()) // 2)}"
                          f"+{py + max(0, (ph - self.winfo_height()) // 3)}")
        except Exception:
            pass
        self.deiconify()
        self.grab_set()
        self.entry.focus_force()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.wait_window(self)

    def _ok(self):
        self.result = self.entry.get().strip()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


def ask_text(parent, title: str, message: str = "", initial: str = "") -> str | None:
    return TextPrompt(parent, title, message, initial).result


class TextDialog(tk.Toplevel):
    """主题化的只读文本查看窗（用于查看注入模型的资料）。"""

    def __init__(self, parent, title: str, text: str, hint: str = ""):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=th.c("card"))
        self.geometry("840x620")
        self.transient(parent)

        body = tk.Frame(self, bg=th.c("card"), padx=16, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=th.c("card"), fg=th.c("text"),
                 font=th.F(12, "bold"), anchor="w").pack(fill="x")
        if hint:
            tk.Label(body, text=hint, bg=th.c("card"), fg=th.c("text_faint"),
                     font=th.F(8), anchor="w").pack(fill="x", pady=(4, 8))

        holder = tk.Frame(body, bg=th.c("card_alt"), padx=2, pady=2)
        holder.pack(fill="both", expand=True)
        wrap = tk.Text(holder, wrap="word", bd=0, relief="flat", bg=th.c("card_alt"),
                       fg=th.c("text"), font=th.F(9), padx=10, pady=8,
                       insertbackground=th.c("accent"), highlightthickness=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", style="DS.Vertical.TScrollbar",
                               command=wrap.yview)
        wrap.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        wrap.pack(fill="both", expand=True)
        wrap.insert("1.0", text or "")
        wrap.configure(state="disabled")

        row = tk.Frame(body, bg=th.c("card"))
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text=f"{len(text or '')} 字符", bg=th.c("card"),
                 fg=th.c("text_faint"), font=th.F(8)).pack(side="left")
        PillButton(row, T("close"), command=self.destroy, kind="primary",
                   bg=th.c("card")).pack(side="right")
        PillButton(row, T("chat.copy"), kind="ghost", bg=th.c("card"),
                   command=lambda: self._copy(text)).pack(side="right", padx=(0, 8))
        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            self.geometry(f"+{px + max(0, (pw - self.winfo_width()) // 2)}"
                          f"+{py + max(0, (ph - self.winfo_height()) // 4)}")
        except Exception:
            pass

    def _copy(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text or "")
        except Exception:
            pass


def show_text(parent, title: str, text: str, hint: str = "") -> None:
    TextDialog(parent, title, text, hint)
