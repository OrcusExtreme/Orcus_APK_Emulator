"""Logcat over gRPC (ARCHITECTURE.md §7.8): Text mode only (Parsed mode sends nothing on 37.2.12, S5).

The fallback only: after LoadSnapshot ([Reset phone]) this stream sends nothing at all, even when the guest's
goldfish-logcat is restarted, so the app injects the engine's `adb logcat` as the
session's source. The stream opens with the whole buffer since boot (~1,500 lines even after `logcat -c`, S5);
lines older than the moment the stream was opened (by their own timestamp, §6.6) are dropped. Each message
carries one threadtime entry **without a newline** (9,667 messages in 6 s, no "\\n" anywhere), so messages are
never joined; a message holding several entries is split where the next timestamp starts.
"""

import threading
import time
from collections.abc import Iterator
from typing import Any

import grpc

from orcus.common import messages_ko
from orcus.common.logtext import THREADTIME, parse, split_entries
from orcus.contracts import ErrorCode, LogEntry, OrcusFailure
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

__all__ = ["THREADTIME", "LogcatStream", "parse", "split_entries"]
BACKLOG_MARGIN_S = 1.0


class LogcatStream:
    """CancellableIter[LogEntry]. Iterate on the consumer's thread; cancel() from any thread."""

    def __init__(self, stub: Any, *, since: float | None = None) -> None:
        self._since_us = int(((since if since is not None else time.time()) - BACKLOG_MARGIN_S) * 1_000_000)
        self._call = stub.streamLogcat(ec.LogMessage())
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self._call.cancel()

    def __iter__(self) -> Iterator[LogEntry]:
        try:
            for msg in self._call:
                for piece in split_entries(msg.contents):
                    entry = parse(piece)
                    if entry is not None and entry.ts_us >= self._since_us:
                        yield entry
        except grpc.RpcError as e:
            if self._cancelled.is_set() or e.code() == grpc.StatusCode.CANCELLED:
                return
            raise OrcusFailure(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE, f"logcat stream: {e.code()}")) from e
