"""[P4] The picture camera: the default color bars, the file replaced whole, the flag on every launch, and a golden
from before the picture camera no longer usable (it would not open with the flag, 4a)."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage

from orcus.common.paths import OrcusPaths
from orcus.contracts import GpuMode, SessionMode, Settings
from orcus.engine import args, camera, snapshots


def test_default_picture_is_color_bars_png() -> None:
    img = QImage.fromData(camera.default_png())
    assert not img.isNull() and (img.width(), img.height()) == (640, 480)
    red = img.pixelColor(640 * 5 // 7 + 10, 100)
    assert (red.red(), red.green(), red.blue()) == camera.BARS[5]                  # the red bar
    end = img.pixelColor(639, 470)
    assert (end.red(), end.green(), end.blue()) == (255, 255, 255)                 # end of the ramp


def test_put_replaces_the_picture_whole_and_ensure_writes_it_once(tmp_path: Path) -> None:
    paths = OrcusPaths(tmp_path)
    assert camera.ensure(paths) == paths.camera_image and paths.camera_image.read_bytes() == camera.default_png()
    camera.put(paths, b"\x89PNG mine")
    assert camera.ensure(paths).read_bytes() == b"\x89PNG mine"                   # not overwritten
    camera.put(paths, None)
    assert paths.camera_image.read_bytes() == camera.default_png()
    assert not list(paths.state.glob(".camera-*"))


def test_every_launch_gets_the_picture_camera() -> None:
    cfg = args.LaunchConfig(Path("emulator.exe"), 5640, 8640, camera_image=Path(r"D:\OrcusAPK\state\camera.png"))
    for mode in (args.LaunchMode.GOLDEN, args.LaunchMode.COLD, args.LaunchMode.PROVISION):
        argv = args.build(cfg, mode, GpuMode.HOST)
        assert argv[argv.index("-camera-back") + 1] == r"imagefile:D:\OrcusAPK\state\camera.png"


def test_a_golden_from_before_the_picture_camera_is_not_usable(tmp_path: Path) -> None:
    paths = OrcusPaths(tmp_path)
    paths.state.mkdir(parents=True)
    key = snapshots.current_key(paths, Settings())
    assert key.camera == args.CAMERA_MODE
    snapshots.write(paths, snapshots.Record(SessionMode.GOLDEN, "id1", key, {}))
    data = json.loads(paths.state_file(snapshots.RECORD).read_text(encoding="utf-8"))
    del data["key"]["camera"]                                  # as 4a's earlier goldens were written
    paths.state_file(snapshots.RECORD).write_text(json.dumps(data), encoding="utf-8")
    assert snapshots.read(paths) is None                       # so it is rebuilt once with the camera
