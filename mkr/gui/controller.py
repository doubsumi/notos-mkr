"""App 事件与胶囊窗口之间的状态机。"""
from __future__ import annotations

import math
import threading
import time

from PySide6.QtCore import QObject, QPoint, QSettings, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ..app import App
from . import theme
from .actions import describe_event, format_duration
from .window import CapsuleWindow, HitGeometry, UiState


class AppBridge(QObject):
    """把 App 的线程回调安全地转成 Qt 信号。"""
    event = Signal(str, object)

    def __init__(self, app: App, parent=None):
        super().__init__(parent)
        app.on(self._handle)

    def _handle(self, name: str, **payload):
        self.event.emit(name, payload)


class GuiController(QObject):
    def __init__(self, app: App, window: CapsuleWindow, parent=None):
        super().__init__(parent)
        self.app = app
        self.window = window
        self.settings = QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            "mkr",
            "mkr",
        )

        self._state = UiState.IDLE
        self._record_started_at = 0.0
        self._last_ticker_text = ""
        self._last_click_at = 0.0
        self._quitting = False
        # 二阶段占位：每段录制的循环次数，0 表示一直循环。
        # 与快捷键路径共用 App.playback_loops，避免按钮/快捷键行为不一致。
        self._loop_count = int(getattr(app, "playback_loops", 0) or 0)
        self._geom: HitGeometry | None = None
        self._geom_lock = threading.Lock()

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

        self._bridge = AppBridge(app, self)
        self._bridge.event.connect(self._on_app_event)

        app.recording_filter = self._ignore_recording_point
        app.set_takeover_filter(self._takeover_filter)

        window.primary_clicked.connect(self._on_primary_clicked)
        window.geometry_changed.connect(self._refresh_geometry)
        window.position_changed.connect(self._save_position)
        window.opacity_changed.connect(self._on_opacity_changed)

        self._restore_opacity()
        self._restore_position()
        self._refresh_geometry()

    # ------------------------------------------------------------ 设置
    def _restore_opacity(self) -> None:
        try:
            value = float(self.settings.value("ui/opacity",
                                              theme.DEFAULT_BG_OPACITY))
        except (TypeError, ValueError):
            value = theme.DEFAULT_BG_OPACITY
        value = max(theme.MIN_BG_OPACITY, min(theme.MAX_BG_OPACITY, value))
        self.window.set_bg_opacity(value)

    def _on_opacity_changed(self, value: float) -> None:
        self.settings.setValue("ui/opacity", round(float(value), 3))
        self.settings.sync()

    def _restore_position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        default_x = avail.left() + (avail.width() - int(theme.WINDOW_W)) // 2
        default_y = avail.bottom() - int(theme.WINDOW_H) - 6

        x, y = default_x, default_y
        raw_x = self.settings.value("ui/x", None)
        raw_y = self.settings.value("ui/y", None)
        if raw_x is not None and raw_y is not None:
            try:
                sx, sy = int(float(raw_x)), int(float(raw_y))
                center = QPoint(sx + int(theme.WINDOW_W) // 2,
                                sy + int(theme.WINDOW_H) // 2)
                if QGuiApplication.screenAt(center) is not None:
                    x, y = sx, sy
            except (TypeError, ValueError):
                pass
        self.window.move(QPoint(x, y))

    def _save_position(self) -> None:
        pos = self.window.pos()
        self.settings.setValue("ui/x", int(pos.x()))
        self.settings.setValue("ui/y", int(pos.y()))
        self.settings.sync()

    # ------------------------------------------------------------ App 事件
    def _on_app_event(self, name: str, payload: dict) -> None:
        try:
            if name == "record_started":
                self._on_record_started()
            elif name == "record_stopped":
                self._on_record_stopped()
            elif name == "play_started":
                self._on_play_started()
            elif name == "play_finished":
                self._on_play_finished()
            elif name == "play_taken_over":
                self._on_play_taken_over()
            elif name == "input_event":
                self._on_input_event(payload.get("event"))
            elif name == "replay_event":
                self._on_replay_event(payload.get("event"))
            elif name == "quit":
                self._on_quit()
            elif name == "status":
                text = payload.get("text", "")
                if text:
                    print(f"[mkr] {text}")
        except Exception as e:
            print(f"[mkr] gui event error ({name}): {e}")

    def _on_record_started(self) -> None:
        self._state = UiState.RECORDING
        self._record_started_at = time.perf_counter()
        self.window.set_time("00:00")
        self.window.set_action("等待操作…")
        self.window.set_state(UiState.RECORDING)
        self._timer.start()
        self._refresh_geometry()

    def _on_record_stopped(self) -> None:
        self._timer.stop()
        if self._state == UiState.RECORDING:
            self._state = UiState.READY
            self.window.set_state(UiState.READY)
            self._refresh_geometry()

    def _on_play_started(self) -> None:
        self._state = UiState.PLAYING
        self._last_ticker_text = ""
        self.window.reset_ticker()
        self.window.set_state(UiState.PLAYING)
        self._refresh_geometry()

    def _on_play_finished(self) -> None:
        if self._state == UiState.PLAYING:
            self._state = UiState.READY
            self.window.set_state(UiState.READY)
            self._refresh_geometry()

    def _on_play_taken_over(self) -> None:
        if self._state == UiState.PLAYING:
            self._state = UiState.TAKEN_OVER
            self.window.set_state(UiState.TAKEN_OVER)
            self._refresh_geometry()

    def _on_input_event(self, ev) -> None:
        if self._state != UiState.RECORDING or not isinstance(ev, dict):
            return
        text = describe_event(ev)
        if text:
            self.window.set_action(text)

    def _on_replay_event(self, ev) -> None:
        if self._state != UiState.PLAYING or not isinstance(ev, dict):
            return
        text = describe_event(ev)
        if text and text != self._last_ticker_text:
            self._last_ticker_text = text
            self.window.set_ticker(text)

    def _tick(self) -> None:
        if self._state != UiState.RECORDING:
            self._timer.stop()
            return
        self.window.set_time(format_duration(time.perf_counter()
                                             - self._record_started_at))

    # ------------------------------------------------------------ 交互
    def _on_primary_clicked(self) -> None:
        try:
            now = time.perf_counter()
            if now - self._last_click_at < 0.30:
                return
            self._last_click_at = now
            if self._state == UiState.IDLE:
                self.app.start_record()
            elif self._state == UiState.RECORDING:
                self.app.stop_record()
            elif self._state == UiState.READY:
                self.app.replay(loops=self._loop_count)
            elif self._state == UiState.PLAYING:
                self.app.stop_replay()
            elif self._state == UiState.TAKEN_OVER:
                self.app.replay(loops=self._loop_count)
        except Exception as e:
            print(f"[mkr] click error: {e}")

    # ------------------------------------------------------------ 命中过滤
    def _snapshot_geometry(self) -> HitGeometry | None:
        with self._geom_lock:
            return self._geom

    def _ignore_recording_point(self, x: int, y: int) -> bool:
        geom = self._snapshot_geometry()
        if geom is None:
            return False
        left, top, right, bottom = geom.capsule
        return left <= x <= right and top <= y <= bottom

    def _takeover_filter(self, kind: str, x: int, y: int, key) -> bool:
        geom = self._snapshot_geometry()
        if geom is None:
            return False
        if kind == "move":
            left, top, right, bottom = geom.capsule
            return left <= x <= right and top <= y <= bottom
        if kind in ("click", "scroll"):
            bx, by = geom.button_center
            return math.hypot(x - bx, y - by) <= geom.button_radius
        return False

    def _refresh_geometry(self) -> None:
        try:
            geom = self.window.takeover_geometry()
            with self._geom_lock:
                self._geom = geom
        except Exception:
            pass

    # ------------------------------------------------------------ 退出
    def _on_quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._timer.stop()
        self._save_position()
        self.window.hide()
        QApplication.quit()

    def cleanup(self) -> None:
        self._timer.stop()
        self._save_position()
        try:
            self.app.shutdown()
        except Exception:
            pass
