"""GUI start-up (ARCHITECTURE.md §1.3, §4): single instance, the Session, bridge wiring, logoff fast stop.

`make_session` is called only after the mutex says we are the first instance; it builds app.Session (and the
process's Job object) in __main__, so this package never imports orcus.app (layering rule §2).
"""

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QSessionManager
from PySide6.QtWidgets import QApplication, QMessageBox

from orcus.common import win32
from orcus.contracts import SessionApi

from . import instance
from .bridge import Bridge
from .window import MainWindow


def run(make_session: Callable[[], SessionApi], logs_dir: Path, files: list[str]) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Orcus APK Emulator")
    _mutex, existed = win32.named_mutex(instance.MUTEX)
    if existed:
        if instance.hand_over(files or []):
            return 0
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
            win.hide()
            session.shutdown(fast=fast)

    def logoff(manager: QSessionManager) -> None:   # Windows logoff/shutdown: fast stop, never save (§1.3)
        shutdown(True)
    app.commitDataRequest.connect(logoff)
    app.aboutToQuit.connect(lambda: shutdown(False))
    win.show()
    session.engine.start()
    if files:
        session.open_paths([Path(f) for f in files])
    code = app.exec()
    del server
    return code
