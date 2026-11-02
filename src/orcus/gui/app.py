"""GUI start-up (ARCHITECTURE.md §1.3, §4): single instance, the Session, bridge wiring, logoff fast stop.

`make_session` is called only after the mutex says we are the first instance; it builds app.Session (and the
process's Job object) in __main__, so this package never imports orcus.app (layering rule §2). On a PC
without the phone's files the setup wizard comes first, alone; the window opens once it got to the end.
"""

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QLibraryInfo, QTimer, QTranslator
from PySide6.QtGui import QIcon, QSessionManager
from PySide6.QtWidgets import QApplication, QMessageBox

from orcus.common import i18n, log, win32
from orcus.common.i18n import t
from orcus.contracts import EngineState, SessionApi

from . import instance
from .bridge import Bridge
from .window import MainWindow

_log = log.get("gui")


def _log_uncaught() -> None:
    """The windowed build has no console (stderr is None): an error in a slot or a thread would go nowhere."""
    def hook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        _log.error("uncaught %s", kind.__name__, exc_info=(kind, value, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            _log.error("uncaught in thread %s", args.thread.name if args.thread else "?",
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    sys.excepthook = hook
    threading.excepthook = thread_hook


def app_icon() -> QIcon:
    '''Load the application icon from embedded resources or project assets.'''
    for loc in (
        Path(__file__).resolve().parent.parent / 'resources' / 'icon.ico',
        Path(__file__).resolve().parent.parent / 'resources' / 'icon.png',
        Path(__file__).resolve().parents[3] / 'assets' / 'icon.ico',
        Path(__file__).resolve().parents[3] / 'assets' / 'icon.png',
    ):
        if loc.is_file():
            return QIcon(str(loc))
    return QIcon()


def qt_words_file() -> Path | None:
    """qtbase_ko.qm where Qt looks for it (the packaged build keeps this one Qt translation; --selftest)."""
    path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_ko.qm"
    return path if path.is_file() else None


def _qt_words(app: QApplication) -> None:
    """Qt's own buttons (Yes, No, OK, Cancel) in Korean too."""
    if i18n.language() != "ko":
        return
    path = qt_words_file()
    words = QTranslator(app)
    if path is not None and words.load(str(path)):
        app.installTranslator(words)
    else:
        _log.info("qtbase_ko.qm not found: Qt's own buttons stay in English")


def run(make_session: Callable[[], SessionApi], logs_dir: Path, files: list[str], *,
        setup_resume: bool = False) -> int:
    _log_uncaught()
    win32.set_app_user_model_id("OrcusExtreme.OrcusAPK.Emulator")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Orcus APK Emulator")
    _qt_words(app)
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    _mutex, existed = win32.named_mutex(instance.MUTEX)
    if existed:
        if instance.hand_over(files or []):
            return 0
        # a closing first instance has stopped listening: wait for it to end, then start in its place
        win32.close_handle(_mutex)
        if instance.wait_until_gone(instance.MUTEX):
            _mutex, existed = win32.named_mutex(instance.MUTEX)
        if existed:
            QMessageBox.information(None, "Orcus APK Emulator", t("Orcus가 이미 실행 중입니다."))
            return 0
    session = make_session()
    bridge = Bridge()
    session.engine.subscribe(bridge.on_engine)
    session.apps.subscribe(bridge.on_run)
    session.apps.subscribe_log(bridge.on_log)
    session.apps.subscribe_vibration(bridge.on_vibrated)
    session.apps.subscribe_media(bridge.on_media)
    session.apps.subscribe_apps(bridge.on_apps)
    win = MainWindow(session, bridge, logs_dir)
    if not icon.isNull():
        win.setWindowIcon(icon)
    held: list[list[str]] | None = [] if session.engine.status().state == EngineState.NOT_INSTALLED else None

    def handed_over(paths: list[str]) -> None:   # during the first setup, files wait for the window
        if held is not None:
            held.append(paths)
            win.raise_setup()
        else:
            bridge.paths.emit(paths)
    server = instance.InstanceServer(handed_over)
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
        # quit even if the logoff is cancelled (an unsaved document elsewhere): a hidden process would keep the
        # mutex and every later start would hand its file to it
        QTimer.singleShot(0, app.quit)
    app.commitDataRequest.connect(logoff)
    app.aboutToQuit.connect(lambda: shutdown(False))

    def bring_forward(_paths: list[str]) -> None:   # a file handed over by a second start: show where it runs
        if win.isMinimized():
            win.showNormal()
        win.raise_()
        win.activateWindow()
    bridge.paths.connect(bring_forward)
    if held is not None:   # a PC without the phone's files: the wizard first (§10.5); closed, nothing else opens
        if not win.run_setup(first=True, resume=setup_resume):
            shutdown(False)
            return 0
        files = [*files, *(p for paths in held for p in paths)]
        held = None
    win.show()
    session.engine.start()
    if files:
        session.open_paths([Path(f) for f in files])
    code = app.exec()
    del server
    return code
