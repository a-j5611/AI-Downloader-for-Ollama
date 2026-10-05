"""浅色 / 深色主题调色板与字体。模块级当前主题 + c() 取色。"""
from __future__ import annotations

LIGHT = {
    "root":        "#e9edf4",   # 窗口底色（无背景图时）
    "card":        "#ffffff",   # 卡片
    "card_alt":    "#f4f6fa",   # 次级块 / 输入框
    "card_hover":  "#eef2f8",
    "sidebar":     "#ffffff",
    "topbar":      "#ffffff",
    "text":        "#182031",
    "text_muted":  "#5f6b80",
    "text_faint":  "#8b95a8",
    "border":      "#dde3ec",
    "accent":      "#2f6feb",
    "accent_hover":"#245ccd",
    "accent_text": "#ffffff",
    "chip":        "#e8eefc",
    "chip_text":   "#24558f",
    "bubble_user": "#e4edff",
    "bubble_ai":   "#f3f5f9",
    "bubble_think":"#fff5dd",
    "think_text":  "#7a5c14",
    "scroll":      "#c8d0dd",
    "trough":      "#e4e8f0",
    "ok":          "#12a150",
    "err":         "#dc3b45",
    "warn":        "#d98a0b",
    "shadow":      "#b9c2d2",
}

DARK = {
    "root":        "#0f131a",
    "card":        "#1a1f29",
    "card_alt":    "#232a36",
    "card_hover":  "#2a323f",
    "sidebar":     "#1a1f29",
    "topbar":      "#1a1f29",
    "text":        "#e9eef7",
    "text_muted":  "#9aa5ba",
    "text_faint":  "#6f7a8d",
    "border":      "#2d3644",
    "accent":      "#4c8dff",
    "accent_hover":"#6699ff",
    "accent_text": "#0a0e14",
    "chip":        "#25324a",
    "chip_text":   "#a8c6ff",
    "bubble_user": "#26364f",
    "bubble_ai":   "#222a36",
    "bubble_think":"#3a3320",
    "think_text":  "#e0c37a",
    "scroll":      "#3a4453",
    "trough":      "#2a323f",
    "ok":          "#2ecc71",
    "err":         "#ff6b6b",
    "warn":        "#f0b429",
    "shadow":      "#05070a",
}

THEMES = {"light": LIGHT, "dark": DARK}
MODES = ("light", "dark")

_current = "dark"
_listeners: list = []


def current() -> str:
    return _current


def is_dark() -> bool:
    return _current == "dark"


def set_mode(mode: str) -> None:
    global _current
    if mode in MODES and mode != _current:
        _current = mode
        for cb in list(_listeners):
            cb()


def toggle() -> str:
    set_mode("light" if is_dark() else "dark")
    return _current


def on_change(cb) -> None:
    _listeners.append(cb)


def c(key: str) -> str:
    return THEMES[_current].get(key, "#ff00ff")


# ---------------------------------------------------------------- 颜色迁移
_COLOR_INDEX: dict[str, str] = {}
_MIGRATE_OPTIONS = (
    "bg", "fg", "insertbackground", "highlightbackground", "highlightcolor",
    "troughcolor", "activebackground", "selectbackground", "disabledforeground",
    "readonlybackground", "selectforeground",
)


def _index() -> dict[str, str]:
    """颜色值 -> 语义角色。两套主题里的同一颜色映射到同一角色，便于跨主题迁移。"""
    if _COLOR_INDEX:
        return _COLOR_INDEX
    for palette in THEMES.values():
        for role, color in palette.items():
            _COLOR_INDEX.setdefault(str(color).lower(), role)
    # 白色在多处出现，统一当作卡片色处理（按浅色主题的用法）
    _COLOR_INDEX.setdefault("#ffffff", "card")
    return _COLOR_INDEX


def migrate_colors(widget, depth: int = 0) -> int:
    """递归把控件里仍是「另一套主题」的颜色换成当前主题的同名颜色。

    有些容器层级较深，逐个视图手写 apply_theme 容易漏，这里做统一兜底。
    返回被修改的属性个数。
    """
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
                current = child.cget(option)
            except Exception:
                continue
            if not current:
                continue
            role = index.get(str(current).lower())
            if role is None:
                continue
            target = c(role)
            if str(target).lower() == str(current).lower():
                continue
            try:
                child.configure(**{option: target})
                fixed += 1
            except Exception:
                pass
        fixed += migrate_colors(child, depth + 1)
    return fixed


# ---------------------------------------------------------------- 字体
def font_family(root=None) -> str:
    """优先微软雅黑（中文显示好），回退 Segoe UI。"""
    try:
        from tkinter import font as tkfont

        families = set(tkfont.families(root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
            if name in families:
                return name
    except Exception:
        pass
    return "Segoe UI"


_FAMILY = "Segoe UI"


def init_fonts(root) -> None:
    global _FAMILY
    _FAMILY = font_family(root)


def F(size: int = 10, weight: str = "normal") -> tuple:
    return (_FAMILY, size, weight)


def MONO(size: int = 9) -> tuple:
    return ("Consolas", size)


F_H1 = lambda: F(17, "bold")
F_H2 = lambda: F(12, "bold")
F_BODY = lambda: F(10)
F_SMALL = lambda: F(9)
F_TINY = lambda: F(8)
