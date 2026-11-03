"""应用层：连接录制器、回放器、热键、UI 事件总线。"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from typing import Callable

try:
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover - 非 Windows
    wintypes = None

from pynput import keyboard, mouse

from .recorder import Recorder, Replayer, release_modifiers


log = logging.getLogger("notos-mkr.app")
_DEBUG_INPUT = os.environ.get("NOTOS_MKR_DEBUG_INPUT") == "1"


if wintypes is not None:
    class _TOKEN_ELEVATION(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]
else:  # pragma: no cover
    _TOKEN_ELEVATION = None


# ---------- 热键常量（集中管理，便于后期扩展）----------
HOTKEY_RECORD_TRIPLE_CTRL = "triple-ctrl"  # 三连 Ctrl（500ms 内）
HOTKEY_PLAYBACK_START = "<ctrl>+<alt>+p"
HOTKEY_PLAYBACK_STOP  = "<ctrl>+<alt>+s"
HOTKEY_QUIT           = "<ctrl>+<alt>+q"


def _current_process_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _foreground_process_info() -> tuple[int | None, bool | None]:
    """返回前台窗口 PID 与是否 Elevated；无法查询时返回 None。"""
    if sys.platform != "win32":
        return None, None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        pid = wintypes.DWORD()
        hwnd = user32.GetForegroundWindow()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None, None
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
        if not h:
            return int(pid.value), None
        try:
            tok = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
                return int(pid.value), None
            try:
                elev = _TOKEN_ELEVATION()
                size = wintypes.DWORD()
                ok = advapi32.GetTokenInformation(
                    tok, 20, ctypes.byref(elev), ctypes.sizeof(elev),
                    ctypes.byref(size),
                )
                return int(pid.value), (bool(elev.TokenIsElevated) if ok else None)
            finally:
                kernel32.CloseHandle(tok)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None, None


class _TripleCtrlDetector:
    """自定义三连 Ctrl 检测器（pynput GlobalHotKeys 不支持 multi-press）。

    500ms 内连续按下 3 次任意 Ctrl（L/R）触发回调；按住不放的自动重复
    不会计数，中间插入其它按键会清空计数。
    """

    CTRL_KEY_IDS = {"special:ctrl", "special:ctrl_l", "special:ctrl_r"}
    TRIPLE_WINDOW = 0.5  # seconds
    TRIPLE_COUNT = 3

    def __init__(self, callback: Callable[[], None]):
        self._cb = callback
        self._press_times: list[float] = []
        self._down: set[str] = set()
        self._lock = threading.Lock()

    def _key_id(self, key) -> str | None:
        if isinstance(key, keyboard.KeyCode):
            return f"vk:{getattr(key, 'vk', None)}"
        return f"special:{getattr(key, 'name', None)}"

    def on_press(self, key, injected: bool = False) -> None:
        if injected:
            return
        kid = self._key_id(key)
        triggered = False
        with self._lock:
            if kid not in self.CTRL_KEY_IDS:
                # 中间插入了其它键，重置这次连按
                self._press_times.clear()
                return
            if kid in self._down:
                # 按住 Ctrl 的自动重复，不计数
                return
            self._down.add(kid)
            now = time.perf_counter()
            cutoff = now - self.TRIPLE_WINDOW
            self._press_times = [t for t in self._press_times if t >= cutoff]
            self._press_times.append(now)
            if len(self._press_times) >= self.TRIPLE_COUNT:
                self._press_times.clear()
                triggered = True
        if triggered:
            try:
                self._cb()
            except Exception as e:
                print(f"[notos-mkr] triple-ctrl callback error: {e}")

    def on_release(self, key, injected: bool = False) -> None:
        if injected:
            return
        kid = self._key_id(key)
        if kid not in self.CTRL_KEY_IDS:
            return
        with self._lock:
            self._down.discard(kid)


class _PollHotkeyDetector:
    """轮询后端下的 Ctrl+Alt+P/S/Q 边沿检测。"""

    def __init__(self, app):
        self._app = app
        self._ctrl = False
        self._alt = False
        self._fired: set[str] = set()

    def _action_for(self, key):
        vk = getattr(key, "vk", None)
        if vk == 0x50:  # P
            return "play", self._app._hk_play
        if vk == 0x53:  # S
            return "stop", self._app._hk_stop_all
        if vk == 0x51:  # Q
            return "quit", self._app._hk_quit
        return None, None

    def on_press(self, key) -> None:
        name = getattr(key, "name", None)
        if name == "ctrl":
            self._ctrl = True
            self._fired.clear()
            return
        if name == "alt":
            self._alt = True
            self._fired.clear()
            return
        if not (self._ctrl and self._alt):
            return
        tag, action = self._action_for(key)
        if action is None or tag in self._fired:
            return
        self._fired.add(tag)
        action()

    def on_release(self, key) -> None:
        name = getattr(key, "name", None)
        if name == "ctrl":
            self._ctrl = False
            self._fired.clear()
            return
        if name == "alt":
            self._alt = False
            self._fired.clear()
            return
        tag, _action = self._action_for(key)
        if tag:
            self._fired.discard(tag)


class App:
    """应用核心：管理录制器、回放器、热键、事件分发。"""

    def __init__(self):
        self.recorder = Recorder()
        self.replayer = Replayer()
        self._listeners: list[Callable] = []
        self._hk_listener = None
        self._hk_listener_extra: keyboard.Listener | None = None
        self._triple_ctrl = _TripleCtrlDetector(self._hk_toggle_record)
        self._poll_hotkeys = _PollHotkeyDetector(self)
        self._mouse_listener = None
        self._kb_listener = None
        self._lock = threading.Lock()
        self._shutdown = False
        self._recording = False
        self._hotkeys_enabled = False
        self._poll_subscribed = False
        self._poll_token = None
        # 回放循环次数占位：0 = 一直循环，直到停止/接管。
        # 二阶段会由活动录制的 loop_count 覆盖。
        self.playback_loops = 0

        # GUI 可注入的过滤/回调
        self.recording_filter: Callable[[int, int], bool] | None = None
        self.recorder.on_event = self._on_recorder_event

    # ---------- 事件通知 ----------
    def on(self, cb: Callable) -> None:
        self._listeners.append(cb)

    def _emit(self, name: str, **payload) -> None:
        for cb in list(self._listeners):
            try:
                cb(name, **payload)
            except Exception as e:
                print(f"[notos-mkr] listener error: {e}")

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_playing(self) -> bool:
        return self.replayer.is_playing

    # ---------- 录制事件 ----------
    def _on_recorder_event(self, ev: dict) -> None:
        if _DEBUG_INPUT:
            log.info("input_event %s", ev)
        self._emit("input_event", event=ev)

    def _on_replay_event(self, ev: dict) -> None:
        self._emit("replay_event", event=ev)

    def set_takeover_filter(self, fn: Callable[[str, int, int, object], bool] | None) -> None:
        self.replayer.set_takeover_filter(fn)

    # ---------- 热键 ----------
    def _ensure_poll_subscription(self) -> None:
        if sys.platform != "win32" or self._poll_subscribed:
            return
        try:
            from .keyboard_polling import get_keyboard_service
            service = get_keyboard_service()
            self._poll_token = service.subscribe(
                self._on_polled_press,
                self._on_polled_release,
                self._on_polled_repeat,
            )
            self._poll_subscribed = self._poll_token is not None
            if self._poll_subscribed:
                log.info("keyboard polling subscribed (token=%s)",
                         self._poll_token)
            else:
                log.warning("keyboard polling service unavailable")
        except Exception as e:
            log.exception("keyboard polling subscribe failed")
            print(f"[notos-mkr] keyboard polling unavailable: {e}")

    def _on_polled_press(self, key) -> None:
        if self._shutdown:
            return
        try:
            if self._recording:
                self.recorder.on_press(key)
            if (self._hotkeys_enabled
                    and not self.replayer.is_synthetic_key(key)):
                self._triple_ctrl.on_press(key)
                self._poll_hotkeys.on_press(key)
        except Exception as e:
            print(f"[notos-mkr] polled key press error: {e}")

    def _on_polled_release(self, key) -> None:
        if self._shutdown:
            return
        try:
            if self._recording:
                self.recorder.on_release(key)
            if self._hotkeys_enabled:
                self._triple_ctrl.on_release(key)
                self._poll_hotkeys.on_release(key)
        except Exception as e:
            print(f"[notos-mkr] polled key release error: {e}")

    def _on_polled_repeat(self, key) -> None:
        """按住键的自动重复：只回灌给录制器，不触发热键/接管。"""
        if self._shutdown or not self._recording:
            return
        try:
            self.recorder.on_press(key)
        except Exception as e:
            print(f"[notos-mkr] polled key repeat error: {e}")

    def start_hotkeys(self) -> None:
        self._hotkeys_enabled = True

        # Windows：统一用 GetAsyncKeyState 轮询，绕开可能失效的键盘钩子
        if sys.platform == "win32":
            self._ensure_poll_subscription()
            return

        # 其它平台保持 pynput 钩子实现
        self._hk_listener = keyboard.GlobalHotKeys({
            HOTKEY_PLAYBACK_START: self._hk_play,
            HOTKEY_PLAYBACK_STOP:  self._hk_stop_all,
            HOTKEY_QUIT:           self._hk_quit,
        })
        self._hk_listener.start()

        self._hk_listener_extra = keyboard.Listener(
            on_press=self._triple_ctrl.on_press,
            on_release=self._triple_ctrl.on_release,
        )
        self._hk_listener_extra.start()

    def stop_hotkeys(self) -> None:
        self._hotkeys_enabled = False
        if sys.platform == "win32":
            # 轮询订阅保留到 shutdown，录制/回放仍需要它
            return
        for lst in (self._hk_listener, self._hk_listener_extra):
            if lst:
                lst.stop()
        self._hk_listener = None
        self._hk_listener_extra = None

    def _hk_toggle_record(self):
        """三连 Ctrl → 开始/停止录制。"""
        if self.replayer.is_playing:
            return
        self.toggle_record()

    def _hk_play(self):
        if self.replayer.is_playing or self._recording:
            return
        # 与胶囊按钮共用同一个循环占位；否则快捷键路径会退回默认 1 次。
        self.replay(loops=self.playback_loops)

    def _hk_stop_all(self):
        if self._recording:
            self.stop_record()
        if self.replayer.is_playing:
            self.stop_replay()

    def _hk_quit(self):
        self.shutdown()

    # ---------- 录制 ----------
    def toggle_record(self) -> None:
        if self._recording:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self) -> None:
        with self._lock:
            if self._recording:
                return
            if self.replayer.is_playing:
                return
            self._recording = True

        self.recorder.ignore_point = self.recording_filter

        # 诊断：记录前台目标是否 Elevated。普通权限的 notos-mkr 在 Elevated
        # 前台窗口上会被 UIPI 阻止捕获/注入键鼠。
        try:
            fg_pid, fg_elevated = _foreground_process_info()
            log.info(
                "record start: foreground_pid=%s foreground_elevated=%s "
                "current_elevated=%s",
                fg_pid, fg_elevated, _current_process_elevated(),
            )
            if fg_elevated and not _current_process_elevated():
                log.warning(
                    "前台窗口属于 Elevated 进程；Windows UIPI 会阻止普通权限 "
                    "notos-mkr 捕获/注入键鼠，请用 run_gui_admin.cmd 以管理员身份启动。"
                )
        except Exception:
            pass

        mouse_listener = None
        try:
            self.recorder.start()
            mouse_listener = mouse.Listener(
                on_move=self.recorder.on_move,
                on_click=self.recorder.on_click,
                on_scroll=self.recorder.on_scroll,
            )
            mouse_listener.start()

            if sys.platform == "win32":
                self._kb_listener = None
                self._ensure_poll_subscription()
            else:
                self._kb_listener = keyboard.Listener(
                    on_press=self.recorder.on_press,
                    on_release=self.recorder.on_release,
                )
                self._kb_listener.start()
        except Exception as e:
            log.exception("record start failed")
            with self._lock:
                self._recording = False
            self.recorder.ignore_point = None
            try:
                self.recorder.stop()
            except Exception:
                pass
            for lst in (mouse_listener, self._kb_listener):
                if lst:
                    try:
                        lst.stop()
                    except Exception:
                        pass
            self._mouse_listener = None
            self._kb_listener = None
            self._emit("status", text=f"开始录制失败: {e}")
            raise

        self._mouse_listener = mouse_listener
        log.info(
            "recording started: mouse_listener=%s poll_subscribed=%s",
            bool(mouse_listener and mouse_listener.running),
            self._poll_subscribed,
        )
        # 监听器已经真正启动后才通知 GUI 进入录制态；失败时 GUI 不会
        # 误显示“录制中”却永远等不到事件。
        self._emit("record_started")

    def stop_record(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        self.recorder.stop()
        self.recorder.ignore_point = None
        for lst in (self._mouse_listener, self._kb_listener):
            if lst:
                lst.stop()
        self._mouse_listener = None
        self._kb_listener = None
        events = list(self.recorder.events)
        counts: dict[str, int] = {}
        for ev in events:
            typ = ev.get("type", "?")
            counts[typ] = counts.get(typ, 0) + 1
        log.info("recording stopped: total=%d counts=%s", len(events), counts)
        self._emit("record_stopped", events=events)

    # ---------- 回放 ----------
    def replay(self, events: list[dict] | None = None,
               speed: float = 1.0, loops: int = 1) -> bool:
        if events is None:
            events = self.recorder.events
        if not events:
            self._emit("status", text="没有可回放的事件，请先录制")
            return False
        if self.replayer.is_playing:
            return False

        def _on_finish(takeover: bool = False):
            if takeover:
                self._emit("play_taken_over")
            else:
                self._emit("play_finished")

        self._emit("play_started")
        try:
            ok = self.replayer.play(
                events, speed=speed, loops=loops,
                on_finish=_on_finish,
                on_event=self._on_replay_event,
            )
        except Exception as e:
            print(f"[notos-mkr] replay start error: {e}")
            self._emit("play_finished")
            return False
        if not ok:
            self._emit("play_finished")
        return ok

    def stop_replay(self) -> None:
        if self.replayer.is_playing:
            self.replayer.stop()

    # ---------- 清空 ----------
    def clear_events(self) -> None:
        self.recorder.events = []
        self._emit("events_cleared")

    # ---------- 关闭 ----------
    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._hotkeys_enabled = False
        if self._recording:
            self.stop_record()
        if self.replayer.is_playing:
            self.replayer.stop()
        self.stop_hotkeys()
        if self._poll_subscribed:
            try:
                from .keyboard_polling import get_keyboard_service
                get_keyboard_service().unsubscribe(self._poll_token)
            except Exception:
                pass
            self._poll_subscribed = False
            self._poll_token = None
        release_modifiers()
        self._emit("quit")
