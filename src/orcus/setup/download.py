"""Resumable downloads: HTTP Range into <name>.part, then size + SHA-1 verification.

Standard library only (urllib); it follows the Windows system proxy like a browser would.
"""

import hashlib
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

from orcus.common import log, messages_ko
from orcus.contracts import ErrorCode, OrcusFailure

CHUNK = 1 << 20
RETRIES = 5
_log = log.get("setup.download")


class Cancelled(Exception):
    pass


class Hasher(Protocol):
    def update(self, data: bytes, /) -> None: ...
    def hexdigest(self) -> str: ...


class Response(Protocol):
    status: int

    def read(self, n: int = -1) -> bytes: ...
    def __enter__(self) -> "Response": ...
    def __exit__(self, *exc: object) -> object: ...


type Opener = Callable[[urllib.request.Request], Response]
type Progress = Callable[[int, int], None]   # (bytes_done, bytes_total)


def default_opener(req: urllib.request.Request) -> Response:
    return urllib.request.urlopen(req, timeout=30)   # type: ignore[return-value]


def _sha1_of(path: Path, upto: int | None = None) -> Hasher:
    h = hashlib.sha1()
    remaining = upto
    with open(path, "rb") as f:
        while remaining is None or remaining > 0:
            block = f.read(CHUNK if remaining is None else min(CHUNK, remaining))
            if not block:
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    return h


def verified(path: Path, size: int, sha1: str) -> bool:
    try:
        return path.stat().st_size == size and _sha1_of(path).hexdigest() == sha1
    except FileNotFoundError:
        return False


def _wait_while_paused(paused: threading.Event | None, cancel: threading.Event | None) -> None:
    while paused is not None and paused.is_set():
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        time.sleep(0.2)


def _copy(resp: Response, out: BinaryIO, h: Hasher, done: int, size: int, progress: Progress | None,
          cancel: threading.Event | None, paused: threading.Event | None) -> int:
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        _wait_while_paused(paused, cancel)
        block = resp.read(CHUNK)
        if not block:
            return done
        out.write(block)
        h.update(block)
        done += len(block)
        if done > size:
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"more bytes than expected ({done} > {size})"))
        if progress is not None:
            progress(done, size)


def download(url: str, dest: Path, *, size: int, sha1: str, progress: Progress | None = None,
             cancel: threading.Event | None = None, paused: threading.Event | None = None,
             opener: Opener = default_opener, retries: int = RETRIES, backoff_s: float = 2.0) -> Path:
    """Download `url` to `dest`, resuming a previous .part. Returns `dest` (already verified)."""
    if verified(dest, size, sha1):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempt = 0
    while True:
        done = part.stat().st_size if part.exists() else 0
        if done > size:
            part.unlink()
            done = 0
        h = _sha1_of(part) if done else hashlib.sha1()
        if done < size:
            req = urllib.request.Request(url, headers={"Range": f"bytes={done}-"} if done else {})
            try:
                with opener(req) as resp:
                    if done and resp.status != 206:   # server ignored Range: start over
                        done, h = 0, hashlib.sha1()
                    with open(part, "ab" if done else "wb") as out:
                        done = _copy(resp, out, h, done, size, progress, cancel, paused)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 416:
                    raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_NETWORK, f"{url}: HTTP {e.code}")) from e
                attempt += 1
                _log.warning("download %s failed at %d bytes (attempt %d): %s", url, done, attempt, e)
                if attempt > retries:
                    raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_NETWORK, f"{url}: {e}")) from e
                time.sleep(backoff_s * attempt)
                continue
        if done < size:
            attempt += 1   # connection closed early; resume
            if attempt > retries:
                raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_NETWORK, f"{url}: stopped at {done}/{size}"))
            continue
        digest = h.hexdigest()
        if done != size or digest != sha1:
            part.unlink(missing_ok=True)
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM,
                                                 f"{dest.name}: size {done}/{size}, sha1 {digest} != {sha1}"))
        part.replace(dest)
        return dest
