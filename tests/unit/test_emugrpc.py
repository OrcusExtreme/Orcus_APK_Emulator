import hashlib
import json
from collections.abc import Iterator

import grpc
import pytest
from google.protobuf import empty_pb2

from conftest import SRC
from orcus.common import pins
from orcus.emugrpc import channel as ch
from orcus.emugrpc.fake import FakeEmulatorServer, png_bytes
from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

GEN = SRC / "orcus" / "emugrpc" / "generated"


@pytest.fixture
def fake() -> Iterator[FakeEmulatorServer]:
    with FakeEmulatorServer(token="secret", width=72, height=128, fps=200) as server:
        yield server


def test_status_with_token(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "secret")
    status = ch.controller(channel).getStatus(empty_pb2.Empty(), timeout=5)
    assert status.booted and status.vmConfig.hypervisorType == ec.VmConfiguration.WHPX
    assert {e.key: e.value for e in status.hardwareConfig.entry}["hw.lcd.width"] == "72"
    channel.close()


def test_wrong_token_is_rejected_for_unary_and_streams(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "nope")
    stub = ch.controller(channel)
    with pytest.raises(grpc.RpcError) as e:
        stub.getStatus(empty_pb2.Empty(), timeout=5)
    assert e.value.code() is grpc.StatusCode.UNAUTHENTICATED
    with pytest.raises(grpc.RpcError) as e2:
        next(iter(stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888), timeout=5)))
    assert e2.value.code() is grpc.StatusCode.UNAUTHENTICATED
    channel.close()


def test_frames_have_the_requested_size_and_cancel(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "secret")
    stream = ch.controller(channel).streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=36, height=64))
    frames = [next(stream) for _ in range(3)]
    stream.cancel()
    # the size is in Image.format; Image.width/height are 0 like the real emulator 37.2.12 (S2)
    assert all(f.format.width == 36 and f.format.height == 64 and len(f.image) == 36 * 64 * 3 for f in frames)
    assert all(f.width == 0 and f.height == 0 for f in frames)
    assert frames[0].seq < frames[1].seq < frames[2].seq
    assert frames[0].format.rotation.rotation == ec.Rotation.PORTRAIT
    channel.close()


def test_input_stream_is_recorded(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "secret")
    events = [ec.InputEvent(mouse_event=ec.MouseEvent(x=10, y=20, buttons=1)),
              ec.InputEvent(mouse_event=ec.MouseEvent(x=10, y=20, buttons=0)),
              ec.InputEvent(key_event=ec.KeyboardEvent(eventType=ec.KeyboardEvent.keypress, key="GoBack"))]
    ch.controller(channel).streamInputEvent(iter(events), timeout=5)
    assert [e.WhichOneof("type") for e in fake.input_events] == ["mouse_event", "mouse_event", "key_event"]
    channel.close()


def test_snapshot_rules(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "secret")
    snap = ch.snapshots(channel)
    pkg = ss.SnapshotPackage(snapshot_id="orcus_golden")
    assert snap.SaveSnapshot(pkg, timeout=5).success
    assert snap.SaveSnapshot(pkg, timeout=5).success   # 37.2.12 overwrites an existing id (S4)
    fake.script_snapshot("SaveSnapshot", ss.SnapshotPackage(snapshot_id="orcus_golden", success=False, err=b"full"))
    refused = snap.SaveSnapshot(pkg, timeout=5)
    assert not refused.success and refused.err == b"full"
    fake.script_snapshot("LoadSnapshot", ss.SnapshotPackage(snapshot_id="orcus_golden", success=False, err=b"boom"))
    assert not snap.LoadSnapshot(pkg, timeout=5).success
    assert snap.LoadSnapshot(pkg, timeout=5).success
    listed = snap.ListSnapshots(ss.SnapshotFilter(statusFilter=ss.SnapshotFilter.All), timeout=5)
    assert [(d.snapshot_id, d.status) for d in listed.snapshots] == [("orcus_golden", ss.SnapshotDetails.Loaded)]
    assert snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id="missing"), timeout=5).success   # always "success"
    channel.close()


def test_clipboard_gps_microphone(fake: FakeEmulatorServer) -> None:
    channel = ch.make_channel(fake.port, "secret")
    stub = ch.controller(channel)
    stub.setClipboard(ec.ClipData(text="안녕하세요 Orcus"), timeout=5)
    assert stub.getClipboard(empty_pb2.Empty(), timeout=5).text == "안녕하세요 Orcus"
    stub.setGps(ec.GpsState(latitude=37.5665, longitude=126.978, passiveUpdate=True), timeout=5)
    assert abs(stub.getGps(empty_pb2.Empty(), timeout=5).latitude - 37.5665) < 1e-6
    stub.setMicrophoneState(ec.MicrophoneState(realAudioEnabled=False), timeout=5)
    assert ("setMicrophoneState", False) in fake.calls
    channel.close()


def test_png_helper_is_a_png() -> None:
    data = png_bytes(4, 3, (1, 2, 3))
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data


def lf_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_manifest_matches_vendored_files() -> None:
    manifest = json.loads((GEN / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["emulator_build"] == pins.EMULATOR_BUILD
    protos = SRC / "orcus" / "emugrpc" / "protos" / pins.PROTO_DIR_NAME
    for name, digest in manifest["protos"].items():
        assert lf_sha256(protos / name) == digest, name
    for name, digest in manifest["generated"].items():
        assert lf_sha256(GEN / name) == digest, name
    assert set(manifest["descriptors"]) == {"emulator_controller_pb2", "snapshot_service_pb2", "snapshot_pb2"}


def test_selftest_passes_in_the_dev_venv() -> None:
    from orcus.engine import selftest
    report = selftest.run()
    assert report.ok, [line for line in report.lines if not line[1]]
    names = [name for name, _, _ in report.lines]
    assert "fake emulator round trip" in names and "version grpcio" in names and "descriptor snapshot_pb2" in names
