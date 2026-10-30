"""[P2] Minimal composition: wire real dependencies into the setup service and doctor.

The CLI and (later) the GUI only build things through here, so they never import setup/engine
directly (layering rule in ARCHITECTURE.md §2).
"""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from orcus.common import accel, childenv, config, hostinfo, log, paths, proc, win32, winjob
from orcus.common.paths import OrcusPaths
from orcus.common.pins import REPO_INDEX_URLS, SYSIMG_INDEX_URLS
from orcus.contracts import CmdResult, GpuMode, Settings
from orcus.engine import bootcheck, readonly
from orcus.engine import selftest as selftest_mod
from orcus.setup import doctor as doctor_mod
from orcus.setup import download, extract, repo_index, whpx
from orcus.setup.steps import SetupDeps, SetupService

_job: winjob.Job | None = None


def exe_dir() -> Path | None:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def make_paths(cli_home: str | None = None) -> OrcusPaths:
    return OrcusPaths(paths.resolve_home(cli_home, os.environ, exe_dir()))


def load_settings(p: OrcusPaths, warnings: list[str] | None = None) -> Settings:
    return config.load(p, warnings=warnings)


def start_logging(p: OrcusPaths, name: str) -> Path:
    """File log under <home>\\logs; warnings no longer leak to the console."""
    return log.setup_file_logging(p.logs, name)


def emulator_check_exe(p: OrcusPaths) -> Path:
    """emulator-check.exe at the expected place, else wherever setup found it (layout is confirmed in S6)."""
    if p.emulator_check_exe.exists():
        return p.emulator_check_exe
    return next(p.emulator_dir.rglob("emulator-check.exe"), p.emulator_check_exe)


def prepare_process() -> None:
    """Call once at startup, before the first child: loader hygiene for the packaged build (§5.4)."""
    if getattr(sys, "frozen", False):
        win32.reset_dll_directory()


def ensure_job() -> None:
    """KILL_ON_JOB_CLOSE job for this process, created once and kept open until exit (§1.2)."""
    global _job
    if _job is None:
        job = winjob.Job.create(kill_on_close=True)
        job.assign_current()
        _job = job


def child_env(p: OrcusPaths, settings: Settings) -> dict[str, str]:
    return childenv.base_env(p, os.environ, adb_server_port=settings.engine.ports.adb_server,
                             strip_dirs=childenv.app_dirs_to_strip())


def resume_command(p: OrcusPaths) -> str:
    """Command line RunOnce uses to continue setup after the reboot."""
    home = [] if p.root == paths.DEFAULT_HOME else ["--home", str(p.root)]
    if getattr(sys, "frozen", False):
        argv = [sys.executable, "setup", "--resume", *home]
    else:
        argv = [sys.executable, "-X", "utf8", "-m", "orcus.cli", "setup", "--resume", *home]
    return subprocess.list2cmdline(argv)


def build_setup(p: OrcusPaths, settings: Settings) -> SetupService:
    def keygen() -> CmdResult:
        p.android_home.mkdir(parents=True, exist_ok=True)
        return proc.run([str(p.adb_exe), "keygen", str(p.adbkey)], timeout_s=30, env=child_env(p, settings))

    deps = SetupDeps(
        paths=p,
        settings=settings,
        fetch_repo_index=lambda: repo_index.fetch_first(REPO_INDEX_URLS),
        fetch_sysimg_index=lambda: repo_index.fetch_first(SYSIMG_INDEX_URLS),
        query_whpx=accel.query_install_state,
        reboot_pending=accel.reboot_pending,
        boot_time=win32.boot_time,
        run_dism=whpx.run_dism,
        download=download.download,
        install_archive=extract.install_archive,
        verify_installed=extract.verify_installed,
        emulator_check=lambda: accel.run_emulator_check(str(emulator_check_exe(p)), child_env(p, settings)),
        adb_keygen=keygen,
        ensure_job=ensure_job,
        disk_free=lambda: hostinfo.disk_free(p.root),
        register_resume=lambda: whpx.register_runonce(resume_command(p)),
        unregister_resume=whpx.unregister_runonce,
        check_interpreter=not getattr(sys, "frozen", False),
    )
    return SetupService(deps)


def boot_check(p: OrcusPaths, settings: Settings, *, gpus: tuple[GpuMode, ...] = (GpuMode.HOST, GpuMode.HOST_HIDDEN),
               full_boot: bool = False,
               on_report: Callable[[bootcheck.BootReport], None] | None = None) -> list[bootcheck.BootReport]:
    """Real cold boots inside our KILL_ON_JOB_CLOSE job (`--boot-check`)."""
    ensure_job()
    return bootcheck.boot_check(p, settings, gpus=gpus, full_boot=full_boot, job=_job, on_report=on_report)


def attach_readonly(p: OrcusPaths) -> readonly.ReadOnlyVm | None:
    """`orcus-cli engine status|shot`: the phone another Orcus owns, read-only (§1.3). No engine of our own."""
    return readonly.attach(p)


def selftest() -> selftest_mod.SelfTestReport:
    """`--selftest`: imports, versions against the lock, stub descriptors, FakeEmulatorServer round trip."""
    return selftest_mod.run()


def doctor(p: OrcusPaths, settings: Settings) -> list[doctor_mod.DoctorLine]:
    exe = emulator_check_exe(p)

    def check() -> accel.AccelReport:
        ensure_job()
        return accel.run_emulator_check(str(exe), child_env(p, settings))
    return doctor_mod.collect(p, settings, emulator_check=check if exe.exists() else None)
