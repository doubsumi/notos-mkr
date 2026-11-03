"""把录制事件转成胶囊上显示的中文操作描述。"""
from __future__ import annotations

from typing import Any

_SPECIAL_NAMES = {
    "ctrl": "Ctrl", "ctrl_l": "Ctrl", "ctrl_r": "Ctrl",
    "alt": "Alt", "alt_l": "Alt", "alt_r": "Alt", "alt_gr": "Alt",
    "shift": "Shift", "shift_l": "Shift", "shift_r": "Shift",
    "cmd": "Win", "cmd_l": "Win", "cmd_r": "Win",
    "enter": "Enter", "space": "空格", "tab": "Tab",
    "backspace": "退格", "delete": "Delete", "esc": "Esc",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "home": "Home", "end": "End",
    "page_up": "PgUp", "page_down": "PgDn",
    "insert": "Insert", "print_screen": "PrtSc", "pause": "Pause",
    "caps_lock": "CapsLock", "num_lock": "NumLock", "scroll_lock": "ScrLk",
    "menu": "Menu", "apps": "Menu",
    "media_play_pause": "播放/暂停", "media_volume_mute": "静音",
    "media_volume_down": "音量-", "media_volume_up": "音量+",
    "media_next": "下一曲", "media_previous": "上一曲",
}

_BUTTON_NAMES = {
    "left": "左键", "right": "右键", "middle": "中键",
    "x1": "侧键", "x2": "侧键",
}

_VK_LABELS = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
    0x20: "空格", 0x2C: "PrtSc", 0x2D: "Insert", 0x2E: "Delete",
    0x2F: "Help", 0x5B: "Win", 0x5C: "Win", 0x5D: "Menu",
    0x90: "NumLock", 0x91: "ScrLk",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]",
    0xDE: "'", 0xE2: "\\",
}
for _i in range(26):
    _VK_LABELS[0x41 + _i] = chr(ord("A") + _i)
for _i in range(10):
    _VK_LABELS[0x30 + _i] = str(_i)
    _VK_LABELS[0x60 + _i] = f"Num{_i}"
for _i in range(24):
    _VK_LABELS[0x70 + _i] = f"F{_i + 1}"
_VK_LABELS.update({
    0x6A: "Num*", 0x6B: "Num+", 0x6C: "Num.", 0x6D: "Num-",
    0x6E: "Num.", 0x6F: "Num/",
})


def _key_label(key: dict[str, Any] | None) -> str | None:
    if not isinstance(key, dict):
        return None
    kind = key.get("kind")
    value = key.get("value")
    if kind == "special":
        name = str(value)
        if name in _SPECIAL_NAMES:
            return _SPECIAL_NAMES[name]
        if name.startswith("f") and name[1:].isdigit():
            return name.upper()
        return name.replace("_", " ").title()
    if kind == "char":
        if value is None:
            return None
        text = str(value)
        if text == " ":
            return "空格"
        if text in ("\r", "\n"):
            return "Enter"
        if text == "\t":
            return "Tab"
        if len(text) == 1 and ord(text) < 32:
            return None
        return text
    if kind == "vk":
        if value is None:
            return None
        try:
            return _VK_LABELS.get(int(value), f"VK{value}")
        except (TypeError, ValueError):
            return None
    return None


def describe_event(ev: dict, include_release: bool = False) -> str | None:
    """返回一条适合小字显示的操作描述；无可显示内容时返回 None。"""
    if not isinstance(ev, dict):
        return None
    typ = ev.get("type")
    if typ == "move":
        return "移动鼠标"
    if typ == "button":
        pressed = bool(ev.get("pressed"))
        if not pressed and not include_release:
            return None
        name = _BUTTON_NAMES.get(str(ev.get("button")), "鼠标键")
        return f"{name}按下" if pressed else f"{name}松开"
    if typ == "scroll":
        dy = int(ev.get("dy") or 0)
        dx = int(ev.get("dx") or 0)
        if dy > 0:
            return "向上滚动"
        if dy < 0:
            return "向下滚动"
        if dx > 0:
            return "向右滚动"
        if dx < 0:
            return "向左滚动"
        return "滚动"
    if typ == "key":
        pressed = bool(ev.get("pressed"))
        if not pressed and not include_release:
            return None
        label = _key_label(ev.get("key"))
        if not label:
            return None
        return f"按下 {label}"
    return None


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
