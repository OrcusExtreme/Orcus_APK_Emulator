"""The setup step model end to end with fakes: license wait, UAC, downloads, reboot gate, resume, A1."""

import json
import queue
from pathlib import Path
from typing import Any

from orcus.common import accel, pins
from orcus.common.paths import OrcusPaths
from orcus.contracts import CmdResult, ErrorCode, Settings, SetupApi, SetupEvent, SetupStep
from orcus.setup import extract, license
from orcus.setup.repo_index import RemoteArchive, RemotePackage, RepoIndex
from orcus.setup.steps import SetupDeps, SetupService
from orcus.setup.whpx import DismOutcome

GIB = 1 << 30
LICENSE_TEXT = "Android SDK License Agreement (test copy)"
PT, EMU, SI = pins.PLATFORM_TOOLS, pins.EMULATOR, pins.SYSTEM_IMAGE
REPO = RepoIndex("repo", (
    RemotePackage(PT.package_path, PT.revision, "android-sdk-license", "stable",
                  (RemoteArchive("windows", PT.url, PT.size, PT.sha1),)),
), {"android-sdk-license": LICENSE_TEXT})
SYSIMG = RepoIndex("sysimg", (
    RemotePackage(SI.package_path, SI.revision, "android-sdk-license", "stable",
                  (RemoteArchive(None, SI.url, SI.size, SI.sha1),)),
), {"android-sdk-license": LICENSE_TEXT})


class World:
    """A fake PC: WHPX state, boots, downloads, installs."""

    def __init__(self) -> None:
        self.installed: set[str] = set()
        self.whpx = accel.WhpxState.DISABLED
        self.boot = 1_000_000.0
        self.reboot_pending = False
        self.dism = DismOutcome(True, None, 3010, False, 42.0)
        self.accel_usable = True
        self.free = 100 * GIB
        self.calls: dict[str, int] = {}
        self.downloads: list[str] = []
        self.resume_registered = False

    def count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def deps(self, p: OrcusPaths) -> SetupDeps:
        def run_dism() -> DismOutcome:
            self.count("dism")
            return self.dism

        def download(url: str, dest: Path, *, size: int, sha1: str, **kw: Any) -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"zip")
            kw["progress"](size, size)
            self.downloads.append(url)
            return dest

        def install(paths: OrcusPaths, archive: pins.Archive, zip_path: Path, **kw: Any) -> extract.Verification:
            assert zip_path.exists()
            self.installed.add(archive.key)
            kw["progress"](1, 1)
            return extract.Verification((), (), {"source.properties": "source.properties"}, True)

        def verify(paths: OrcusPaths, archive: pins.Archive) -> extract.Verification:
            ok = archive.key in self.installed
            return extract.Verification(() if ok else ("source.properties",), (), {}, ok)

        def check() -> accel.AccelReport:
            self.count("emulator_check")
            text = "WHPX(10.0.26100) is installed and usable." if self.accel_usable else "WHPX is not usable."
            return accel.AccelReport(self.accel_usable, "WHPX", 0 if self.accel_usable else 1, text)

        def keygen() -> CmdResult:
            self.count("keygen")
            p.adbkey.parent.mkdir(parents=True, exist_ok=True)
            p.adbkey.write_text("private")
            p.adbkey.with_suffix(".pub").write_text("public")
            return CmdResult(("adb", "keygen"), 0, "", "", 0.1, False)

        def register() -> None:
            self.resume_registered = True

        def unregister() -> None:
            self.resume_registered = False

        return SetupDeps(
            paths=p, settings=Settings(),
            fetch_repo_index=lambda: (self.count("index"), REPO)[1],
            fetch_sysimg_index=lambda: SYSIMG,
            query_whpx=lambda: self.whpx, reboot_pending=lambda: self.reboot_pending, boot_time=lambda: self.boot,
            run_dism=run_dism, download=download, install_archive=install, verify_installed=verify,
            emulator_check=check, adb_keygen=keygen, ensure_job=lambda: self.count("job"),
            disk_free=lambda: self.free, register_resume=register, unregister_resume=unregister,
            check_interpreter=False, check_home=lambda root: [])


class Recorder:
    def __init__(self, api: SetupService) -> None:
        self.q: "queue.Queue[SetupEvent]" = queue.Queue()
        api.subscribe(self.q.put)

    def until(self, step: SetupStep, state: str, timeout: float = 15) -> SetupEvent:
        while True:
            ev = self.q.get(timeout=timeout)
            assert not (ev.state == "failed" and (step, state) != (ev.step, "failed")), ev
            if ev.step is step and ev.state == state:
                return ev


def start(world: World, p: OrcusPaths) -> tuple[SetupService, Recorder]:
    api = SetupService(world.deps(p))
    rec = Recorder(api)
    api.run()
    return api, rec


def test_protocol_conformance(tmp_path: Path) -> None:
    _: SetupApi = SetupService(World().deps(OrcusPaths(tmp_path)))


def test_fresh_install_across_a_reboot_then_nothing(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    leftover = p.tmp / "extract_emulator" / "emulator"   # what the old extract-then-rename code left behind
    leftover.mkdir(parents=True)
    (leftover / "qemu.dll").write_bytes(b"MZ")
    api, rec = start(w, p)
    rec.until(SetupStep.LICENSE, "waiting_user")
    lt = api.license()
    assert lt is not None and lt.text == LICENSE_TEXT
    assert set(lt.packages) == {PT.package_path, EMU.package_path, SI.package_path}
    api.accept_license("not-the-shown-text", "tester")      # ignored: hash mismatch
    api.accept_license(lt.sha256, "tester")
    rec.until(SetupStep.LICENSE, "done")
    rec.until(SetupStep.WHPX, "waiting_user")
    api.enable_whpx()
    rec.until(SetupStep.WHPX, "done")
    rec.until(SetupStep.DOWNLOAD, "done")
    rec.until(SetupStep.EXTRACT, "done")
    assert not (p.tmp / "extract_emulator").exists()
    assert not any(p.downloads.glob("*.zip")), "zips are deleted after a verified install"
    rec.until(SetupStep.REBOOT, "waiting_user")
    assert api.join(5) and w.resume_registered
    assert w.calls == {"index": 1, "dism": 1} and len(w.downloads) == 3
    assert EMU.url in w.downloads   # pinned URL, not in the index any more
    assert license.load_records(p)[0].accepted_by == "tester"
    assert p.platforms.is_dir()

    w.boot += 3600   # Windows restarted
    w.whpx = accel.WhpxState.ENABLED
    api2, rec2 = start(w, p)
    rec2.until(SetupStep.DONE, "done")
    assert not w.resume_registered
    assert w.calls == {"index": 1, "dism": 1, "job": 1, "emulator_check": 1, "keygen": 1}
    assert (p.avd_data / "config.ini").exists() and p.adbkey.exists()
    states = {e.step: e.state for e in api2.events()}
    assert states[SetupStep.PROVISION] == "pending"
    assert all(s == "done" for step, s in states.items() if step is not SetupStep.PROVISION)

    api3, rec3 = start(w, p)   # A1: the second complete run changes nothing
    rec3.until(SetupStep.DONE, "done")
    assert w.calls == {"index": 1, "dism": 1, "job": 1, "emulator_check": 1, "keygen": 1}
    saved = json.loads(p.state_file("setup.json").read_text(encoding="utf-8"))
    assert saved["dism"]["exit_code"] == 3010 and saved["accel"]["usable"] is True
    assert saved["resolved"][1] == {"key": "emulator", "url": EMU.url, "in_index": False, "license": "android-sdk-license"}


def test_uac_refusal_can_continue_with_downloads(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    w.dism = DismOutcome(False, 1223, None, None, 3.0)
    w.accel_usable = False
    api, rec = start(w, p)
    rec.until(SetupStep.LICENSE, "waiting_user")
    lt = api.license()
    assert lt is not None
    api.accept_license(lt.sha256, "tester")
    rec.until(SetupStep.WHPX, "waiting_user")
    api.enable_whpx()
    failed = rec.until(SetupStep.WHPX, "failed")
    assert failed.problem is not None and failed.problem.code is ErrorCode.SETUP_UAC_DENIED
    api.resume()   # [Download anyway]
    rec.until(SetupStep.EXTRACT, "done")
    rec.until(SetupStep.REBOOT, "done")
    accel_failed = rec.until(SetupStep.ACCEL, "failed")
    assert accel_failed.problem is not None and accel_failed.problem.code is ErrorCode.SETUP_ACCEL_FAILED
    assert api.join(5)


def preinstalled(w: World, p: OrcusPaths) -> None:
    w.installed = {a.key for a in pins.ARCHIVES}
    license.record_acceptance(p, license.LicenseText("android-sdk-license", LICENSE_TEXT, "x",
                                                     tuple(a.package_path for a in pins.ARCHIVES)), "tester")


def test_accel_failure_with_reboot_pending(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    preinstalled(w, p)
    w.whpx = accel.WhpxState.ENABLED
    w.accel_usable = False
    w.reboot_pending = True
    api, rec = start(w, p)
    ev = rec.until(SetupStep.ACCEL, "failed")
    assert ev.problem is not None and ev.problem.code is ErrorCode.SETUP_REBOOT_PENDING
    assert "index" not in w.calls   # nothing to download: no network needed


def test_whpx_reverted_after_reboot(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    preinstalled(w, p)
    p.state.mkdir(parents=True, exist_ok=True)
    p.state_file("setup.json").write_text(json.dumps(
        {"version": 1, "dism": {"ok": True, "reboot_needed": True, "boot_time": w.boot - 7200}}), encoding="utf-8")
    api, rec = start(w, p)
    ev = rec.until(SetupStep.WHPX, "failed")
    assert ev.problem is not None and ev.problem.code is ErrorCode.SETUP_WHPX_REVERTED
    assert api.join(5)
    api2, rec2 = start(w, p)   # the next run offers UAC again
    rec2.until(SetupStep.WHPX, "waiting_user")
    api2.cancel()
    assert api2.join(5)


def test_accel_failure_after_enable_and_reboot_is_reverted(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    preinstalled(w, p)
    w.whpx = accel.WhpxState.ENABLED
    w.accel_usable = False
    p.state.mkdir(parents=True, exist_ok=True)
    p.state_file("setup.json").write_text(json.dumps(
        {"version": 1, "dism": {"ok": True, "reboot_needed": True, "boot_time": w.boot - 7200}}), encoding="utf-8")
    api, rec = start(w, p)
    ev = rec.until(SetupStep.ACCEL, "failed")
    assert ev.problem is not None and ev.problem.code is ErrorCode.SETUP_WHPX_REVERTED


def test_low_disk_stops_before_downloads(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    w.free = 10 * GIB
    api, rec = start(w, p)
    ev = rec.until(SetupStep.CHECK, "failed")
    assert ev.problem is not None and ev.problem.code is ErrorCode.SETUP_DISK
    assert api.join(5) and not w.downloads


def test_changed_license_asks_again(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    license.record_acceptance(p, license.LicenseText("android-sdk-license", "old text", "old-sha", ("emulator",)), "t")
    api, rec = start(w, p)
    ev = rec.until(SetupStep.LICENSE, "waiting_user")
    assert ev.problem is not None and ev.problem.code is ErrorCode.SETUP_LICENSE_CHANGED
    api.cancel()
    assert api.join(5)


def test_cancel_while_waiting_for_license(tmp_path: Path) -> None:
    w, p = World(), OrcusPaths(tmp_path)
    api, rec = start(w, p)
    rec.until(SetupStep.LICENSE, "waiting_user")
    api.cancel()
    assert api.join(5)
    assert license.load_records(p) == []
