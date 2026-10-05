"""背景管理：内置渐变 + 本地图片（铺满 / 高斯模糊 / 压暗 / 背景库缩略图）。"""
from __future__ import annotations

import os

try:  # Pillow 可选：缺失时仅能使用纯色底
    from PIL import Image, ImageDraw, ImageFilter, ImageTk

    HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFilter = ImageTk = None
    HAS_PIL = False


# 内置渐变（用于未选择图片时）
PRESETS: dict[str, dict] = {
    "midnight":  {"zh": "午夜",   "en": "Midnight",  "top": "#0f2027", "bottom": "#2c5364"},
    "graphite":  {"zh": "石墨",   "en": "Graphite",  "top": "#232526", "bottom": "#4b5563"},
    "ocean":     {"zh": "深海",   "en": "Ocean",     "top": "#1a2980", "bottom": "#26d0ce"},
    "forest":    {"zh": "森林",   "en": "Forest",    "top": "#134e5e", "bottom": "#71b280"},
    "dusk":      {"zh": "暮色",   "en": "Dusk",      "top": "#2b1055", "bottom": "#7597de"},
    "lavender":  {"zh": "薰衣草", "en": "Lavender",  "top": "#654ea3", "bottom": "#eaafc8"},
    "sky":       {"zh": "晴空",   "en": "Sky",       "top": "#2193b0", "bottom": "#6dd5ed"},
    "blush":     {"zh": "暖霞",   "en": "Blush",     "top": "#ee9ca7", "bottom": "#ffdde1"},
    "sand":      {"zh": "沙丘",   "en": "Sand",      "top": "#e6d5b8", "bottom": "#f7f1e3"},
}
PRESET_IDS = list(PRESETS.keys())


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class BackgroundManager:
    """负责按窗口尺寸渲染背景图，并维护背景库。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._cache_key = None
        self._cache_img = None          # ImageTk.PhotoImage
        self._thumb_cache: dict[str, object] = {}

    # -------------------------------------------------- 状态读写
    @property
    def bg(self) -> dict:
        return self.cfg.setdefault("background", {})

    @property
    def source(self) -> str:
        return self.bg.get("source") or ""

    @property
    def preset(self) -> str:
        pid = self.bg.get("preset") or "midnight"
        return pid if pid in PRESETS else "midnight"

    @property
    def blur(self) -> int:
        try:
            return max(0, min(40, int(self.bg.get("blur", 0))))
        except Exception:
            return 0

    @property
    def dim(self) -> int:
        try:
            return max(0, min(85, int(self.bg.get("dim", 35))))
        except Exception:
            return 35

    def set_source(self, path: str) -> None:
        self.bg["source"] = path or ""
        self.invalidate()
        if path:
            self.add_to_library(path)

    def set_preset(self, preset_id: str) -> None:
        if preset_id in PRESETS:
            self.bg["preset"] = preset_id
            self.bg["source"] = ""      # 选渐变即退出图片背景
            self.invalidate()

    def set_blur(self, value) -> None:
        self.bg["blur"] = max(0, min(40, int(round(float(value)))))
        self.invalidate()

    def set_dim(self, value) -> None:
        self.bg["dim"] = max(0, min(85, int(round(float(value)))))
        self.invalidate()

    def invalidate(self) -> None:
        self._cache_key = None
        self._cache_img = None

    # -------------------------------------------------- 背景库
    @property
    def library(self) -> list:
        lib = self.bg.setdefault("library", [])
        return [p for p in lib if isinstance(p, str)]

    def add_to_library(self, path: str) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        lib = [p for p in self.library if os.path.normcase(p) != os.path.normcase(path)]
        lib.insert(0, path)
        self.bg["library"] = lib[:12]

    def remove_from_library(self, path: str) -> None:
        self.bg["library"] = [p for p in self.library if os.path.normcase(p) != os.path.normcase(path)]
        if os.path.normcase(self.source) == os.path.normcase(path):
            self.bg["source"] = ""
            self.invalidate()

    # -------------------------------------------------- 渲染
    def _gradient(self, size: tuple[int, int]):
        top = _hex_to_rgb(PRESETS[self.preset]["top"])
        bottom = _hex_to_rgb(PRESETS[self.preset]["bottom"])
        w, h = size
        strip = Image.new("RGB", (1, max(h, 2)))
        px = strip.load()
        for y in range(strip.height):
            k = y / max(strip.height - 1, 1)
            px[0, y] = (
                int(top[0] + (bottom[0] - top[0]) * k),
                int(top[1] + (bottom[1] - top[1]) * k),
                int(top[2] + (bottom[2] - top[2]) * k),
            )
        return strip.resize((w, h), Image.BILINEAR)

    def _cover(self, img, size: tuple[int, int]):
        """等比缩放并居中裁剪，铺满目标尺寸。"""
        w, h = size
        if w <= 0 or h <= 0:
            return img
        ratio = max(w / img.width, h / img.height)
        new_size = (max(1, int(img.width * ratio + 0.5)), max(1, int(img.height * ratio + 0.5)))
        img = img.resize(new_size, Image.LANCZOS)
        left = (img.width - w) // 2
        top = (img.height - h) // 2
        return img.crop((left, top, left + w, top + h))

    def render(self, size: tuple[int, int]):
        """返回 PhotoImage；无 Pillow 或渲染失败时返回 None。"""
        if not HAS_PIL:
            return None
        w, h = int(size[0]), int(size[1])
        if w < 2 or h < 2:
            return None
        src = self.source
        key = (src, self.preset, self.blur, self.dim, w, h)
        if key == self._cache_key and self._cache_img is not None:
            return self._cache_img
        try:
            if src and os.path.exists(src):
                img = Image.open(src)
                try:
                    img.draft("RGB", (w * 2, h * 2))   # JPEG 快速降采样
                except Exception:
                    pass
                img = img.convert("RGB")
                img = self._cover(img, (w, h))
            else:
                img = self._gradient((w, h))

            if self.blur > 0:
                img = img.filter(ImageFilter.GaussianBlur(radius=self.blur / 2.0))

            if self.dim > 0:
                overlay = Image.new("RGB", img.size, (0, 0, 0))
                img = Image.blend(img, overlay, self.dim / 100.0)

            photo = ImageTk.PhotoImage(img)
            self._cache_key = key
            self._cache_img = photo          # 保持引用，防止被回收
            return photo
        except Exception:
            return None

    def thumbnail(self, path: str, size: tuple[int, int] = (132, 78)):
        if not HAS_PIL or not os.path.exists(path):
            return None
        key = f"{path}|{size[0]}x{size[1]}|{os.path.getmtime(path):.0f}"
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        try:
            img = Image.open(path)
            try:
                img.draft("RGB", (size[0] * 3, size[1] * 3))
            except Exception:
                pass
            img = self._cover(img.convert("RGB"), size)
            photo = ImageTk.PhotoImage(img)
            self._thumb_cache[key] = photo
            return photo
        except Exception:
            return None

    def preset_swatch(self, preset_id: str, size: tuple[int, int] = (44, 28)):
        if not HAS_PIL or preset_id not in PRESETS:
            return None
        key = f"preset:{preset_id}|{size[0]}x{size[1]}"
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        top = _hex_to_rgb(PRESETS[preset_id]["top"])
        bottom = _hex_to_rgb(PRESETS[preset_id]["bottom"])
        img = Image.new("RGB", size)
        draw = ImageDraw.Draw(img)
        for y in range(size[1]):
            k = y / max(size[1] - 1, 1)
            draw.line(
                [(0, y), (size[0], y)],
                fill=(
                    int(top[0] + (bottom[0] - top[0]) * k),
                    int(top[1] + (bottom[1] - top[1]) * k),
                    int(top[2] + (bottom[2] - top[2]) * k),
                ),
            )
        photo = ImageTk.PhotoImage(img)
        self._thumb_cache[key] = photo
        return photo

    def preset_label(self, preset_id: str, lang: str = "zh") -> str:
        info = PRESETS.get(preset_id, {})
        return info.get(lang, info.get("en", preset_id))
