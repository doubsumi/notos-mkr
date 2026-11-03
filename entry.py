"""独立入口脚本 —— 供 PyInstaller 打包使用（绝对 import，无父包依赖）。"""
import sys
from pathlib import Path

# PyInstaller onefile 解压目录（frozen 模式下 sys.frozen=True）
if getattr(sys, "frozen", None) is not None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        sys.path.insert(0, str(Path(meipass)))
    else:
        sys.path.insert(0, str(Path(sys.executable).parent))

# ---- P0 修复：Windows 上强制 Per-Monitor V2 DPI ----
# pynput 钩子回调和 SendInput API 内部都用物理像素坐标，
# 但 DPI Unaware 进程在 >100% 缩放下会被系统虚拟化 DPI，
# 导致录制/回放的坐标偏移。尽早声明让整个进程生命周期
# 都使用真实物理像素。
import platform as _plt
if _plt.system() == "Windows":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from mkr.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
