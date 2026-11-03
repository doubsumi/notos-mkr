from __future__ import annotations

import argparse
import atexit
import sys
import threading
import time

from .app import App
from .recorder import release_modifiers
from . import storage


def _safe_release_modifiers():
    try:
        release_modifiers()
    except Exception:
        pass


def _require_pynput():
    try:
        import pynput  # noqa: F401
    except ImportError:
        print("缺少依赖: pynput。请运行: pip install pynput", file=sys.stderr)
        sys.exit(1)


def _check_linux_wayland():
    import platform, os
    if platform.system() == "Linux" and os.environ.get("WAYLAND_DISPLAY") \
            and not os.environ.get("DISPLAY"):
        print("[警告] 检测到纯 Wayland 会话，pynput 仅支持 X11。"
              "请切换到 X11 会话或启用 XWayland。", file=sys.stderr)


# ---------- record ----------
def cmd_record(args):
    _require_pynput()
    _check_linux_wayland()

    app = App()
    done = threading.Event()

    def on_ev(name, **payload):
        if name == "record_started":
            print("● 录制中… 连按 3 次 Ctrl 停止", flush=True)
        elif name == "record_stopped":
            evs = payload.get("events", [])
            print(f"✓ 录制完成，共 {len(evs)} 条事件", flush=True)
            if evs:
                storage.save(evs, args.output)
                print(f"  已保存到: {args.output}", flush=True)
        elif name == "quit":
            done.set()

    app.on(on_ev)
    app.start_hotkeys()

    print("快捷键:")
    print("  连按 3 次 Ctrl  开始 / 停止录制")
    print("  Ctrl+Alt+P  回放 (需已录到内存)")
    print("  Ctrl+Alt+S  停止当前录制 / 回放")
    print("  Ctrl+Alt+Q  退出")

    if args.immediate:
        app.start_record()
    else:
        print("等待连按 3 次 Ctrl 开始录制 …")

    try:
        while not done.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C")
    finally:
        app.shutdown()


# ---------- replay ----------
def cmd_replay(args):
    _require_pynput()
    from .recorder import Replayer

    events = storage.load(args.file)
    if not events:
        print("录制文件为空")
        return
    print(f"已加载 {len(events)} 条事件，时长 {events[-1]['t']:.2f}s")

    if not args.no_countdown:
        for i in (3, 2, 1):
            print(f"{i}…", flush=True)
            time.sleep(1)

    rp = Replayer()
    print(f"开始回放 (speed={args.speed}, loops={args.loops})，Ctrl+C 停止")
    rp.play(events, speed=args.speed, loops=args.loops)
    try:
        while rp.is_playing:
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        rp.stop()
        release_modifiers()
    print("回放结束")


# ---------- gui ----------
def cmd_gui(_args):
    from .gui import run_gui
    return run_gui()


# ---------- main ----------
def main(argv=None):
    # 兜底：无论从哪个节点退出（异常、Ctrl+C、正常结束），
    # 解释器结束前都主动释放所有修饰键，避免系统输入映射错乱
    atexit.register(_safe_release_modifiers)

    p = argparse.ArgumentParser(prog="mkr", description="鼠标键盘录制回放工具")
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="毛玻璃胶囊 GUI (默认)")
    g.set_defaults(func=cmd_gui)

    r = sub.add_parser("record", help="通过热键录制并保存为 JSON")
    r.add_argument("-o", "--output", default="recording.json")
    r.add_argument("--immediate", action="store_true",
                   help="启动后立即开始录制，无需按热键")
    r.set_defaults(func=cmd_record)

    pl = sub.add_parser("replay", help="回放 JSON 录制文件")
    pl.add_argument("file")
    pl.add_argument("--speed", type=float, default=1.0, help="倍速 (默认 1.0)")
    pl.add_argument("--loops", type=int, default=1, help="循环次数")
    pl.add_argument("--no-countdown", action="store_true", help="跳过 3 秒倒计时")
    pl.set_defaults(func=cmd_replay)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        return cmd_gui(args)
    return args.func(args)