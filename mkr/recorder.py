"""录制器 / 回放器。"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable

from pynput import keyboard, mouse


# ---------- 按键序列化 ----------
def _serialize_key(key) -> dict:
    if key is None:
        return {"kind": "vk", "value": None}
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return {"kind": "char", "value": key.char}
        return {"kind": "vk", "value": getattr(key, "vk", None)}
    return {"kind": "special", "value": key.name}


def _deserialize_key(ev: dict):
    kind = ev["kind"]
    if kind == "char":
        return keyboard.KeyCode.from_char(ev["value"])
    if kind == "special":
        return getattr(keyboard.Key, ev["value"])
    if kind == "vk":
        return keyboard.KeyCode(vk=ev["value"])
    raise ValueError(f"Unknown key kind: {kind}")


def _key_id(key) -> str:
    """键的稳定标识，用于跟踪修饰键状态。"""
    if key is None:
        return "vk:None"
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return f"char:{key.char}"
        return f"vk:{getattr(key, 'vk', None)}"
    return f"special:{key.name}"


def _normalize_vk(vk: int) -> int:
    """把左右修饰键归一到通用 VK，便于轮询守卫匹配。"""
    if vk in (0x10, 0xA0, 0xA1):
        return 0x10
    if vk in (0x11, 0xA2, 0xA3):
        return 0x11
    if vk in (0x12, 0xA4, 0xA5):
        return 0x12
    return vk


def _key_vk(key) -> int | None:
    """取 key 的虚拟键码，用于回放注入的按键守卫。"""
    if key is None:
        return None
    vk = getattr(key, "vk", None)
    if vk:
        return _normalize_vk(int(vk))
    value = getattr(key, "value", None)
    vk = getattr(value, "vk", None)
    if vk:
        return _normalize_vk(int(vk))
    # 旧版录制里可能存在 char 事件；用 VkKeyScan 反查 VK
    char = getattr(key, "char", None)
    if char:
        try:
            from pynput._util.win32 import VkKeyScan
            scan = int(VkKeyScan(char))
            vk = scan & 0xFF
            if vk and vk != 0xFF:
                return _normalize_vk(vk)
        except Exception:
            pass
    return None


# ---------- 修饰键清理 ----------
MODIFIER_KEY_NAMES = (
    "alt", "alt_l", "alt_r", "alt_gr",
    "ctrl", "ctrl_l", "ctrl_r",
    "shift", "shift_l", "shift_r",
    "cmd", "cmd_l", "cmd_r",
)
MODIFIER_SPECIAL_IDS = {f"special:{n}" for n in MODIFIER_KEY_NAMES}
_CTRL_NAMES = {"ctrl", "ctrl_l", "ctrl_r"}
_ALT_NAMES = {"alt", "alt_l", "alt_r", "alt_gr"}


def release_modifiers(kb: keyboard.Controller | None = None) -> None:
    kb = kb or keyboard.Controller()
    for name in MODIFIER_KEY_NAMES:
        try:
            kb.release(getattr(keyboard.Key, name))
        except Exception:
            pass


# ---------- 录制器 ----------
class Recorder:
    def __init__(self, min_move_interval: float = 0.015):
        self.events: list[dict] = []
        self.is_recording = False
        self.min_move_interval = min_move_interval
        self._start_time = 0.0
        self._last_move_time = 0.0
        self._lock = threading.Lock()
        self._mouse_ctrl = mouse.Controller()
        # 当前被按下但尚未 release 的键（防止 stop 时修饰键事件残缺）
        self._pressed_keys: set[str] = set()

        # GUI 注入点：实时事件回调 / 胶囊区域过滤
        self.on_event: Callable[[dict], None] | None = None
        self.ignore_point: Callable[[int, int], bool] | None = None

    def start(self) -> None:
        with self._lock:
            self.events = []
            self._start_time = time.perf_counter()
            self._last_move_time = 0.0
            self._pressed_keys.clear()
            self.is_recording = True
        try:
            x, y = self._mouse_ctrl.position
            if not (self.ignore_point and self.ignore_point(int(x), int(y))):
                self._record("move", x=int(x), y=int(y))
        except Exception:
            pass

    def stop(self) -> None:
        with self._lock:
            self.is_recording = False
            # 补录：对所有仍处于"按下"状态的键（主要是 stop 热键的修饰键）
            # 生成 release 事件，确保回放时修饰键状态完整
            now = time.perf_counter() - self._start_time
            for key_id in list(self._pressed_keys):
                special_name = key_id.split(":", 1)[1] if key_id.startswith("special:") else None
                if special_name:
                    k = getattr(keyboard.Key, special_name, None)
                else:
                    k = None
                if k is not None:
                    self.events.append({
                        "t": round(now + 0.001, 6),  # +1ms 保证在最后
                        "type": "key", "pressed": False,
                        "key": {"kind": "special", "value": special_name},
                    })
            self._pressed_keys.clear()

    def _point_ignored(self, x: int, y: int) -> bool:
        if self.ignore_point is None:
            return False
        try:
            return bool(self.ignore_point(int(x), int(y)))
        except Exception:
            return False

    def _record(self, type_: str, **data: Any) -> None:
        if not self.is_recording:
            return
        ev = None
        with self._lock:
            if not self.is_recording:
                return
            t = time.perf_counter() - self._start_time
            ev = {"t": round(t, 6), "type": type_, **data}
            self.events.append(ev)
        if ev is not None and self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:
                pass

    # --- pynput 回调 ---
    def on_move(self, x: int, y: int) -> None:
        if not self.is_recording:
            return
        if self._point_ignored(x, y):
            return
        now = time.perf_counter()
        if now - self._last_move_time < self.min_move_interval:
            return
        self._last_move_time = now
        self._record("move", x=int(x), y=int(y))

    def on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if self._point_ignored(x, y):
            return
        self._record(
            "button",
            x=int(x), y=int(y),
            button=getattr(button, "name", str(button)),
            pressed=bool(pressed),
        )

    def on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if self._point_ignored(x, y):
            return
        self._record("scroll", x=int(x), y=int(y), dx=int(dx), dy=int(dy))

    def on_press(self, key) -> None:
        self._record("key", pressed=True, key=_serialize_key(key))
        self._pressed_keys.add(_key_id(key))

    def on_release(self, key) -> None:
        self._record("key", pressed=False, key=_serialize_key(key))
        self._pressed_keys.discard(_key_id(key))


# ---------- 回放器 ----------
class Replayer:
    """回放器，并在回放过程中检测人为鼠标/键盘操作。

    Windows 下 pynput 会传入 ``injected`` 标志，因此可以精确忽略
    自己注入的事件；其它平台退化为“最近注入事件签名”匹配。
    """

    def __init__(self):
        self.is_playing = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()
        self._held_modifiers: set[str] = set()

        # 接管检测
        self._takeover_detected = False
        self._tk_listener: keyboard.Listener | None = None
        self._tk_mouse_listener: mouse.Listener | None = None
        self._takeover_filter: Callable[[str, int, int, Any], bool] | None = None
        self._takeover_grace = 0.40
        self._play_started_at = 0.0
        self._human_modifiers: set[str] = set()
        self._synthetic_keys: dict[int, float] = {}
        self._synthetic_key_guard = 0.15

        # 轮询键盘订阅（Windows）
        self._poll_subscribed = False
        self._poll_token = None

        # 非 Windows 的兜底：记录最近注入事件，避免把回放动作当成接管
        self._injected_supported = sys.platform.startswith("win")
        self._recent_injected: dict[str, tuple[Any, float]] = {}
        self._recent_lock = threading.Lock()

    # ---------- 配置 ----------
    def set_takeover_filter(self, fn: Callable[[str, int, int, Any], bool] | None) -> None:
        """注册接管过滤：返回 True 表示忽略该人为事件。"""
        self._takeover_filter = fn

    def set_takeover_grace(self, seconds: float) -> None:
        self._takeover_grace = max(0.0, float(seconds))

    # ---------- 播放 ----------
    def play(self, events: list[dict], speed: float = 1.0,
             loops: int | None = 1, on_finish=None,
             on_event: Callable[[dict], None] | None = None) -> bool:
        """loops: None/0 表示一直循环，直到停止或被人工接管。"""
        if self.is_playing or not events:
            return False
        self._stop.clear()
        self.is_playing = True
        self._takeover_detected = False
        self._play_started_at = time.perf_counter()
        self._human_modifiers.clear()
        with self._recent_lock:
            self._recent_injected.clear()

        try:
            self._start_takeover_detection()
        except Exception:
            self.is_playing = False
            self._stop_takeover_detection()
            raise

        self._thread = threading.Thread(
            target=self._run, args=(events, speed, loops, on_finish, on_event),
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ---------- 接管检测 ----------
    def _in_grace(self) -> bool:
        return (time.perf_counter() - self._play_started_at) < self._takeover_grace

    def _mark_synthetic_key(self, key) -> None:
        """记录一次回放按键注入，供轮询后端忽略自己发出的这个键。"""
        vk = _key_vk(key)
        if vk is not None:
            self._synthetic_keys[vk] = time.perf_counter() + self._synthetic_key_guard

    def is_synthetic_key(self, key) -> bool:
        vk = _key_vk(key)
        if vk is None:
            return False
        until = self._synthetic_keys.get(vk)
        if until is None:
            return False
        if time.perf_counter() < until:
            return True
        self._synthetic_keys.pop(vk, None)
        return False

    def _trigger_takeover(self) -> None:
        if not self._takeover_detected:
            self._takeover_detected = True
            self._stop.set()

    def _ignored_by_ui(self, kind: str, x: int, y: int, key: Any) -> bool:
        if self._takeover_filter is None:
            return False
        try:
            return bool(self._takeover_filter(kind, int(x), int(y), key))
        except Exception:
            return False

    def _ctrl_alt_down(self) -> bool:
        return bool(self._human_modifiers & _CTRL_NAMES) and \
            bool(self._human_modifiers & _ALT_NAMES)

    def _takeover_key_press(self, key, injected: bool = False) -> None:
        if injected:
            return
        if self.is_synthetic_key(key):
            return
        try:
            name = key.name if isinstance(key, keyboard.Key) else None
        except Exception:
            name = None
        if name in MODIFIER_KEY_NAMES:
            self._human_modifiers.add(name)
            return
        # 应用自身热键均带 Ctrl+Alt，不把热键组合判为接管
        if self._ctrl_alt_down():
            return
        if self._in_grace():
            return
        if self._ignored_by_ui("key", 0, 0, key):
            return
        if not self._injected_supported and self._recently_injected("key", key):
            return
        self._trigger_takeover()

    def _takeover_key_release(self, key, injected: bool = False) -> None:
        if injected:
            return
        try:
            name = key.name if isinstance(key, keyboard.Key) else None
        except Exception:
            name = None
        if name in MODIFIER_KEY_NAMES:
            self._human_modifiers.discard(name)

    def _takeover_mouse_move(self, x: int, y: int, injected: bool = False) -> None:
        if injected or self._in_grace():
            return
        if self._ignored_by_ui("move", x, y, None):
            return
        if not self._injected_supported and self._recently_injected("move", (x, y)):
            return
        self._trigger_takeover()

    def _takeover_mouse_click(self, x: int, y: int, button, pressed: bool,
                              injected: bool = False) -> None:
        if injected or self._in_grace():
            return
        if self._ignored_by_ui("click", x, y, button):
            return
        if not self._injected_supported and self._recently_injected(
                "click", (x, y, getattr(button, "name", str(button)), pressed)):
            return
        self._trigger_takeover()

    def _takeover_mouse_scroll(self, x: int, y: int, dx: int, dy: int,
                               injected: bool = False) -> None:
        if injected or self._in_grace():
            return
        if self._ignored_by_ui("scroll", x, y, (dx, dy)):
            return
        if not self._injected_supported and self._recently_injected(
                "scroll", (x, y, dx, dy)):
            return
        self._trigger_takeover()

    def _start_takeover_detection(self) -> None:
        self._tk_mouse_listener = mouse.Listener(
            on_move=self._takeover_mouse_move,
            on_click=self._takeover_mouse_click,
            on_scroll=self._takeover_mouse_scroll,
        )
        self._tk_mouse_listener.start()

        if sys.platform == "win32":
            # 键盘走 GetAsyncKeyState 轮询；回放自己注入的键由 guard 忽略
            try:
                from .keyboard_polling import get_keyboard_service
                service = get_keyboard_service()
                self._poll_token = service.subscribe(
                    self._takeover_polled_press,
                    self._takeover_polled_release,
                )
                self._poll_subscribed = self._poll_token is not None
            except Exception:
                self._poll_subscribed = False
        else:
            self._tk_listener = keyboard.Listener(
                on_press=self._takeover_key_press,
                on_release=self._takeover_key_release,
            )
            self._tk_listener.start()

    def _takeover_polled_press(self, key) -> None:
        if self.is_playing:
            self._takeover_key_press(key)

    def _takeover_polled_release(self, key) -> None:
        self._takeover_key_release(key)

    def _stop_takeover_detection(self) -> None:
        if self._poll_subscribed:
            try:
                from .keyboard_polling import get_keyboard_service
                get_keyboard_service().unsubscribe(self._poll_token)
            except Exception:
                pass
            self._poll_subscribed = False
            self._poll_token = None
        for lst in (self._tk_listener, self._tk_mouse_listener):
            if lst:
                lst.stop()
        self._tk_listener = None
        self._tk_mouse_listener = None

    # ---------- 非 Windows 兜底 ----------
    def _mark_injected(self, key: str, value: Any) -> None:
        if self._injected_supported:
            return
        with self._recent_lock:
            self._recent_injected[key] = (value, time.perf_counter() + 0.20)

    def _recently_injected(self, kind: str, value: Any) -> bool:
        with self._recent_lock:
            item = self._recent_injected.get(kind)
            if not item:
                return False
            last_value, deadline = item
            if time.perf_counter() > deadline:
                return False
        if kind == "move":
            try:
                return abs(last_value[0] - value[0]) <= 2 and abs(last_value[1] - value[1]) <= 2
            except Exception:
                return False
        return last_value == value

    # ---------- 内部 ----------
    def _run(self, events, speed, loops, on_finish, on_event):
        try:
            self._release_held_keys()
            infinite = loops is None or int(loops) <= 0
            if infinite:
                while not self._stop.is_set():
                    self._play_once(events, speed, on_event)
            else:
                for _ in range(max(1, int(loops))):
                    if self._stop.is_set():
                        break
                    self._play_once(events, speed, on_event)
        finally:
            self._release_held_keys()
            self.is_playing = False
            self._stop_takeover_detection()
            if on_finish:
                try:
                    on_finish(takeover=self._takeover_detected)
                except Exception:
                    pass

    def _play_once(self, events: list[dict], speed: float, on_event=None):
        speed = max(speed, 0.01)
        t0 = time.perf_counter()
        for ev in events:
            if self._stop.is_set():
                return
            target = ev["t"] / speed
            delay = target - (time.perf_counter() - t0)
            if delay > 0 and self._stop.wait(timeout=delay):
                return
            if self._stop.is_set():
                return
            if not self._injected_supported:
                self._mark_injected(
                    ev["type"],
                    {
                        "move": (ev.get("x"), ev.get("y")),
                        "button": (ev.get("x"), ev.get("y"), ev.get("button"), ev.get("pressed")),
                        "scroll": (ev.get("x"), ev.get("y"), ev.get("dx"), ev.get("dy")),
                        "key": _deserialize_key(ev["key"]) if ev.get("type") == "key" else None,
                    }.get(ev.get("type")),
                )
            self._dispatch(ev)
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass

    def _dispatch(self, ev: dict):
        typ = ev["type"]
        if typ == "move":
            self._mouse.position = (ev["x"], ev["y"])
        elif typ == "button":
            self._mouse.position = (ev["x"], ev["y"])
            btn = getattr(mouse.Button, ev["button"])
            (self._mouse.press if ev["pressed"] else self._mouse.release)(btn)
        elif typ == "scroll":
            self._mouse.position = (ev["x"], ev["y"])
            self._mouse.scroll(ev["dx"], ev["dy"])
        elif typ == "key":
            k = _deserialize_key(ev["key"])
            self._mark_synthetic_key(k)
            kid = _key_id(k)
            try:
                if ev["pressed"]:
                    if kid in self._held_modifiers:
                        # 自动重复：非修饰键允许再次按下；修饰键只按一次
                        if (kid in MODIFIER_SPECIAL_IDS
                                or _key_vk(k) in (0x10, 0x11, 0x12)):
                            return
                    self._keyboard.press(k)
                    self._held_modifiers.add(kid)
                else:
                    # 孤儿 release：从未 press 过则跳过
                    if kid in self._held_modifiers:
                        self._keyboard.release(k)
                        self._held_modifiers.discard(kid)
            except Exception:
                pass

    def _release_held_keys(self):
        """释放回放过程中自己按下、但录制里没有 release 的键。"""
        for kid in list(self._held_modifiers):
            try:
                if kid.startswith("special:"):
                    name = kid.split(":", 1)[1]
                    key = getattr(keyboard.Key, name, None)
                elif kid.startswith("char:"):
                    key = keyboard.KeyCode.from_char(kid.split(":", 1)[1])
                elif kid.startswith("vk:"):
                    raw = kid.split(":", 1)[1]
                    key = (keyboard.KeyCode(vk=int(raw))
                           if raw not in ("None", "") else None)
                else:
                    key = None
                if key is not None:
                    self._keyboard.release(key)
            except Exception:
                pass
        self._held_modifiers.clear()
