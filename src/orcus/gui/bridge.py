"""Callbacks from producer threads -> Qt signals (ARCHITECTURE.md §3 callback rules, §4).

Engine, APK pipeline, logcat and frame threads call these methods; each only emits a signal, which Qt queues
to the main thread because the receivers live there. Nothing here touches a widget.
"""

from PySide6.QtCore import QObject, Signal

from orcus.contracts import EngineStatus, LogEntry, OrcusError, RunEvent


class Bridge(QObject):
    engine = Signal(object)        # EngineStatus
    run = Signal(object)           # RunEvent
    log = Signal(object)           # LogEntry
    frame = Signal()               # a new frame waits in the FramePump mailbox
    frames_ended = Signal(object)  # OrcusError | None
    shot = Signal(object)          # bytes | None (screenshot PNG)
    paths = Signal(list)           # files handed over by a second instance
    vibrated = Signal(int)         # ms, -1 while it runs
    media = Signal(object)         # MediaResult
    apps = Signal(object)          # tuple[InstalledApp, ...]
    diag = Signal(object)          # Path of the diagnostic bundle, or the exception

    def on_engine(self, st: EngineStatus) -> None:
        self.engine.emit(st)

    def on_run(self, ev: RunEvent) -> None:
        self.run.emit(ev)

    def on_log(self, entry: LogEntry) -> None:
        self.log.emit(entry)

    def on_frames_ended(self, err: OrcusError | None) -> None:
        self.frames_ended.emit(err)

    def on_shot(self, png: bytes | None) -> None:
        self.shot.emit(png)

    def on_vibrated(self, ms: int) -> None:
        self.vibrated.emit(ms)

    def on_media(self, result: object) -> None:
        self.media.emit(result)

    def on_apps(self, apps: object) -> None:
        self.apps.emit(apps)

    def on_diag(self, result: object) -> None:
        self.diag.emit(result)
