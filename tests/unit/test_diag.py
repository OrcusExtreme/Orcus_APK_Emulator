"""[P4] The diagnostic bundle: what goes in, the cut of a big log, the masking, the pruning; the CLI command."""

import os
import time
import zipfile
from pathlib import Path

import pytest

from orcus import cli
from orcus.app import diag
from orcus.common.paths import OrcusPaths

ENV = {"USERNAME": "student7", "COMPUTERNAME": "LAB-PC-07"}   # made up: no real names in the repository


def home(tmp_path: Path) -> OrcusPaths:
    p = OrcusPaths(tmp_path / "OrcusAPK")
    p.logs.mkdir(parents=True)
    p.state.mkdir(parents=True)
    t = time.time() - 1000
    for i in range(35):
        f = p.logs / f"orcus-{i:02}.log"
        f.write_bytes(f"line {i}: C:\\Users\\student7\\x on LAB-PC-07 appinventor.ai_kim_lab.HelloPurr\n".encode())
        os.utime(f, (t + i, t + i))
    for i in range(7):
        f = p.logs / f"emulator-2026103{i}-golden.log"
        f.write_text("emulator\n", encoding="utf-8")
        os.utime(f, (t + i, t + i))
    (p.state / "settings.json").write_text('{"apk": {"grant_all": true}}', encoding="utf-8")
    (p.state / "engine.lock").write_text("locked", encoding="utf-8")
    p.avd_data.mkdir(parents=True)
    (p.avd_data / "config.ini").write_text("hw.ramSize=2560\n", encoding="utf-8")
    return p


def test_the_bundle_has_the_newest_logs_and_the_state_with_names_masked(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    p = home(tmp_path)
    monkeypatch.setattr(diag, "MAX_FILE_BYTES", 120)                   # the other logs are 77 bytes
    (p.logs / "orcus-34.log").write_bytes(b"x" * 200 + b"\nthe end\n")   # the newest, too big
    out = diag.make_bundle(p, "보고서 kim@school.example appinventor.ai_kim_lab.App", ["ok   whpx  enabled"],
                           mask=diag.masker(ENV))
    assert out.parent == p.root / "diag" and not list(out.parent.glob("*.part"))
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        text = {n: z.read(n).decode("utf-8") for n in names}
    logs = {n for n in names if n.startswith("logs/orcus-")}
    assert len(logs) == diag.ORCUS_LOGS and "logs/orcus-00.log" not in logs   # the oldest stay out
    assert len([n for n in names if n.startswith("logs/emulator-")]) == diag.EMULATOR_LOGS
    assert {"report.txt", "doctor.txt", "state/settings.json", "avd/config.ini"} <= names
    assert "state/engine.lock" not in names
    assert text["report.txt"] == "보고서 <email> appinventor.ai_<account>.App"
    assert text["logs/orcus-33.log"] == "line 33: C:\\Users\\<user>\\x on <pc> appinventor.ai_<account>.HelloPurr\n"
    assert text["logs/orcus-34.log"].startswith("[처음") and text["logs/orcus-34.log"].endswith("the end\n")


def test_old_bundles_are_pruned(tmp_path: Path) -> None:
    p = home(tmp_path)
    out_dir = p.root / "diag"
    out_dir.mkdir(parents=True)
    for i in range(7):
        old = out_dir / f"orcus-diag-2026010{i}-000000.zip"
        old.write_bytes(b"PK")
        os.utime(old, (1000 + i, 1000 + i))
    out = diag.make_bundle(p, "", [], mask=diag.masker(ENV))
    left = sorted(f.name for f in out_dir.glob("orcus-diag-*.zip"))
    assert len(left) == diag.KEEP and out.name in left and "orcus-diag-20260100-000000.zip" not in left


def test_cli_diag_writes_a_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "OrcusAPK"
    home(tmp_path)
    assert cli.main(["diag", "--home", str(root)]) == 0
    printed = capsys.readouterr().out
    assert "진단 묶음:" in printed and len(list((root / "diag").glob("orcus-diag-*.zip"))) == 1
