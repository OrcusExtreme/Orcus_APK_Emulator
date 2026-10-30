"""AVD files are byte-identical to the spec blocks in ARCHITECTURE.md §11.2; pins are consistent."""

import re
from pathlib import Path

from conftest import ARCHITECTURE, SRC
from orcus.common import avd, pins
from orcus.common.paths import OrcusPaths
from orcus.contracts import EngineSettings


def spec_block(after: str) -> str:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    start = text.index(after, text.index("### 11.2 AVD 파일"))
    fence = text.index("```\n", start) + 4
    return text[fence: text.index("```", fence)]


def test_avd_ini_matches_spec() -> None:
    assert avd.avd_ini_text(OrcusPaths(Path(r"D:\OrcusAPK"))) == spec_block("`avd\\orcus36.ini`")


def test_config_ini_matches_spec() -> None:
    assert avd.config_ini_text(EngineSettings()) == spec_block("`avd\\orcus36.avd\\config.ini`")


def test_write_avd_is_idempotent(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    first = avd.write_avd(p, EngineSettings())
    second = avd.write_avd(p, EngineSettings())
    assert first.changed and not second.changed
    assert first.config_sha256 == second.config_sha256 == avd.config_sha256(p)
    assert (p.avd_data / "config.ini").read_bytes() == avd.config_ini_text(EngineSettings()).encode()
    third = avd.write_avd(p, EngineSettings(ram_mb=3072))
    assert third.changed and third.config_sha256 != first.config_sha256


def test_avd_name_rule() -> None:
    assert avd.avd_name_is_valid("orcus36")
    assert not avd.avd_name_is_valid("orcus 36") and not avd.avd_name_is_valid("오르카")


def test_pins_are_consistent() -> None:
    for a in pins.ARCHIVES:
        assert a.url.startswith(pins.SDK_REPO) and a.url.endswith(".zip")
        assert re.fullmatch(r"[0-9a-f]{40}", a.sha1), a.key
        assert a.size > 1_000_000
        assert a.license_id == "android-sdk-license"
    assert pins.EMULATOR.filename == f"emulator-windows_x64-{pins.EMULATOR_BUILD}.zip"
    assert pins.revision_tuple("7") == pins.revision_tuple("7.0.0") != pins.revision_tuple("7.0.1")


def test_proto_folder_matches_pinned_build() -> None:
    protos = SRC / "orcus" / "emugrpc" / "protos"
    if protos.exists():
        assert [d.name for d in protos.iterdir() if d.is_dir()] == [pins.PROTO_DIR_NAME]
