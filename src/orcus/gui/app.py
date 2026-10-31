"""GUI start-up (ARCHITECTURE.md §1.3, §4): single instance, the Session, bridge wiring, logoff fast stop.

`make_session` is called only after the mutex says we are the first instance; it builds app.Session (and the
process's Job object) in __main__, so this package never imports orcus.app (layering rule §2).
"""

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtGui import QSessionManager
from PySide6.QtWidgets import QApplication, QMessageBox

from orcus.common import log, win32
from orcus.contracts import SessionApi

from . import instance
from .bridge import Bridge
from .window import MainWindow

_log = log.get("gui")


def _log_uncaught() -> None:
    """The windowed build has no console (stderr is None): an error in a slot or a thread went nowhere (3a)."""
    def hook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        _log.error("uncaught %s", kind.__name__, exc_info=(kind, value, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            _log.error("uncaught in thread %s", args.thread.name if args.thread else "?",
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    sys.excepthook = hook
    threading.excepthook = thread_hook


def run(make_session: Callable[[], SessionApi], logs_dir: Path, files: list[str]) -> int:
    _log_uncaught()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Orcus APK Emulator")
    _mutex, existed = win32.named_mutex(instance.MUTEX)
    if existed:
        if instance.hand_over(files or []):
            return 0
        # a closing first instance has stopped listening: wait for it to end, then start in its place (3a review)
        win32.close_handle(_mutex)
        if instance.wait_until_gone(instance.MUTEX):
            _mutex, existed = win32.named_mutex(instance.MUTEX)
        if existed:
            QMessageBox.information(None, "Orcus APK Emulator", "Orcus가 이미 실행 중입니다.")
            return 0
    session = make_session()
    bridge = Bridge()
    session.engine.subscribe(bridge.on_engine)
    session.apps.subscribe(bridge.on_run)
    session.apps.subscribe_log(bridge.on_log)
    win = MainWindow(session, bridge, logs_dir)
    server = instance.InstanceServer(bridge.paths.emit)
    done = [False]

    def shutdown(fast: bool) -> None:
        if not done[0]:
            done[0] = True
            server.close()          # a second start waits for us instead of handing over a file we never open
            win.quiet_close = True  # logoff: no question about app data (Qt 6's quit() closes visible windows)
            win.hide()
            session.shutdown(fast=fast)

    def logoff(manager: QSessionManager) -> None:   # Windows logoff/shutdown: fast stop, never save (§1.3)
        shutdown(True)
        # quit even if the logoff is cancelled (an unsaved document elsewhere): a hidden process kept the mutex
        # and every later start handed its file to it (3a review)
        QTimer.singleShot(0, app.quit)
    app.commitDataRequest.connect(logoff)
    app.aboutToQuit.connect(lambda: shutdown(False))

    def bring_forward(_paths: list[str]) -> None:   # a file handed over by a second start: show where it runs
        if win.isMinimized():
            win.showNormal()
        win.raise_()
        win.activateWindow()
    bridge.paths.connect(bring_forward)
    win.show()
    session.engine.start()
    if files:
        session.open_paths([Path(f) for f in files])
    code = app.exec()
    del server
    return code
