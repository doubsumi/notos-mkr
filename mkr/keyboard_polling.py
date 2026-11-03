"""Windows 键盘轮询后端。

背景：部分 Windows 环境（安全软件、Elevated 前台窗口、远程/隔离桌面等）里
``WH_KEYBOARD_LL`` 钩子可以安装成功，但回调永远收不到键盘事件；Raw Input
也可能被拦截。``GetAsyncKeyState`` 直接读取桌面键盘状态，不依赖钩子，因此
作为 Windows 下录制/热键/接管的键盘后端。

限制：
- 只检测按键状态变化，不产生系统自动重复事件（按住一个键只记录一次）。
- 左右修饰键合并为通用 Ctrl / Shift / Alt。
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from pynput import keyboard

log = logging.getLogger("mkr.keyboard")

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
else:  # pragma: no cover - 非 Windows 不启动轮询
    _user32 = None


def _build_vks() -> tuple[int, ...]:
    # 0x01-0x07 是鼠标按钮，跳过；0xA0-0xA5 是左右修饰键，
    # 会和 0x10-0x12 重复，跳过；0xE5/0xE7 是 IME 处理键/包键，
    # 物理按键本身会以对应 VK 被轮询到。其余键全部覆盖，
    # 避免不常见的 OEM/IME/媒体键漏录。
    skip = set(range(0x00, 0x08)) | set(range(0xA0, 0xA6)) | {0xE5, 0xE7, 0xFF}
    return tuple(vk for vk in range(0x00, 0x100) if vk not in skip)


_VKS = _build_vks()

# 允许系统自动重复的键（字母/数字/标点/退格/方向键等）
_REPEATABLE = set(range(0x30, 0x3A))
_REPEATABLE |= set(range(0x41, 0x5B))
_REPEATABLE |= set(range(0x60, 0x70))
_REPEATABLE |= set(range(0xBA, 0xE3))
_REPEATABLE |= {
    0x08, 0x09, 0x0D, 0x20,
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2E,
}

# VK -> pynput keyboard.Key（有语义的键）
_SPECIAL: dict[int, keyboard.Key] = {}
for _key in keyboard.Key:
    try:
        _vk = int(getattr(_key.value, "vk", 0) or 0)
    except Exception:
        _vk = 0
    if _vk:
        _SPECIAL.setdefault(_vk, _key)
_SPECIAL.pop(0, None)
# pynput 没有 VK_SHIFT 的通用 Key（Key.shift 实际是 VK_LSHIFT），
# 这里显式把轮询到的通用修饰键映射成有语义的 Key。
_SPECIAL.setdefault(0x10, keyboard.Key.shift)
_SPECIAL.setdefault(0x11, keyboard.Key.ctrl)
_SPECIAL.setdefault(0x12, keyboard.Key.alt)

_KEY_CACHE: list = [None] * 256


def _key_for_vk(vk: int):
    key = _KEY_CACHE[vk]
    if key is not None:
        return key
    key = _SPECIAL.get(vk)
    if key is None:
        try:
            key = keyboard.KeyCode(vk=vk)
        except Exception:
            return None
    _KEY_CACHE[vk] = key
    return key


class PollingKeyboardService:
    """单例轮询服务；订阅者收到 ``on_press(key)`` / ``on_release(key)``。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[int, tuple] = {}
        self._next_token = 1
        self._thread: threading.Thread | None = None
        self._reset = True

    @property
    def available(self) -> bool:
        return _IS_WINDOWS and _user32 is not None

    def subscribe(self, on_press, on_release, on_repeat=None) -> int | None:
        if not self.available:
            return None
        with self._lock:
            token = self._next_token
            self._next_token += 1
            was_empty = not self._subs
            self._subs[token] = (on_press, on_release, on_repeat)
            if was_empty:
                self._reset = True
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="mkr-keyboard-poll",
                    daemon=True,
                )
                self._thread.start()
                log.info("keyboard polling service started (%d VKs)", len(_VKS))
        return token

    def unsubscribe(self, token: int | None) -> None:
        if token is None:
            return
        with self._lock:
            self._subs.pop(token, None)
            if not self._subs:
                self._reset = True

    def _snapshot(self):
        with self._lock:
            reset = self._reset
            self._reset = False
            return reset, list(self._subs.values())

    def _run(self) -> None:
        if _user32 is None:
            return
        prev = [False] * 256
        hold: dict[int, tuple[float, float]] = {}
        logged_first = False

        def _emit(subs, index: int, key) -> None:
            for sub in subs:
                cb = sub[index]
                if cb is None:
                    continue
                try:
                    cb(key)
                except Exception:
                    log.exception("keyboard poll callback")

        while True:
            try:
                reset, subs = self._snapshot()
                if reset:
                    for vk in _VKS:
                        prev[vk] = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    hold.clear()
                if not subs:
                    time.sleep(0.05)
                    continue

                now = time.perf_counter()
                for vk in _VKS:
                    down = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    if down != prev[vk]:
                        prev[vk] = down
                        key = _key_for_vk(vk)
                        if key is None:
                            continue
                        if down:
                            if not logged_first:
                                log.info("keyboard polling first key: vk=%#x",
                                         vk)
                                logged_first = True
                            if vk in _REPEATABLE:
                                hold[vk] = (now, now + 0.50)
                            _emit(subs, 0, key)
                        else:
                            hold.pop(vk, None)
                            _emit(subs, 1, key)
                    elif down and vk in hold:
                        started, next_at = hold[vk]
                        if now >= next_at:
                            hold[vk] = (started, now + 0.030)
                            key = _key_for_vk(vk)
                            if key is not None:
                                _emit(subs, 2, key)
                time.sleep(0.010)
            except Exception:
                log.exception("keyboard polling loop error")
                time.sleep(0.05)


_service: PollingKeyboardService | None = None
_service_lock = threading.Lock()


def get_keyboard_service() -> PollingKeyboardService:
    global _service
    with _service_lock:
        if _service is None:
            _service = PollingKeyboardService()
        return _service
