"""[P2] `--selftest`: built-in checks for the packaged build (ARCHITECTURE.md §12.5). No pytest.

Guards against the dev/packaged runtime mismatch seen in an earlier project (OpenCV 4.14 vs 5.0):
runtime imports, installed versions against requirements.lock, the gRPC stubs against
MANIFEST.json, and a FakeEmulatorServer round trip over the real channel code.
"""

import hashlib
import importlib
import importlib.metadata
import importlib.resources
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RUNTIME_MODULES = ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "grpc", "google.protobuf", "apkInspector")


@dataclass
class SelfTestReport:
    lines: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.lines)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.lines.append((name, ok, detail))


def lock_file() -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    return base / "requirements.lock"


def pinned_versions(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def _check_imports(report: SelfTestReport) -> None:
    for name in RUNTIME_MODULES:
        try:
            importlib.import_module(name)
            report.add(f"import {name}", True)
        except Exception as e:   # report, do not stop
            report.add(f"import {name}", False, f"{type(e).__name__}: {e}")


def _check_versions(report: SelfTestReport) -> None:
    path = lock_file()
    try:
        pins = pinned_versions(path)
    except OSError as e:
        report.add("requirements.lock", False, f"{path}: {e}")
        return
    for dist, wanted in sorted(pins.items()):
        try:
            got = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            report.add(f"version {dist}", False, f"not installed (lock {wanted})")
            continue
        report.add(f"version {dist}", got == wanted, f"{got} (lock {wanted})")


def _check_stubs(report: SelfTestReport) -> None:
    from orcus.common import pins
    try:
        text = importlib.resources.files("orcus.emugrpc.generated").joinpath("MANIFEST.json").read_text(encoding="utf-8")
        manifest = json.loads(text)
    except (OSError, ValueError) as e:
        report.add("stub manifest", False, str(e))
        return
    report.add("stub build", manifest.get("emulator_build") == pins.EMULATOR_BUILD,
               f"{manifest.get('emulator_build')} (pin {pins.EMULATOR_BUILD})")
    for module, digest in sorted(manifest.get("descriptors", {}).items()):
        mod = importlib.import_module(f"orcus.emugrpc.generated.{module}")
        actual = hashlib.sha256(mod.DESCRIPTOR.serialized_pb).hexdigest()
        report.add(f"descriptor {module}", actual == digest, actual[:12])


def _check_fake_roundtrip(report: SelfTestReport) -> None:
    import grpc
    from google.protobuf import empty_pb2

    from orcus.emugrpc import channel as grpc_channel
    from orcus.emugrpc.fake import FakeEmulatorServer
    from orcus.emugrpc.generated import emulator_controller_pb2 as ec
    from orcus.emugrpc.generated import snapshot_service_pb2 as ss

    try:
        with FakeEmulatorServer(token="selftest", width=36, height=64) as fake:
            good = grpc_channel.make_channel(fake.port, "selftest")
            stub = grpc_channel.controller(good)
            status = stub.getStatus(empty_pb2.Empty(), timeout=5)
            stream = stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=36, height=64), timeout=5)
            frame = next(iter(stream))
            stream.cancel()
            stub.setClipboard(ec.ClipData(text="안녕"), timeout=5)
            clip = stub.getClipboard(empty_pb2.Empty(), timeout=5).text
            snaps = grpc_channel.snapshots(good)
            first = snaps.SaveSnapshot(ss.SnapshotPackage(snapshot_id="selftest"), timeout=5).success
            listed = [d.snapshot_id for d in snaps.ListSnapshots(ss.SnapshotFilter(), timeout=5).snapshots]
            snaps.DeleteSnapshot(ss.SnapshotPackage(snapshot_id="selftest"), timeout=5)
            second = "selftest" in [d.snapshot_id for d in snaps.ListSnapshots(ss.SnapshotFilter(), timeout=5).snapshots]
            first = first and listed == ["selftest"]
            bad = grpc_channel.make_channel(fake.port, "wrong")
            try:
                grpc_channel.controller(bad).getStatus(empty_pb2.Empty(), timeout=5)
                rejected = False
            except grpc.RpcError as e:
                rejected = e.code() is grpc.StatusCode.UNAUTHENTICATED
            good.close()
            bad.close()
        ok = (status.booted and len(frame.image) == 36 * 64 * 3 and clip == "안녕" and first and not second and rejected)
        report.add("fake emulator round trip", ok,
                   f"booted={status.booted} frame={len(frame.image)}B clip={clip!r} save+list={first} "
                   f"after_delete={second} wrong_token_rejected={rejected}")
    except Exception as e:
        report.add("fake emulator round trip", False, f"{type(e).__name__}: {e}")


def run() -> SelfTestReport:
    report = SelfTestReport()
    report.add("python", sys.version_info[:2] == (3, 14), sys.version.split()[0])
    _check_imports(report)
    _check_versions(report)
    _check_stubs(report)
    _check_fake_roundtrip(report)
    return report
