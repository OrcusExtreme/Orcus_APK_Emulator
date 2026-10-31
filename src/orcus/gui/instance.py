"""Single instance (ARCHITECTURE.md §1.3): the named mutex decides, a per-user QLocalServer hands files over.

A second start with a file (double-click on an .apk, a drop on the shortcut) connects for up to 3 s, sends the
paths and ends; the first instance opens them through SessionApi.open_paths.
"""

import os
import time
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from orcus.common import log, win32

MUTEX = "Local\\OrcusAPK.Instance"
HANDOVER_S = 3.0
MAX_MESSAGE = 64 * 1024
_log = log.get("gui.instance")


def acceptable(path: str) -> bool:
    """Only an existing local file: no network share (another account could hand one over and make us fetch it
    over SMB with our credentials), no relative path (3a review)."""
    if not path.strip() or path.startswith(("\\\\", "//")) or not os.path.isabs(path):
        return False
    return os.path.isfile(path)


def server_name() -> str:
    return f"OrcusAPK.ipc.{win32.user_sid() or 'user'}"


def hand_over(paths: list[str], timeout_s: float = HANDOVER_S) -> bool:
    """Second instance: deliver the paths to the first one. True when it acknowledged."""
    paths = [os.path.abspath(p) for p in paths]   # the first instance has another working folder (3a review)
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        sock = QLocalSocket()
        sock.connectToServer(server_name())
        if sock.waitForConnected(500):
            sock.write(("\n".join(paths) + "\n\n").encode("utf-8"))
            sock.waitForBytesWritten(1000)
            ok = sock.waitForReadyRead(2000) and bytes(sock.readAll().data()).startswith(b"ok")
            sock.disconnectFromServer()
            return ok
        time.sleep(0.2)
    return False


class InstanceServer(QObject):
    def __init__(self, on_paths: Callable[[list[str]], None]) -> None:
        super().__init__()
        self._on_paths = on_paths
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(server_name())   # a crashed first instance can leave the name behind
        if not self._server.listen(server_name()):
            _log.warning("instance server not listening: %s", self._server.errorString())
        self._server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        buf = bytearray()

        def read() -> None:
            buf.extend(bytes(sock.readAll().data()))
            if len(buf) > MAX_MESSAGE:   # a few paths, never megabytes (3a review)
                sock.abort()
                return
            if buf.endswith(b"\n\n"):
                lines = buf.decode("utf-8", errors="replace").split("\n")   # not splitlines(): U+2028 is a name
                buf.clear()
                sock.write(b"ok")
                sock.flush()
                self._on_paths([p for p in lines if acceptable(p)])
        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)
