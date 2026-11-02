"""Resumable download against a local HTTP server with Range support."""

import hashlib
import http.server
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from orcus.contracts import ErrorCode, OrcusFailure
from orcus.setup import download

DATA = bytes(range(256)) * 4096   # 1 MiB
SHA1 = hashlib.sha1(DATA).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "ranges"          # "ranges" | "ignore_range" | "drop_once" | "drop_every" | "416_once"
    dropped = False
    requests: list[str | None] = []

    def log_message(self, format: str, *args: object) -> None:   # noqa: A002 (base class name)
        pass

    def do_GET(self) -> None:
        rng = self.headers.get("Range")
        Handler.requests.append(rng)
        if Handler.mode == "416_once" and rng and not Handler.dropped:   # the .part no longer fits the file
            Handler.dropped = True
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start = 0
        if rng and Handler.mode != "ignore_range":
            start = int(rng.removeprefix("bytes=").split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        body = DATA[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if Handler.mode == "drop_once" and not Handler.dropped:
            Handler.dropped = True
            self.wfile.write(body[: len(body) // 3])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        if Handler.mode == "drop_every" and len(body) > 200_000:   # Wi-Fi that keeps dropping, with progress
            self.wfile.write(body[:200_000])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(body)


@pytest.fixture
def server() -> Iterator[str]:
    Handler.mode, Handler.dropped, Handler.requests = "ranges", False, []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/file.zip"
    httpd.shutdown()


def test_full_download(server: str, tmp_path: Path) -> None:
    seen: list[int] = []
    dest = download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1,
                             progress=lambda d, t: seen.append(d))
    assert dest.read_bytes() == DATA and seen[-1] == len(DATA)
    assert not (tmp_path / "f.zip.part").exists()


def test_resume_from_part(server: str, tmp_path: Path) -> None:
    (tmp_path / "f.zip.part").write_bytes(DATA[:300_000])
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1)
    assert (tmp_path / "f.zip").read_bytes() == DATA
    assert Handler.requests == ["bytes=300000-"]


def test_server_ignoring_range_restarts(server: str, tmp_path: Path) -> None:
    Handler.mode = "ignore_range"
    (tmp_path / "f.zip.part").write_bytes(DATA[:300_000])
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1)
    assert (tmp_path / "f.zip").read_bytes() == DATA


def test_dropped_connection_resumes(server: str, tmp_path: Path) -> None:
    Handler.mode = "drop_once"
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1, backoff_s=0.01)
    assert (tmp_path / "f.zip").read_bytes() == DATA
    assert len(Handler.requests) == 2 and Handler.requests[1] is not None


def test_drops_with_progress_keep_the_retry_budget(server: str, tmp_path: Path) -> None:
    """Six drops over a long download must not end in SETUP_NETWORK while each attempt gets further."""
    Handler.mode = "drop_every"
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1, retries=2, backoff_s=0.01)
    assert (tmp_path / "f.zip").read_bytes() == DATA and len(Handler.requests) > 3


def test_416_drops_the_part_and_starts_over(server: str, tmp_path: Path) -> None:
    Handler.mode = "416_once"
    (tmp_path / "f.zip.part").write_bytes(DATA[:300_000])
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1, backoff_s=0.01)
    assert (tmp_path / "f.zip").read_bytes() == DATA
    assert Handler.requests == ["bytes=300000-", None]


def test_checksum_mismatch_removes_part(server: str, tmp_path: Path) -> None:
    with pytest.raises(OrcusFailure) as e:
        download.download(server, tmp_path / "f.zip", size=len(DATA), sha1="0" * 40)
    assert e.value.error.code is ErrorCode.SETUP_CHECKSUM
    assert not (tmp_path / "f.zip.part").exists() and not (tmp_path / "f.zip").exists()


def test_size_mismatch(server: str, tmp_path: Path) -> None:
    with pytest.raises(OrcusFailure) as e:
        download.download(server, tmp_path / "f.zip", size=len(DATA) - 10, sha1=SHA1)
    assert e.value.error.code is ErrorCode.SETUP_CHECKSUM


def test_verified_file_is_not_downloaded_again(server: str, tmp_path: Path) -> None:
    (tmp_path / "f.zip").write_bytes(DATA)
    download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1)
    assert Handler.requests == []


def test_unreachable_server_is_setup_network(tmp_path: Path) -> None:
    attempts: list[str] = []

    def refused(req: urllib.request.Request) -> download.Response:
        attempts.append(req.full_url)
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
    with pytest.raises(OrcusFailure) as e:
        download.download("http://127.0.0.1:9/x.zip", tmp_path / "x.zip", size=10, sha1="0" * 40,
                          opener=refused, retries=2, backoff_s=0.01)
    assert e.value.error.code is ErrorCode.SETUP_NETWORK and len(attempts) == 3


def test_cancel_keeps_part(server: str, tmp_path: Path) -> None:
    cancel = threading.Event()

    def progress(done: int, total: int) -> None:
        if done > 0:
            cancel.set()
    with pytest.raises(download.Cancelled):
        download.download(server, tmp_path / "f.zip", size=len(DATA), sha1=SHA1, progress=progress, cancel=cancel)
    assert (tmp_path / "f.zip.part").exists()
