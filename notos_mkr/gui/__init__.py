"""notos-mkr 的 Windows 优先 PySide6 胶囊 GUI 入口。"""
from __future__ import annotations

import atexit
import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _set_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _app_data_dir() -> str:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "notos-mkr")
    return os.path.join(os.path.expanduser("~"), ".notos-mkr")


def _setup_logging(app_dir: str) -> None:
    try:
        os.makedirs(app_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(app_dir, "notos-mkr.log"),
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except Exception:
        pass

    def _hook(exc_type, exc, tb):
        logging.getLogger("notos-mkr").error("unhandled exception",
                                       exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _fatal_message(text: str) -> None:
    """启动失败时给一个原生消息框；无控制台/无 Qt 时也能看到原因。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "notos-mkr", 0x10)
        return
    except Exception:
        pass
    try:
        print(text, file=sys.stderr)
    except Exception:
        pass


def _acquire_single_instance(app_dir: str, log: logging.Logger):
    """返回 (kind, object, handle)。kind: 'mutex' | 'lock' | 'denied'."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p
        ]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        name = "Local\\notos-mkr-gui-" + (os.environ.get("USERNAME") or "user")
        handle = kernel32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if not handle:
            log.warning("CreateMutex failed, continuing without single-instance")
            return "mutex", None, None
        if err == 183:  # ERROR_ALREADY_EXISTS
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return "denied", None, None
        return "mutex", None, handle

    try:
        from PySide6.QtCore import QLockFile
    except Exception:
        return "lock", None, None
    lock = QLockFile(os.path.join(app_dir, "notos-mkr.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(120):
        return "denied", lock, None
    return "lock", lock, None


def run_gui() -> int:
    """启动胶囊 GUI，返回进程退出码。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    _set_windows_dpi_awareness()

    # 在导入 PySide6 之前就把日志目录建好：这样即使打包缺失 PySide6，
    # 也能在日志里看到原因，而不是“双击后什么都没发生”。
    app_dir = _app_data_dir()
    _setup_logging(app_dir)
    log = logging.getLogger("notos-mkr.gui")
    log.info("starting notos-mkr gui on %s", sys.platform)

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith("PySide6"):
            msg = (
                "notos-mkr 缺少 GUI 依赖 PySide6。\n\n"
                "请在项目目录运行:\n"
                "  .venv\\Scripts\\pip install PySide6-Essentials\n\n"
                f"日志: {os.path.join(app_dir, 'notos-mkr.log')}\n"
                "也可以直接使用 run_gui.cmd（不经过 exe，不会触发 SmartScreen）。"
            )
            log.exception("missing PySide6")
            _fatal_message(msg)
            return 1
        raise

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        qapp = QApplication(sys.argv)
    except Exception as e:
        log.exception("QApplication init failed")
        _fatal_message(
            "notos-mkr 启动失败：Qt 初始化失败。\n"
            f"{e!r}\n\n日志: {os.path.join(app_dir, 'notos-mkr.log')}\n"
            "请查看日志排查。"
        )
        return 1
    qapp.setApplicationName("notos-mkr")
    qapp.setOrganizationName("notos-mkr")
    qapp.setApplicationDisplayName("notos-mkr")
    qapp.setQuitOnLastWindowClosed(True)

    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        elevated = False
    log.info("elevated=%s", elevated)
    log.info("keyboard backend=%s",
             "GetAsyncKeyState polling" if sys.platform == "win32" else "pynput")
    log.info("app_dir=%s", app_dir)

    kind, lock, mutex_handle = _acquire_single_instance(app_dir, log)
    if kind == "denied":
        log.warning("another notos-mkr instance is already running")
        _fatal_message(
            "notos-mkr 已经在运行。\n\n"
            "如果看不到胶囊，请检查屏幕边缘，或先在任务管理器结束旧的 notos-mkr/pythonw 进程。\n"
            f"日志: {os.path.join(app_dir, 'notos-mkr.log')}"
        )
        return 0

    try:
        try:
            from ..recorder import release_modifiers
        except Exception as e:
            log.exception("missing runtime dependency")
            _fatal_message(
                f"notos-mkr 启动失败：缺少运行依赖。\n{e!r}\n\n"
                f"日志: {os.path.join(app_dir, 'notos-mkr.log')}\n"
                "请先运行: .venv\\Scripts\\pip install -e ."
            )
            return 1

        atexit.register(release_modifiers)

        app = None
        window = None
        controller = None
        try:
            from ..app import App
            from .controller import GuiController
            from .window import CapsuleWindow

            app = App()
            window = CapsuleWindow()
            controller = GuiController(app, window)
            qapp.aboutToQuit.connect(controller.cleanup)

            window.show()
            window.raise_()
            app.start_hotkeys()

            return int(qapp.exec())
        except Exception as e:
            log.exception("gui crashed")
            _fatal_message(
                "notos-mkr 启动失败。\n"
                f"{e!r}\n\n日志: {os.path.join(app_dir, 'notos-mkr.log')}\n"
                "请查看日志排查。"
            )
            return 1
        finally:
            if controller is not None:
                try:
                    controller.cleanup()
                except Exception:
                    pass
            elif app is not None:
                try:
                    app.shutdown()
                except Exception:
                    pass
            if window is not None:
                try:
                    window.close()
                except Exception:
                    pass
            try:
                release_modifiers()
            except Exception:
                pass
    finally:
        if mutex_handle:
            try:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.CloseHandle.argtypes = [ctypes.c_void_p]
                k32.CloseHandle.restype = ctypes.c_bool
                k32.CloseHandle(mutex_handle)
            except Exception:
                pass
        if lock is not None:
            try:
                lock.unlock()
            except Exception:
                pass
