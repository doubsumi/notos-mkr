"""透明胶囊窗口：绘制、动画、命中测试与鼠标交互。"""
from __future__ import annotations

import ctypes
import math
import sys
from dataclasses import dataclass
from enum import Enum

try:
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover - 非 Windows 平台
    wintypes = None

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFontMetrics, QLinearGradient, QMouseEvent,
    QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QWidget

from . import theme
from .icons import IconKind, draw_icon


class UiState(Enum):
    IDLE = "idle"            # 初始：开始录制按钮居中
    RECORDING = "recording"  # 录制：停止按钮在最左，中间时间/操作
    READY = "ready"          # 结束录制：回放按钮居中
    PLAYING = "playing"      # 回放：停止按钮在最左，右侧操作滚动
    TAKEN_OVER = "taken_over"  # 接管：左侧回放按钮，右侧接管提示


class RightMode(Enum):
    NONE = "none"
    TICKER = "ticker"
    TAKEOVER = "takeover"


HINT_IDLE = "Ctrl×3"
HINT_PLAY = "Ctrl+Alt+P"
HINT_STOP = "Ctrl+Alt+S"

_USER32 = None


def _user32():
    """缓存 user32 句柄，避免 WM_NCHITTEST 高频路径重复 WinDLL 初始化。"""
    global _USER32
    if _USER32 is None:
        _USER32 = ctypes.WinDLL("user32", use_last_error=True)
        _USER32.GetWindowRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT)
        ]
        _USER32.GetWindowRect.restype = wintypes.BOOL
    return _USER32


@dataclass(frozen=True)
class HitGeometry:
    """给接管过滤器使用的物理像素命中信息。"""
    capsule: tuple[int, int, int, int]
    button_center: tuple[int, int]
    button_radius: int


class CapsuleWindow(QWidget):
    primary_clicked = Signal()
    geometry_changed = Signal()
    position_changed = Signal()
    opacity_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setFixedSize(int(theme.WINDOW_W), int(theme.WINDOW_H))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("mkr")

        self._state = UiState.IDLE
        self._bg_opacity = theme.DEFAULT_BG_OPACITY

        # 按钮 / 图标 / 快捷键提示
        self._button_x = theme.BUTTON_CENTER_X_CENTER
        self._icon_from = IconKind.RECORD
        self._icon_to = IconKind.RECORD
        self._icon_mix = 1.0
        self._hint_from = HINT_IDLE
        self._hint_to = HINT_IDLE
        self._hint_mix = 1.0

        # 中央录制信息
        self._center_alpha = 0.0
        self._center_timer: QTimer | None = None
        self._time_text = "00:00"
        self._action_text = "等待操作…"

        # 右侧区域
        self._right_prev_mode = RightMode.NONE
        self._right_prev_alpha = 0.0
        self._right_mode = RightMode.NONE
        self._right_target = 0.0
        self._right_mix = 1.0
        self._ticker_prev = ""
        self._ticker_cur = ""
        self._ticker_mix = 1.0
        self._ticker_pending: str | None = None

        # 交互状态
        self._hover_button = False
        self._hover_takeover = False
        self._hover = 0.0
        self._press = 0.0
        self._accent = 0.0
        self._pressed_button = False
        self._pressed_takeover = False
        self._dragging = False
        self._drag_moved = False
        self._drag_offset = QPoint()
        self._drag_start_global = QPoint()
        self._screen_hooked = False

        self._anims: dict[str, QVariantAnimation] = {}
        self._capsule_path = self._build_capsule_path()

    # ------------------------------------------------------------ 基本属性
    def _build_capsule_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(theme.CAPSULE_RECT, theme.CAPSULE_RADIUS,
                            theme.CAPSULE_RADIUS)
        return path

    def state(self) -> UiState:
        return self._state

    def bg_opacity(self) -> float:
        return self._bg_opacity

    def set_bg_opacity(self, value: float) -> None:
        value = max(theme.MIN_BG_OPACITY, min(theme.MAX_BG_OPACITY, float(value)))
        if abs(value - self._bg_opacity) < 1e-5:
            return
        self._bg_opacity = value
        self.update()

    def button_center(self) -> QPointF:
        return QPointF(self._button_x, theme.BUTTON_CENTER_Y)

    # ------------------------------------------------------------ 动画工具
    def _stop_anim(self, attr: str) -> None:
        anim = self._anims.pop(attr, None)
        if anim is not None:
            anim.stop()
            anim.deleteLater()

    def _animate_float(self, attr: str, target: float, duration: int,
                       easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
                       on_finished=None) -> None:
        current = float(getattr(self, attr))
        self._stop_anim(attr)
        if duration <= 0 or abs(current - target) < 1e-5:
            setattr(self, attr, float(target))
            self.update()
            if on_finished:
                on_finished()
            return

        anim = QVariantAnimation(self)
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.setDuration(int(duration))
        anim.setEasingCurve(QEasingCurve(easing))

        def _on_value(value):
            setattr(self, attr, float(value))
            self.update()

        def _on_finished():
            if self._anims.get(attr) is anim:
                self._anims.pop(attr, None)
            anim.deleteLater()
            if on_finished:
                on_finished()

        anim.valueChanged.connect(_on_value)
        anim.finished.connect(_on_finished)
        self._anims[attr] = anim
        anim.start()

    def _stop_center_timer(self) -> None:
        if self._center_timer is not None:
            self._center_timer.stop()
            self._center_timer.deleteLater()
            self._center_timer = None

    # ------------------------------------------------------------ 状态切换
    def set_state(self, state: UiState, animate: bool = True) -> None:
        if state == self._state:
            return
        if self._dragging:
            self._dragging = False
            if self._drag_moved:
                self.position_changed.emit()
        self._state = state

        target_x = (theme.BUTTON_CENTER_X_LEFT
                    if state in (UiState.RECORDING, UiState.PLAYING, UiState.TAKEN_OVER)
                    else theme.BUTTON_CENTER_X_CENTER)
        self._animate_float("_button_x", target_x, 380 if animate else 0)

        icon = {
            UiState.IDLE: IconKind.RECORD,
            UiState.RECORDING: IconKind.STOP,
            UiState.PLAYING: IconKind.STOP,
            UiState.READY: IconKind.PLAY,
            UiState.TAKEN_OVER: IconKind.PLAY,
        }[state]
        self._set_icon(icon, animate)

        hint = {
            UiState.IDLE: HINT_IDLE,
            UiState.RECORDING: HINT_IDLE,
            UiState.PLAYING: HINT_STOP,
            UiState.READY: HINT_PLAY,
            UiState.TAKEN_OVER: HINT_PLAY,
        }[state]
        self._set_hint(hint, animate)

        self._set_center(state == UiState.RECORDING, animate)

        right = {
            UiState.PLAYING: RightMode.TICKER,
            UiState.TAKEN_OVER: RightMode.TAKEOVER,
        }.get(state, RightMode.NONE)
        self._set_right(right, animate)

        if state == UiState.TAKEN_OVER and animate:
            self._pulse_accent()

        self._refresh_hover()
        self.geometry_changed.emit()

    def _set_icon(self, new_icon: IconKind, animate: bool) -> None:
        if new_icon == self._icon_to and self._icon_mix >= 1.0:
            return
        current = self._icon_to if self._icon_mix >= 0.5 else self._icon_from
        if new_icon == current and self._icon_mix < 1.0:
            self._icon_to = new_icon
            self._animate_float("_icon_mix", 1.0, 180 if animate else 0)
            return
        self._icon_from = current
        self._icon_to = new_icon
        self._icon_mix = 0.0
        self._animate_float("_icon_mix", 1.0, 220 if animate else 0)

    def _set_hint(self, new_hint: str, animate: bool) -> None:
        if new_hint == self._hint_to and self._hint_mix >= 1.0:
            return
        current = self._hint_to if self._hint_mix >= 0.5 else self._hint_from
        if new_hint == current and self._hint_mix < 1.0:
            self._hint_to = new_hint
            self._animate_float("_hint_mix", 1.0, 160 if animate else 0)
            return
        self._hint_from = current
        self._hint_to = new_hint
        self._hint_mix = 0.0
        self._animate_float("_hint_mix", 1.0, 200 if animate else 0)

    def _set_center(self, visible: bool, animate: bool) -> None:
        self._stop_center_timer()
        if visible:
            if self._center_alpha >= 0.999:
                return
            if not animate:
                self._center_alpha = 1.0
                self.update()
                return
            if self._center_alpha > 0.01:
                self._animate_float("_center_alpha", 1.0, 180)
                return
            # 等按钮先让开中央位置，再淡入文字
            self._center_timer = QTimer(self)
            self._center_timer.setSingleShot(True)
            self._center_timer.timeout.connect(
                lambda: self._animate_float("_center_alpha", 1.0, 240)
            )
            self._center_timer.start(100)
        else:
            if self._center_alpha <= 0.001:
                return
            self._animate_float("_center_alpha", 0.0, 150 if animate else 0)

    def _right_visible_now(self) -> float:
        if self._right_mix >= 1.0:
            return self._right_target if self._right_mode != RightMode.NONE else 0.0
        # 粗略取当前可见强度，供下一次切换接管
        return max(self._right_prev_alpha * (1.0 - self._right_mix),
                   self._right_target * self._right_mix)

    def _set_right(self, new_mode: RightMode, animate: bool) -> None:
        if new_mode == self._right_mode and self._right_mix >= 1.0:
            return
        old_visible = self._right_visible_now()
        self._right_prev_mode = self._right_mode
        self._right_prev_alpha = old_visible
        self._right_mode = new_mode
        self._right_target = 1.0 if new_mode != RightMode.NONE else 0.0
        self._right_mix = 0.0
        self._animate_float("_right_mix", 1.0, 280 if animate else 0)
        if new_mode == RightMode.TICKER:
            self._ticker_mix = 1.0
            self._ticker_pending = None

    def _pulse_accent(self) -> None:
        self._stop_anim("_accent")
        self._accent = 0.0
        self._animate_float(
            "_accent", 0.85, 130,
            on_finished=lambda: self._animate_float("_accent", 0.0, 720),
        )

    # ------------------------------------------------------------ 数据更新
    def set_time(self, text: str) -> None:
        if text == self._time_text:
            return
        self._time_text = text
        if self._center_alpha > 0.01:
            self.update()

    def set_action(self, text: str) -> None:
        if not text or text == self._action_text:
            return
        self._action_text = text
        if self._center_alpha > 0.01:
            self.update()

    def reset_ticker(self) -> None:
        self._stop_anim("_ticker_mix")
        self._ticker_prev = ""
        self._ticker_cur = ""
        self._ticker_pending = None
        self._ticker_mix = 1.0
        self.update()

    def set_ticker(self, text: str) -> None:
        if not text or text == self._ticker_cur:
            return
        if self._ticker_mix < 1.0:
            self._ticker_pending = text
            return
        self._ticker_prev = self._ticker_cur
        self._ticker_cur = text
        self._ticker_mix = 0.0
        self._animate_float("_ticker_mix", 1.0, 240, on_finished=self._ticker_done)

    def _ticker_done(self) -> None:
        pending = self._ticker_pending
        self._ticker_pending = None
        if pending and pending != self._ticker_cur:
            self._ticker_prev = self._ticker_cur
            self._ticker_cur = pending
            self._ticker_mix = 0.0
            self._animate_float("_ticker_mix", 1.0, 240,
                                on_finished=self._ticker_done)

    # ------------------------------------------------------------ 命中测试
    def _point_in_capsule(self, point: QPointF) -> bool:
        r = theme.CAPSULE_RECT
        if not r.contains(point):
            return False
        rad = theme.CAPSULE_RADIUS
        if r.left() + rad <= point.x() <= r.right() - rad:
            return True
        if r.top() + rad <= point.y() <= r.bottom() - rad:
            return True
        cx = r.left() + rad if point.x() < r.left() + rad else r.right() - rad
        cy = r.top() + rad if point.y() < r.top() + rad else r.bottom() - rad
        return (point.x() - cx) ** 2 + (point.y() - cy) ** 2 <= rad * rad

    def _point_in_button(self, point: QPointF) -> bool:
        return math.hypot(point.x() - self._button_x,
                          point.y() - theme.BUTTON_CENTER_Y) <= theme.BUTTON_HIT_RADIUS

    def _point_in_right(self, point: QPointF) -> bool:
        return theme.RIGHT_RECT.contains(point)

    def _local_from_physical(self, x: int, y: int) -> QPointF:
        left, top, _right, _bottom = self._physical_window_rect()
        dpr = self.devicePixelRatioF() or 1.0
        return QPointF((x - left) / dpr, (y - top) / dpr)

    def _physical_window_rect(self) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                user32 = _user32()
                rect = wintypes.RECT()
                if user32.GetWindowRect(int(self.winId()), ctypes.byref(rect)):
                    return (rect.left, rect.top, rect.right, rect.bottom)
            except Exception:
                pass
        fg = self.frameGeometry()
        dpr = self.devicePixelRatioF() or 1.0
        return (
            int(round(fg.x() * dpr)),
            int(round(fg.y() * dpr)),
            int(round((fg.x() + fg.width()) * dpr)),
            int(round((fg.y() + fg.height()) * dpr)),
        )

    def takeover_geometry(self) -> HitGeometry:
        dpr = self.devicePixelRatioF() or 1.0
        left, top, _right, _bottom = self._physical_window_rect()
        cap_left = left + int(round(theme.MARGIN * dpr))
        cap_top = top + int(round(theme.MARGIN * dpr))
        cap_right = cap_left + int(round(theme.CAPSULE_W * dpr))
        cap_bottom = cap_top + int(round(theme.CAPSULE_H * dpr))
        # 回放期间按钮的目标位置就在最左端；用目标位置而不是动画中的
        # 瞬时位置，保证接管过滤器在整个回放期间都能正确放行停止按钮。
        button_x = (theme.BUTTON_CENTER_X_LEFT
                    if self._state == UiState.PLAYING else self._button_x)
        bx = left + int(round((theme.MARGIN + button_x) * dpr))
        by = top + int(round((theme.MARGIN + theme.BUTTON_CENTER_Y) * dpr))
        br = int(round(theme.BUTTON_HIT_RADIUS * dpr))
        return HitGeometry(
            capsule=(cap_left, cap_top, cap_right, cap_bottom),
            button_center=(bx, by),
            button_radius=br,
        )

    # ------------------------------------------------------------ 原生命中
    def nativeEvent(self, eventType, message):
        if sys.platform == "win32":
            is_generic = False
            try:
                if hasattr(eventType, "data"):
                    raw_type = bytes(eventType.data()).rstrip(b"\x00")
                else:
                    raw_type = bytes(eventType).rstrip(b"\x00")
                is_generic = raw_type == b"windows_generic_MSG"
            except Exception:
                try:
                    is_generic = "windows_generic_MSG" in str(eventType)
                except Exception:
                    is_generic = False
            if is_generic:
                try:
                    addr = int(message)
                except Exception:
                    try:
                        addr = message.__int__()
                    except Exception:
                        return super().nativeEvent(eventType, message)
                try:
                    msg = wintypes.MSG.from_address(addr)
                except Exception:
                    return super().nativeEvent(eventType, message)
                if msg.message == 0x0084:  # WM_NCHITTEST
                    local = self._local_from_physical(msg.pt.x, msg.pt.y)
                    transparent = not self._point_in_capsule(local)
                    if self._state == UiState.PLAYING and not transparent:
                        # 回放期间胶囊背景点击穿透，只保留停止按钮可点
                        transparent = not self._point_in_button(local)
                    return True, -1 if transparent else 1
        return super().nativeEvent(eventType, message)

    # ------------------------------------------------------------ 窗口事件
    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                user32 = _user32()
                hwnd = int(self.winId())
                GWL_EXSTYLE = -20
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOOLWINDOW = 0x00000080
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
                get_long.restype = ctypes.c_ssize_t
                get_long.argtypes = [wintypes.HWND, ctypes.c_int]
                set_long.restype = ctypes.c_ssize_t
                set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
                ex = get_long(hwnd, GWL_EXSTYLE)
                set_long(hwnd, GWL_EXSTYLE,
                         ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
                # 让扩展样式立即生效，同时保持位置/尺寸/Z 序不变
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT,
                ]
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
            except Exception:
                pass
        if not self._screen_hooked:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(lambda _s: self.geometry_changed.emit())
                self._screen_hooked = True
        self.geometry_changed.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.geometry_changed.emit()

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        if self._point_in_button(point):
            self._pressed_button = True
            self._animate_float("_press", 1.0, 90)
            event.accept()
            return
        if (self._state == UiState.TAKEN_OVER
                and self._point_in_right(point)):
            self._pressed_takeover = True
            event.accept()
            return
        if (self._state in (UiState.IDLE, UiState.READY, UiState.TAKEN_OVER)
                and self._point_in_capsule(point)):
            self._dragging = True
            self._drag_moved = False
            self._drag_start_global = event.globalPosition().toPoint()
            self._drag_offset = self._drag_start_global - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            if event.buttons() & Qt.MouseButton.LeftButton:
                delta = event.globalPosition().toPoint() - self._drag_start_global
                if not self._drag_moved and delta.manhattanLength() > 2:
                    self._drag_moved = True
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                event.accept()
                return
            # 鼠标抓取丢失时兜底结束拖动
            self._dragging = False
            if self._drag_moved:
                self.position_changed.emit()
        self._update_hover(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        handled = False
        if self._pressed_button:
            if self._point_in_button(event.position()):
                self.primary_clicked.emit()
            self._pressed_button = False
            self._animate_float("_press", 0.0, 140)
            handled = True
        if self._pressed_takeover:
            if (self._state == UiState.TAKEN_OVER
                    and self._point_in_right(event.position())):
                self.primary_clicked.emit()
            self._pressed_takeover = False
            handled = True
        if self._dragging:
            self._dragging = False
            if self._drag_moved:
                self.position_changed.emit()
            handled = True
        if handled:
            self._update_hover(event.position())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            steps = delta / 120.0
            new_value = self._bg_opacity + steps * theme.BG_OPACITY_STEP
            new_value = max(theme.MIN_BG_OPACITY,
                            min(theme.MAX_BG_OPACITY, new_value))
            if abs(new_value - self._bg_opacity) > 1e-5:
                self._bg_opacity = new_value
                self.update()
                self.opacity_changed.emit(new_value)
            event.accept()
            return
        super().wheelEvent(event)

    def leaveEvent(self, event):
        if self._hover_button:
            self._hover_button = False
            self._animate_float("_hover", 0.0, 120)
        self._hover_takeover = False
        if not self._dragging:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def _refresh_hover(self) -> None:
        if self.underMouse():
            self._update_hover(QPointF(self.mapFromGlobal(QCursor.pos())))
        else:
            self._hover_button = False
            self._hover_takeover = False

    def _update_hover(self, point: QPointF) -> None:
        hover_button = self._point_in_button(point)
        hover_takeover = (self._state == UiState.TAKEN_OVER
                          and self._point_in_right(point))
        if hover_button != self._hover_button:
            self._hover_button = hover_button
            self._animate_float("_hover", 1.0 if hover_button else 0.0, 120)
        if hover_takeover != self._hover_takeover:
            self._hover_takeover = hover_takeover
            self.update()
        if self._dragging:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif hover_button or hover_takeover:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._state in (UiState.IDLE, UiState.READY, UiState.TAKEN_OVER):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        self._paint_shadow(p)
        self._paint_glass(p)
        self._paint_accent(p)
        self._paint_button(p)
        self._paint_center(p)
        self._paint_right(p)
        p.end()

    def _paint_shadow(self, p: QPainter) -> None:
        opacity = self._bg_opacity
        shifted = QPainterPath(self._capsule_path)
        shifted.translate(0, 1.5)
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, strength in ((16.0, 0.04), (11.0, 0.055),
                                (7.0, 0.075), (3.0, 0.10)):
            alpha = strength * (0.25 + 0.75 * opacity)
            pen = QPen(theme.with_alpha(QColor(0, 0, 0), alpha), width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(shifted)
        p.restore()

    def _paint_glass(self, p: QPainter) -> None:
        opacity = self._bg_opacity
        grad = QLinearGradient(0, theme.CAPSULE_RECT.top(),
                               0, theme.CAPSULE_RECT.bottom())
        grad.setColorAt(0.0, theme.with_alpha(theme.GLASS_TOP, opacity * 0.92))
        grad.setColorAt(1.0, theme.with_alpha(theme.GLASS_BOTTOM, opacity))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(self._capsule_path)

        border_alpha = 0.06 + 0.10 * opacity
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.with_alpha(QColor(255, 255, 255), border_alpha), 1.0))
        p.drawPath(self._capsule_path)

        # 顶部内侧一道很轻的高光，玻璃感
        p.setClipPath(self._capsule_path)
        p.setPen(QPen(theme.with_alpha(QColor(255, 255, 255),
                                       0.035 + 0.035 * opacity), 1.0))
        y = theme.CAPSULE_RECT.top() + 0.75
        p.drawLine(QPointF(theme.CAPSULE_RECT.left() + theme.CAPSULE_RADIUS, y),
                   QPointF(theme.CAPSULE_RECT.right() - theme.CAPSULE_RADIUS, y))
        p.restore()

    def _paint_accent(self, p: QPainter) -> None:
        if self._accent <= 0.01:
            return
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.with_alpha(theme.ACCENT, 0.14 * self._accent), 3.0))
        p.drawPath(self._capsule_path)
        p.setPen(QPen(theme.with_alpha(theme.ACCENT, 0.75 * self._accent), 1.0))
        p.drawPath(self._capsule_path)
        p.restore()

    def _paint_button(self, p: QPainter) -> None:
        center = self.button_center()
        scale = 1.0 + 0.035 * self._hover - 0.07 * self._press
        p.save()
        p.translate(center)
        p.scale(scale, scale)

        r = theme.BUTTON_RADIUS
        lighten = 100 + int(round(28 * self._hover))
        grad = QLinearGradient(0, -r, 0, r)
        grad.setColorAt(0.0, theme.BUTTON_TOP_COLOR.lighter(lighten))
        grad.setColorAt(1.0, theme.BUTTON_BOTTOM_COLOR.lighter(lighten))
        p.setPen(QPen(theme.with_alpha(theme.BUTTON_BORDER,
                                       0.10 + 0.10 * self._hover), 1.0))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(0, 0), r, r)

        icon_color = QColor(255, 255, 255)
        if self._icon_from != self._icon_to:
            if self._icon_mix < 0.999:
                draw_icon(p, self._icon_from, QPointF(0, 0), icon_color,
                          opacity=(1.0 - self._icon_mix) * 0.95,
                          scale=1.0 - 0.10 * self._icon_mix)
            draw_icon(p, self._icon_to, QPointF(0, 0), icon_color,
                      opacity=0.95 * self._icon_mix,
                      scale=0.82 + 0.18 * self._icon_mix)
        else:
            draw_icon(p, self._icon_to, QPointF(0, 0), icon_color, opacity=0.95)
        p.restore()

        hint_rect = QRectF(center.x() - theme.HINT_WIDTH / 2.0,
                           theme.HINT_TOP, theme.HINT_WIDTH, theme.HINT_HEIGHT)
        p.save()
        p.setFont(theme.font("hint"))
        p.setPen(theme.TEXT_HINT)
        if self._hint_from != self._hint_to:
            if self._hint_mix < 0.999:
                p.setOpacity(1.0 - self._hint_mix)
                p.drawText(hint_rect,
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                           self._hint_from)
            p.setOpacity(self._hint_mix)
            p.drawText(hint_rect,
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       self._hint_to)
        else:
            p.setOpacity(1.0)
            p.drawText(hint_rect,
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       self._hint_to)
        p.restore()

    def _paint_center(self, p: QPainter) -> None:
        if self._center_alpha <= 0.005:
            return
        p.save()
        p.setOpacity(self._center_alpha)

        p.setFont(theme.font("time"))
        p.setPen(theme.TEXT_TIME)
        p.drawText(theme.TIME_RECT, Qt.AlignmentFlag.AlignCenter, self._time_text)

        p.setFont(theme.font("action"))
        p.setPen(theme.TEXT_ACTION)
        fm = QFontMetrics(theme.font("action"))
        action = fm.elidedText(self._action_text, Qt.TextElideMode.ElideRight,
                               int(theme.CENTER_TEXT_WIDTH) - 6)
        p.drawText(theme.ACTION_RECT, Qt.AlignmentFlag.AlignCenter, action)
        p.restore()

    def _paint_right(self, p: QPainter) -> None:
        old_alpha = self._right_prev_alpha * (1.0 - self._right_mix)
        new_alpha = self._right_target * self._right_mix
        if old_alpha > 0.005:
            self._paint_right_mode(p, self._right_prev_mode, old_alpha)
        if new_alpha > 0.005:
            self._paint_right_mode(p, self._right_mode, new_alpha)

    def _paint_right_mode(self, p: QPainter, mode: RightMode, alpha: float) -> None:
        if mode == RightMode.TICKER:
            self._paint_ticker(p, alpha)
        elif mode == RightMode.TAKEOVER:
            self._paint_takeover(p, alpha)

    def _paint_ticker(self, p: QPainter, alpha: float) -> None:
        if not self._ticker_prev and not self._ticker_cur:
            return
        p.save()
        p.setClipRect(theme.RIGHT_RECT)
        p.setFont(theme.font("ticker"))
        p.setPen(theme.TEXT_TICKER)
        fm = QFontMetrics(theme.font("ticker"))
        line_h = max(1.0, float(fm.height()))

        def draw(text: str, offset: float, opacity: float) -> None:
            if not text or opacity <= 0.005:
                return
            p.setOpacity(opacity * alpha)
            shown = fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                  int(theme.RIGHT_WIDTH) - 2)
            rect = QRectF(theme.RIGHT_RECT.left(),
                          theme.RIGHT_RECT.top() + offset,
                          theme.RIGHT_WIDTH, theme.RIGHT_RECT.height())
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, shown)

        if self._ticker_mix < 0.999:
            draw(self._ticker_prev, -self._ticker_mix * line_h, 1.0 - self._ticker_mix)
        draw(self._ticker_cur, (1.0 - self._ticker_mix) * line_h, self._ticker_mix)
        p.restore()

    def _paint_takeover(self, p: QPainter, alpha: float) -> None:
        p.save()
        p.setOpacity(alpha)
        f1 = theme.font("takeover")
        f2 = theme.font("takeover_action")
        m1 = QFontMetrics(f1)
        m2 = QFontMetrics(f2)
        first = "当前操作已被接管"
        second = " · 点击重新回放"
        w1 = m1.horizontalAdvance(first)
        w2 = m2.horizontalAdvance(second)
        total = w1 + w2
        x = theme.RIGHT_CENTER_X - total / 2.0
        y = theme.RIGHT_RECT.top()
        h = theme.RIGHT_RECT.height()

        p.setFont(f1)
        p.setPen(theme.TEXT_TAKEOVER)
        p.drawText(QRectF(x, y, w1 + 2, h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, first)

        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 236) if self._hover_takeover
                 else theme.TEXT_TAKEOVER_ACTION)
        p.drawText(QRectF(x + w1, y, w2 + 2, h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, second)
        if self._hover_takeover:
            fm = QFontMetrics(f2)
            underline_y = y + h / 2.0 + fm.descent() + 1.0
            p.setPen(QPen(theme.with_alpha(QColor(255, 255, 255), 0.55), 1.0))
            p.drawLine(QPointF(x + w1 + 2, underline_y),
                       QPointF(x + w1 + w2 - 2, underline_y))
        p.restore()
