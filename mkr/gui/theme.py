"""胶囊 GUI 的尺寸、配色与字体。所有尺寸都是逻辑像素（Qt DIP）。"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase

# ---------- 几何 ----------
WINDOW_W = 372.0
WINDOW_H = 68.0
MARGIN = 10.0

CAPSULE_W = 352.0
CAPSULE_H = 48.0
CAPSULE_RADIUS = 24.0
CAPSULE_RECT = QRectF(MARGIN, MARGIN, CAPSULE_W, CAPSULE_H)
CAPSULE_CENTER_X = CAPSULE_RECT.center().x()
CAPSULE_CENTER_Y = CAPSULE_RECT.center().y()

BUTTON_DIAMETER = 26.0
BUTTON_RADIUS = BUTTON_DIAMETER / 2.0
BUTTON_TOP = MARGIN + 3.5                 # 13.5
BUTTON_CENTER_Y = BUTTON_TOP + BUTTON_RADIUS  # 26.5
BUTTON_HIT_RADIUS = 15.0
BUTTON_CENTER_X_CENTER = CAPSULE_CENTER_X      # 186
BUTTON_CENTER_X_LEFT = MARGIN + 38.0           # 48: 留出圆角，提示文字完全在胶囊内

HINT_TOP = BUTTON_TOP + BUTTON_DIAMETER + 1.0  # 40.5
HINT_HEIGHT = 15.5
HINT_WIDTH = 76.0

CENTER_TEXT_LEFT = CAPSULE_CENTER_X - 110.0
CENTER_TEXT_WIDTH = 220.0
TIME_RECT = QRectF(CENTER_TEXT_LEFT, MARGIN + 1.5, CENTER_TEXT_WIDTH, 25.5)
ACTION_RECT = QRectF(CENTER_TEXT_LEFT, MARGIN + 26.5, CENTER_TEXT_WIDTH, 18.0)

RIGHT_LEFT = MARGIN + 62.0                 # 72
RIGHT_RIGHT = CAPSULE_RECT.right() - 12.0  # 350
RIGHT_WIDTH = RIGHT_RIGHT - RIGHT_LEFT
RIGHT_CENTER_X = (RIGHT_LEFT + RIGHT_RIGHT) / 2.0
RIGHT_LINE_H = 18.0
RIGHT_RECT = QRectF(RIGHT_LEFT, CAPSULE_CENTER_Y - RIGHT_LINE_H / 2.0,
                    RIGHT_WIDTH, RIGHT_LINE_H)

# ---------- 透明度 ----------
DEFAULT_BG_OPACITY = 0.72
MIN_BG_OPACITY = 0.10
MAX_BG_OPACITY = 0.90
BG_OPACITY_STEP = 0.04

# ---------- 颜色 ----------
TEXT = QColor(255, 255, 255)
TEXT_TIME = QColor(255, 255, 255, 242)
TEXT_HINT = QColor(255, 255, 255, 165)
TEXT_ACTION = QColor(255, 255, 255, 165)
TEXT_TICKER = QColor(255, 255, 255, 220)
ACCENT = QColor(242, 166, 90)
TEXT_TAKEOVER = QColor(244, 174, 104)
TEXT_TAKEOVER_ACTION = QColor(255, 255, 255, 205)
GLASS_TOP = QColor(45, 47, 54)
GLASS_BOTTOM = QColor(17, 18, 22)
BUTTON_TOP_COLOR = QColor(60, 63, 72)
BUTTON_BOTTOM_COLOR = QColor(35, 37, 42)
BUTTON_BORDER = QColor(255, 255, 255)


def with_alpha(color: QColor, alpha: float | int) -> QColor:
    """返回带指定透明度的颜色副本；alpha 为 0..1 或 0..255。"""
    c = QColor(color)
    if isinstance(alpha, float) and 0.0 <= alpha <= 1.0:
        c.setAlpha(max(0, min(255, int(round(255 * alpha)))))
    else:
        c.setAlpha(max(0, min(255, int(alpha))))
    return c


# ---------- 字体 ----------
_FONT_CACHE: dict[str, QFont] = {}
_FAMILIES: set[str] | None = None

_FONT_SPECS = {
    "time": {
        "families": ("Segoe UI Variable Display", "Segoe UI", "Inter",
                     "DejaVu Sans"),
        "size": 15.0, "weight": QFont.Weight.DemiBold, "tnum": True,
    },
    "hint": {
        "families": ("Segoe UI Variable Text", "Segoe UI", "Inter",
                     "DejaVu Sans"),
        "size": 8.5, "weight": QFont.Weight.Medium, "spacing": 0.15,
    },
    "action": {
        "families": ("Microsoft YaHei UI", "Microsoft YaHei",
                     "Noto Sans CJK SC", "Segoe UI"),
        "size": 9.0, "weight": QFont.Weight.Normal,
    },
    "ticker": {
        "families": ("Microsoft YaHei UI", "Microsoft YaHei",
                     "Noto Sans CJK SC", "Segoe UI"),
        "size": 9.0, "weight": QFont.Weight.Medium,
    },
    "takeover": {
        "families": ("Microsoft YaHei UI", "Microsoft YaHei",
                     "Noto Sans CJK SC", "Segoe UI"),
        "size": 9.0, "weight": QFont.Weight.Medium,
    },
    "takeover_action": {
        "families": ("Microsoft YaHei UI", "Microsoft YaHei",
                     "Noto Sans CJK SC", "Segoe UI"),
        "size": 9.0, "weight": QFont.Weight.Normal,
    },
}


def _families() -> set[str]:
    global _FAMILIES
    if _FAMILIES is None:
        try:
            _FAMILIES = set(QFontDatabase.families())
        except Exception:
            _FAMILIES = set()
    return _FAMILIES


def pick_family(candidates: tuple[str, ...]) -> str:
    fams = _families()
    for name in candidates:
        if not fams or name in fams:
            return name
    return candidates[-1]


def font(role: str) -> QFont:
    """按角色取缓存字体；必须在 QApplication 创建后调用。"""
    cached = _FONT_CACHE.get(role)
    if cached is not None:
        return cached
    spec = _FONT_SPECS[role]
    f = QFont(pick_family(spec["families"]))
    f.setPointSizeF(float(spec["size"]))
    f.setWeight(spec["weight"])
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if spec.get("spacing"):
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spec["spacing"])
    if spec.get("tnum"):
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except Exception:
            pass
    _FONT_CACHE[role] = f
    return f
