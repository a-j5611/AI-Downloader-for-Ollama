"""Windows 11 风格主题：色板、强调色（accent）、跟随系统、字体。

颜色取自 Windows 11 / WinUI 的官方设计令牌：
  浅色  backdrop #F3F3F3 · card #FFFFFF · stroke #E1E1E1
        text #1A1A1A · secondary #5D5D5D · tertiary #9A9A9A
        accent（强调色浅色变体）例如蓝 #0067C0，其上文字为白色
  深色  backdrop #202020 · card #2B2B2B · control #383838 · stroke #3D3D3D
        text #FFFFFF · secondary #C5C5C5 · tertiary #8D8D8D
        accent（强调色深色变体）例如蓝 #60CDFF，其上文字为黑色

强调色可以在「设置 → 外观」里切换，也可「跟随 Windows」——
读 DWM 的 ColorizationColor，再吸附到最接近的预设（这样悬停/按下的深浅变体仍然成立）。
"""
from __future__ import annotations

import ctypes

# ---------------------------------------------------------------- 强调色预设
# name: (浅色主题用值, 深色主题用值, 中文名, 英文名)
ACCENTS: dict[str, tuple[str, str, str, str]] = {
    "blue":   ("#0067C0", "#60CDFF", "蓝", "Blue"),
    "teal":   ("#038387", "#59D9D5", "青", "Teal"),
    "green":  ("#107C10", "#6CCB5F", "绿", "Green"),
    "purple": ("#744DA9", "#B894F5", "紫", "Purple"),
    "pink":   ("#C239B3", "#F2A6E8", "粉", "Pink"),
    "red":    ("#C42B1C", "#FF99A4", "红", "Red"),
    "orange": ("#CA5010", "#FFB877", "橙", "Orange"),
    "gray":   ("#5D6B70", "#B0B7BC", "灰", "Gray"),
}
ACCENT_ORDER = ("blue", "teal", "green", "purple", "pink", "red", "orange", "gray")


# ---------------------------------------------------------------- 颜色工具
def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(r: int, g: int, b: int) -> str:
    clamp = lambda v: max(0, min(255, int(round(v))))
    return f"#{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"


def shade(color: str, factor: float) -> str:
    """factor > 0 变亮，< 0 变暗（0.08 = 8%）。"""
    r, g, b = _rgb(color)
    if factor >= 0:
        return _hex(r + (255 - r) * factor, g + (255 - g) * factor, b + (255 - b) * factor)
    f = 1 + factor
    return _hex(r * f, g * f, b * f)


def blend(a: str, b: str, t: float) -> str:
    """把 a 按比例 t 混入 b（t=0 -> b，t=1 -> a）。"""
    ar, ag, ab = _rgb(a)
    br, bg, bb = _rgb(b)
    return _hex(ar * t + br * (1 - t), ag * t + bg * (1 - t), ab * t + bb * (1 - t))


# ---------------------------------------------------------------- 基准色板
LIGHT = {
    "root":         "#F3F3F3",
    "card":         "#FFFFFF",
    "card_alt":     "#F7F7F7",
    "card_hover":   "#F0F0F0",
    "sidebar":      "#F3F3F3",
    "topbar":       "#F3F3F3",
    "control":      "#FFFFFF",
    "control_hover":"#F6F6F6",
    "text":         "#1A1A1A",
    "text_muted":   "#5D5D5D",
    "text_faint":   "#9A9A9A",
    "border":       "#E1E1E1",
    "divider":      "#EDEDED",
    "chip":         "#EBEBEB",
    "chip_text":    "#3A3A3A",
    "nav_hover":    "#EDEDED",
    "nav_active":   "#E8E8E8",
    "scroll":       "#C6C6C6",
    "trough":       "#E8E8E8",
    "ok":           "#0F7B0F",
    "err":          "#C42B1C",
    "warn":         "#9D5D00",
    "shadow":       "#DDDDDD",
    # 仅旧界面用到，保留键位
    "bubble_user":  "#E8F1FB",
    "bubble_ai":    "#F7F7F7",
    "bubble_think": "#FFF7E0",
    "think_text":   "#7A5C14",
}

DARK = {
    "root":         "#202020",
    "card":         "#2B2B2B",
    "card_alt":     "#323232",
    "card_hover":   "#3A3A3A",
    "sidebar":      "#202020",
    "topbar":       "#202020",
    "control":      "#383838",
    "control_hover":"#3F3F3F",
    "text":         "#FFFFFF",
    "text_muted":   "#C5C5C5",
    "text_faint":   "#8D8D8D",
    "border":       "#3D3D3D",
    "divider":      "#3A3A3A",
    "chip":         "#3A3A3A",
    "chip_text":    "#E4E4E4",
    "nav_hover":    "#2D2D2D",
    "nav_active":   "#333333",
    "scroll":       "#5A5A5A",
    "trough":       "#333333",
    "ok":           "#6CCB5F",
    "err":          "#FF99A4",
    "warn":         "#FCE100",
    "shadow":       "#151515",
    "bubble_user":  "#2A3A4D",
    "bubble_ai":    "#303030",
    "bubble_think": "#3A3320",
    "think_text":   "#E0C37A",
}

THEMES = {"light": LIGHT, "dark": DARK}
MODES = ("light", "dark", "auto")

_current = "dark"
_accent = "blue"
_listeners: list = []
# 由 set_accent() 计算出的派生色，c() 直接取
_DERIVED: dict[str, str] = {}


def current() -> str:
    return _current


def is_dark() -> bool:
    return _current == "dark"


def accent_name() -> str:
    return _accent


# ---------------------------------------------------------------- 跟随 Windows
def system_mode() -> str:
    """读 Windows 的「应用模式」：1 = 浅色，0 = 深色。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
    except Exception:
        return "dark"


def system_accent_rgb() -> tuple[int, int, int] | None:
    """取 Windows 的强调色（DWM 的 ColorizationColor）。"""
    try:
        color = ctypes.c_uint32(0)
        opaque = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetColorizationColor(
            ctypes.byref(color), ctypes.byref(opaque))
        value = int(color.value)
        if value:
            return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    except Exception:
        pass
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value, _ = winreg.QueryValueEx(key, "ColorizationColor")
            value = int(value)
            return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    except Exception:
        return None


def system_accent_name() -> str:
    """把系统强调色吸附到最接近的预设。"""
    rgb = system_accent_rgb()
    if not rgb:
        return _accent or "blue"
    best, best_dist = "blue", None
    for name in ACCENT_ORDER:
        light, dark, _zh, _en = ACCENTS[name]
        for hex_color in (light, dark):
            r, g, b = _rgb(hex_color)
            dist = (r - rgb[0]) ** 2 + (g - rgb[1]) ** 2 + (b - rgb[2]) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = name, dist
    return best


def resolve(mode: str = "auto", accent: str = "auto") -> tuple[str, str]:
    """把配置里的 auto 解析成具体值。"""
    real_mode = system_mode() if mode == "auto" else (mode if mode in ("light", "dark") else "dark")
    real_accent = system_accent_name() if accent == "auto" else (
        accent if accent in ACCENTS else "blue")
    return real_mode, real_accent


# ---------------------------------------------------------------- 切换
def set_accent(name: str) -> None:
    global _accent
    if name not in ACCENTS or name == _accent:
        return
    _accent = name
    _derive()
    for cb in list(_listeners):
        cb()


def set_mode(mode: str) -> None:
    """mode: light | dark | auto"""
    global _current
    target = mode
    if mode == "auto":
        target = system_mode()
    if target not in ("light", "dark") or target == _current:
        return
    _current = target
    _derive()
    for cb in list(_listeners):
        cb()


def apply(mode: str = "auto", accent: str = "auto") -> tuple[str, str]:
    """一次性应用模式与强调色（启动时/设置页用）。"""
    global _current, _accent
    real_mode, real_accent = resolve(mode, accent)
    changed = (real_mode != _current) or (real_accent != _accent)
    _current, _accent = real_mode, real_accent
    _derive()
    if changed:
        for cb in list(_listeners):
            cb()
    return _current, _accent


def toggle() -> str:
    set_mode("light" if is_dark() else "dark")
    return _current


def on_change(cb) -> None:
    _listeners.append(cb)


def _derive() -> None:
    """根据当前模式 + 强调色算出 accent 系列颜色。"""
    name = _accent if _accent in ACCENTS else "blue"
    light, dark, _zh, _en = ACCENTS[name]
    base = dark if _current == "dark" else light
    card = THEMES[_current]["card"]
    if _current == "dark":
        hover = shade(base, -0.12)
        pressed = shade(base, -0.24)
        text_on = "#000000"
    else:
        hover = shade(base, -0.10)
        pressed = shade(base, -0.22)
        text_on = "#FFFFFF"
    _DERIVED.clear()
    _DERIVED.update({
        "accent": base,
        "accent_hover": hover,
        "accent_pressed": pressed,
        "accent_text": text_on,
        # 选中行的淡色底：强调色混入卡片色
        "accent_soft": blend(base, card, 0.16 if _current == "light" else 0.22),
        # 标签/徽章
        "chip": blend(base, card, 0.10 if _current == "light" else 0.18),
        "chip_text": base if _current == "light" else shade(base, 0.25),
    })


def c(key: str) -> str:
    if key in _DERIVED:
        return _DERIVED[key]
    return THEMES[_current].get(key, "#ff00ff")


# ---------------------------------------------------------------- 颜色迁移
_COLOR_INDEX: dict[str, str] = {}
_MIGRATE_OPTIONS = (
    "bg", "fg", "insertbackground", "highlightbackground", "highlightcolor",
    "troughcolor", "activebackground", "selectbackground", "disabledforeground",
    "readonlybackground", "selectforeground",
)


def _reset_index() -> None:
    """强调色变了以后，索引要重建（否则旧颜色认不出来）。"""
    _COLOR_INDEX.clear()


def _index() -> dict[str, str]:
    """颜色 -> 语义角色，**只收录语义唯一**的颜色。

    关键：浅色的 card(#FFFFFF) 与深色的 text(#FFFFFF) 是同一个色值，
    若直接建立映射，跨主题迁移会把标题文字改成"卡片色"（深色下就是隐形字）。
    所以这里先统计每个色值对应几个角色，多于一个就跳过（不做迁移，保持原样）。
    """
    if _COLOR_INDEX:
        return _COLOR_INDEX
    roles: dict[str, set] = {}
    for palette in THEMES.values():
        for role, color in palette.items():
            roles.setdefault(str(color).lower(), set()).add(role)
    for role, color in _DERIVED.items():
        roles.setdefault(str(color).lower(), set()).add(role)
    for color, names in roles.items():
        if len(names) == 1:            # 语义唯一，才可安全迁移
            _COLOR_INDEX[color] = next(iter(names))
    return _COLOR_INDEX


def migrate_colors(widget, depth: int = 0) -> int:
    """递归把控件里仍是「另一套主题」的颜色换成当前主题的同名颜色。"""
    if depth > 14:
        return 0
    index = _index()
    fixed = 0
    try:
        children = widget.winfo_children()
    except Exception:
        return 0
    for child in children:
        for option in _MIGRATE_OPTIONS:
            try:
                value = child.cget(option)
            except Exception:
                continue
            if not value:
                continue
            role = index.get(str(value).lower())
            if role is None:
                continue
            target = c(role)
            if str(target).lower() == str(value).lower():
                continue
            try:
                child.configure(**{option: target})
                fixed += 1
            except Exception:
                pass
        fixed += migrate_colors(child, depth + 1)
    return fixed


# ---------------------------------------------------------------- Windows 11 细节
def set_win11_frame(root) -> None:
    """给窗口开启 Win11 的圆角与深色标题栏（DWM 沉浸式）。"""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        dwm = ctypes.windll.dwmapi
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        pref = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dark = ctypes.c_int(1 if _current == "dark" else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        # 标题栏颜色跟随主题（DWMWA_CAPTION_COLOR = 35）
        base = THEMES[_current]["root"].lstrip("#")
        caption = ctypes.c_int(int(f"0x00{base[4:6]}{base[2:4]}{base[0:2]}", 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass


# ---------------------------------------------------------------- 字体
# 字体优先级：标准「微软雅黑」优先（用户指定），其余为兜底
FONT_CANDIDATES = (
    "微软雅黑",              # 标准微软雅黑（同 Microsoft YaHei，系统里的中文名）
    "Microsoft YaHei",
    "Microsoft YaHei UI",    # UI 变体，字距更紧，放在标准体之后
    "微软雅黑 Light",
    "Segoe UI Variable Text",
    "Segoe UI",
)


def font_family(root=None) -> str:
    """返回可用的界面字体家族（优先微软雅黑）。"""
    try:
        from tkinter import font as tkfont

        families = set(tkfont.families(root))
        for name in FONT_CANDIDATES:
            if name in families:
                return name
    except Exception:
        pass
    return "微软雅黑"


_FAMILY = "Segoe UI"


def init_fonts(root) -> None:
    """选定界面字体，并把 Tk 的命名字体一起改掉。

    只改自定义控件不够：tk.Menu、ttk 控件、messagebox、默认 Entry 等
    用的是 TkDefaultFont / TkTextFont 这些命名对象，必须同步，否则会混字体。
    """
    global _FAMILY
    _FAMILY = font_family(root)
    try:
        from tkinter import font as tkfont

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont",
                     "TkTooltipFont", "TkFixedFont"):
            try:
                named = tkfont.nametofont(name)
                if name == "TkFixedFont":
                    named.configure(family="Consolas")   # 等宽处保留等宽
                else:
                    named.configure(family=_FAMILY)
            except Exception:
                pass
    except Exception:
        pass


def F(size: int = 10, weight: str = "normal") -> tuple:
    return (_FAMILY, size, weight)


def MONO(size: int = 9) -> tuple:
    return ("Consolas", size)


F_H1 = lambda: F(17, "bold")
F_H2 = lambda: F(12, "bold")
F_BODY = lambda: F(10)
F_SMALL = lambda: F(9)
F_TINY = lambda: F(8)


_derive()
