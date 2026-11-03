"""MCP Server —— 供本机 Agent 通过 stdio 调用的录制/回放能力。

Agent 配置示例（在 Claude Desktop / Cursor / Cline 等里）::

    "mcpServers": {
      "mkr": {
        "command": "mkr-mcp",
        "args": []
      }
    }

直接运行（stdio 模式）::

    mkr-mcp
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from mcp.server import MCPServer as FastMCP

from .app import App
from .recorder import Replayer, release_modifiers
from . import storage

mcp = FastMCP("mkr")

# 单例 App / Replayer，MCP 工具共享同一实例
_app: App | None = None
_replayer: Replayer | None = None
_state_lock = threading.Lock()


def _get_app() -> App:
    global _app
    if _app is None:
        _app = App()
        # MCP server 场景下默认不自动启动热键——Agent 控制；
        # 但仍启用以支持用户手动干预
        _app.start_hotkeys()
    return _app


def _get_replayer() -> Replayer:
    global _replayer
    if _replayer is None:
        _replayer = Replayer()
    return _replayer


# ---------- MCP Tools ----------

@mcp.tool()
def start_record() -> dict:
    """开始录制鼠标键盘操作。返回录制前状态。"""
    app = _get_app()
    with _state_lock:
        if app.is_recording:
            return {"ok": False, "error": "已在录制中"}
        if app.replayer.is_playing:
            return {"ok": False, "error": "回放进行中，请先 stop_replay"}
        app.start_record()
        return {"ok": True, "state": "recording"}


@mcp.tool()
def stop_record() -> dict:
    """停止录制。返回事件数与时长。"""
    app = _get_app()
    with _state_lock:
        if not app.is_recording:
            return {"ok": False, "error": "当前未在录制"}
        app.stop_record()
        evs = app.recorder.events
        dur = evs[-1]["t"] if evs else 0.0
        return {"ok": True, "events": len(evs), "duration": round(dur, 2)}


@mcp.tool()
def replay(speed: float = 1.0, loops: int = 1) -> dict:
    """回放当前内存中的录制。

    Args:
        speed: 回放速度倍率 (0.1-10.0, 默认 1.0)
        loops: 循环次数 (默认 1)
    """
    app = _get_app()
    with _state_lock:
        if app.is_recording:
            return {"ok": False, "error": "录制中，请先 stop_record"}
        if not app.recorder.events:
            return {"ok": False, "error": "内存中无录制，先 start_record + stop_record 或 replay_file"}
        if app.replayer.is_playing:
            return {"ok": False, "error": "已在回放中"}
        ok = app.replay(speed=speed, loops=loops)
        return {"ok": ok, "speed": speed, "loops": loops}


@mcp.tool()
def replay_file(path: str, speed: float = 1.0, loops: int = 1) -> dict:
    """从 JSON 文件加载并回放。

    Args:
        path: 录制文件路径 (.json)
        speed: 回放速度倍率 (默认 1.0)
        loops: 循环次数 (默认 1)
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在: {p}"}
    try:
        events = storage.load(str(p))
    except Exception as e:
        return {"ok": False, "error": f"加载失败: {e}"}
    if not events:
        return {"ok": False, "error": "文件为空"}
    rp = _get_replayer()
    with _state_lock:
        if rp.is_playing:
            return {"ok": False, "error": "已在回放中"}
        rp.play(events, speed=speed, loops=loops)
        return {
            "ok": True, "events": len(events),
            "duration": round(events[-1]["t"], 2),
            "speed": speed, "loops": loops,
        }


@mcp.tool()
def stop_replay() -> dict:
    """停止正在进行的回放。"""
    app = _app
    rp = _get_replayer()
    with _state_lock:
        if app and app.replayer.is_playing:
            app.replayer.stop()
        rp.stop()
        release_modifiers()
        return {"ok": True}


@mcp.tool()
def save(path: str) -> dict:
    """把内存中已录制的事件保存为 JSON 文件。

    Args:
        path: 保存路径 (.json)
    """
    app = _app
    if app is None or not app.recorder.events:
        return {"ok": False, "error": "内存中无录制内容"}
    p = Path(path).expanduser()
    try:
        storage.save(app.recorder.events, str(p))
        return {"ok": True, "events": len(app.recorder.events), "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": f"保存失败: {e}"}


@mcp.tool()
def load(path: str) -> dict:
    """从 JSON 文件加载录制到内存（供后续 replay 使用）。

    Args:
        path: 录制文件路径 (.json)
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在: {p}"}
    try:
        events = storage.load(str(p))
    except Exception as e:
        return {"ok": False, "error": f"加载失败: {e}"}
    app = _get_app()
    # 写入 recorder.events，供 app.replay() 使用
    app.recorder.events = events
    dur = events[-1]["t"] if events else 0.0
    return {"ok": True, "events": len(events), "duration": round(dur, 2)}


@mcp.tool()
def get_status() -> dict:
    """查询当前状态：是否录制中、是否回放中、内存事件数、时长。"""
    app = _app
    rp = _replayer
    events = app.recorder.events if app else []
    dur = events[-1]["t"] if events else 0.0
    return {
        "is_recording": bool(app and app.is_recording),
        "is_playing": bool(
            (app and app.replayer.is_playing) or (rp and rp.is_playing)
        ),
        "events": len(events),
        "duration": round(dur, 2),
        "platform": __import__("platform").system(),
    }


@mcp.tool()
def clear() -> dict:
    """清空内存中已录制的事件。"""
    app = _app
    if app:
        app.recorder.events = []
    return {"ok": True}


# ---------- Entry ----------
def main() -> None:
    """MCP stdio server 入口。"""
    import atexit
    atexit.register(release_modifiers)
    # 保持与 GUI/CLI 相同的修饰键清理兜底

    # 等 App 初始化（后台线程），然后把 MCP 主循环交给 FastMCP
    _get_app()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
