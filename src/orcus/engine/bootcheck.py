"""[P2] Boot check: prove this PC really runs the emulator (ARCHITECTURE.md §12.5, spikes S0b/S6).

`emulator-check accel` is not enough: it reported "WHPX usable" even before the post-dism reboot.
For each GPU mode this launches the AVD cold (no snapshot load or save), waits for its discovery
file and `getStatus` on the token channel, optionally for the boot to complete, records the SDK
processes and their listening sockets, shuts the VM down and checks that no SDK process is left.
"""

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import grpc
from google.protobuf import empty_pb2

from orcus.common import childenv, log, ports, proc, win32, winjob
from orcus.common.paths import OrcusPaths
from orcus.contracts import GpuMode, Settings
from orcus.emugrpc import channel as grpc_channel
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from . import args, discovery

_log = log.get("engine.bootcheck")


@dataclass
class BootReport:
    gpu: str
    ok: bool = False
    error: str | None = None
    argv: list[str] = field(default_factory=list)
    t_discovery_s: float | None = None
    t_status_s: float | None = None
    t_booted_s: float | None = None
    hypervisor: str | None = None
    emulator_version: str | None = None
    hardware: dict[str, str] = field(default_factory=dict)
    emulator_pid: int = 0
    discovery_file: str | None = None
    discovery_pid_image: str | None = None
    discovery_keys: list[str] = field(default_factory=list)
    sdk_processes: list[str] = field(default_factory=list)
    job_member_pids: list[int] = field(default_factory=list)
    listening: list[str] = field(default_factory=list)
    stop_s: float | None = None
    stop_method: str | None = None
    leftovers: list[str] = field(default_factory=list)
    status_hypervisor: str | None = None
    command_lines: list[str] = field(default_factory=list)
    leftover_command_lines: list[str] = field(default_factory=list)
    log_file: str | None = None
    log_tail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


ACCEL_LINES = {   # getStatus.vmConfig.hypervisorType stays UNKNOWN in 37.2.12: the log is the evidence
    "WHPX": "Windows Hypervisor Platform accelerator is operational",
    "AEHD": "AEHD is operational",
    "HAXM": "HAX is working and emulator runs in fast virt mode",
}


def accelerator_from_log(text: str) -> str | None:
    return next((name for name, line in ACCEL_LINES.items() if line in text), None)


def command_lines(root: Path) -> list[str]:
    """Command lines of processes whose image is under `root` (for S6: who starts what)."""
    # the path goes in through the environment, never into the script text: a home like D:\x';calc;' ran code (3a)
    script = ("$r = $env:ORCUS_SCAN_ROOT.TrimEnd('\\') + '\\'; Get-CimInstance Win32_Process | Where-Object { "
              "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($r, 'OrdinalIgnoreCase') } | "
              "ForEach-Object { \"$($_.ProcessId) parent=$($_.ParentProcessId) $($_.CommandLine)\" }")
    from orcus.common.accel import powershell_exe
    result = proc.run([powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script], timeout_s=30,
                      env={**os.environ, "ORCUS_SCAN_ROOT": str(root)})
    return [line.strip() for line in result.out.splitlines() if line.strip()]


def _listening(pids: set[int]) -> list[str]:
    """`netstat -ano` lines in LISTENING state owned by our SDK processes (bind addresses for S6)."""
    netstat = str(win32.system_dir() / "NETSTAT.EXE")   # by full path, never one in the current folder (3a review)
    result = proc.run([netstat, "-ano", "-p", "TCP"], timeout_s=15)
    result6 = proc.run([netstat, "-ano", "-p", "TCPv6"], timeout_s=15)
    out: list[str] = []
    for line in (result.out + "\n" + result6.out).splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[4].isdigit() and int(parts[4]) in pids:
            out.append(f"{parts[1]} pid={parts[4]}")
    return sorted(set(out))


def _stop(emulator: subprocess.Popen[bytes], stub: object | None, rec: discovery.DiscoveryRecord | None,
          paths: OrcusPaths, report: BootReport) -> None:
    """Pause, then end qemu, as the engine does (§5.1 STOPPING): qemu's own exit can fail fast in combase.dll and
    leave a crash report under C:\\ProgramData."""
    start = time.monotonic()
    method = "TerminateProcess"
    if stub is not None:
        try:
            stub.setVmState(ec.VmRunState(state=ec.VmRunState.PAUSED), timeout=5)   # type: ignore[attr-defined]
            method = "setVmState(PAUSED) -> TerminateProcess"
        except grpc.RpcError as e:
            _log.warning("PAUSED failed: %s", e)
    if emulator.poll() is None:
        for pid, image in winjob.processes_under(paths.sdk):   # qemu first, then emulator.exe
            qemu = pid == rec.pid if rec is not None else "qemu-system" in image.lower()
            if qemu:
                winjob.terminate_pid(pid)
        emulator.kill()
        emulator.wait(timeout=10)
    if rec is not None:   # the emulator removes its discovery file only when it exits by itself
        rec.source.unlink(missing_ok=True)
    report.stop_s = round(time.monotonic() - start, 2)
    report.stop_method = method


def boot_once(paths: OrcusPaths, settings: Settings, gpu: GpuMode, *, full_boot: bool,
              discovery_timeout_s: float = 60.0, boot_timeout_s: float = 300.0,
              job: winjob.Job | None = None, leftover_wait_s: float = 3.0) -> BootReport:
    # leftover_wait_s: after a stop only crashpad_handler (gone in ~0.1 s, 3a) or, after a graceful exit, the
    # `emulator -kill <qemu> -sleep 20` watchdog remain (S6); they are safe to end at once, so A5's N stays near 5 s
    report = BootReport(gpu=str(gpu))
    chosen = ports.choose(settings.engine.ports, ports.load_saved(paths))
    proxy = childenv.decide_proxy(os.environ)
    cfg = args.LaunchConfig(paths.emulator_exe, chosen.console, chosen.grpc, settings.engine.idle_grpc_timeout_s,
                            settings.engine.timezone, settings.engine.locale, proxy.http_proxy_flag)
    argv = args.build(cfg, args.LaunchMode.COLD, gpu)
    report.argv = argv
    env = childenv.base_env(paths, os.environ, adb_server_port=chosen.adb_server,
                            strip_dirs=childenv.app_dirs_to_strip())
    log_path = paths.logs / f"emulator-bootcheck-{gpu}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    report.log_file = str(log_path)
    dirs = discovery.candidate_dirs({"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", "")})   # not the shared <home>\tmp
    start = time.monotonic()
    emulator = proc.spawn(argv, env=env, log_path=log_path)
    report.emulator_pid = emulator.pid
    channel: grpc.Channel | None = None
    stub = None
    rec = None
    try:
        while time.monotonic() - start < discovery_timeout_s:
            if emulator.poll() is not None:
                raise RuntimeError(f"emulator exited with {emulator.returncode} before discovery")
            rec = discovery.find(dirs, serial_port=chosen.console, avd_dir=paths.avd_data, alive=winjob.pid_alive)
            if rec is not None and rec.grpc_token:
                break
            time.sleep(0.25)
        if rec is None or not rec.grpc_token or not rec.grpc_port:
            raise TimeoutError(f"no discovery file with a gRPC token within {discovery_timeout_s:.0f} s")
        report.t_discovery_s = round(time.monotonic() - start, 2)
        report.discovery_file = str(rec.source)
        report.discovery_pid_image = winjob.image_path(rec.pid)
        report.discovery_keys = sorted(re.findall(r"^([\w.]+)=", rec.source.read_text(encoding="utf-8", errors="replace"),
                                                  flags=re.MULTILINE))
        channel = grpc_channel.make_channel(rec.grpc_port, rec.grpc_token)
        stub = grpc_channel.controller(channel)
        status = None
        while time.monotonic() - start < discovery_timeout_s + 30:
            try:
                status = stub.getStatus(empty_pb2.Empty(), timeout=5)
                break
            except grpc.RpcError as e:
                _log.info("getStatus not ready: %s", e.code())
                time.sleep(0.5)
        if status is None:
            raise TimeoutError("getStatus never answered")
        report.t_status_s = round(time.monotonic() - start, 2)
        report.status_hypervisor = ec.VmConfiguration.VmHypervisorType.Name(status.vmConfig.hypervisorType)
        report.hypervisor = accelerator_from_log(log_path.read_text(encoding="utf-8", errors="replace"))
        if report.hypervisor is None and report.status_hypervisor != "UNKNOWN":
            report.hypervisor = report.status_hypervisor
        report.emulator_version = status.version
        report.hardware = {e.key: e.value for e in status.hardwareConfig.entry
                           if e.key.startswith(("hw.lcd", "hw.gpu", "hw.ramSize", "hw.cpu"))}
        if full_boot:
            while not status.booted:
                if time.monotonic() - start > boot_timeout_s:
                    raise TimeoutError(f"not booted within {boot_timeout_s:.0f} s")
                if emulator.poll() is not None:
                    raise RuntimeError(f"emulator exited with {emulator.returncode} while booting")
                time.sleep(1.0)
                status = stub.getStatus(empty_pb2.Empty(), timeout=5)
            report.t_booted_s = round(time.monotonic() - start, 2)
        running = winjob.processes_under(paths.sdk)
        report.sdk_processes = sorted(f"{Path(image).name} pid={pid}" for pid, image in running)
        report.command_lines = command_lines(paths.sdk)
        if job is not None:
            report.job_member_pids = job.process_ids()
        report.listening = _listening({pid for pid, _ in running})
        report.ok = report.hypervisor == "WHPX"
        if not report.ok:
            report.error = f"hypervisor is {report.hypervisor}, expected WHPX"
    except Exception as e:   # the report carries the failure; the caller decides
        report.error = f"{type(e).__name__}: {e}"
        report.ok = False
    finally:
        _stop(emulator, stub, rec, paths, report)
        if channel is not None:
            channel.close()
        deadline = time.monotonic() + leftover_wait_s
        left = winjob.processes_under(paths.sdk)
        while left and time.monotonic() < deadline:
            time.sleep(0.5)
            left = winjob.processes_under(paths.sdk)
        report.leftovers = sorted(f"{Path(image).name} pid={pid}" for pid, image in left)
        if left:
            report.leftover_command_lines = command_lines(paths.sdk)
        for pid, image in left:   # A5: nothing of ours may survive a stop (never another adb, 3a review)
            if Path(image).name.lower() in discovery.EMULATOR_IMAGES:
                winjob.terminate_pid(pid)
        report.log_tail = _tail(log_path)
    return report


def boot_check(paths: OrcusPaths, settings: Settings, *, gpus: tuple[GpuMode, ...] = (GpuMode.HOST, GpuMode.HOST_HIDDEN),
               full_boot: bool = False, job: winjob.Job | None = None,
               on_report: Callable[[BootReport], None] | None = None) -> list[BootReport]:
    """Holds the engine lock for the whole check; another Orcus using the VM makes it fail fast."""
    lock = winjob.ExclusiveLock(paths.state_file("engine.lock"))
    try:
        lock.acquire()
    except winjob.LockBusy:
        return [BootReport(gpu=str(g), error="ENGINE_IN_USE: another Orcus holds the phone") for g in gpus]
    try:
        reports: list[BootReport] = []
        for gpu in gpus:
            r = boot_once(paths, settings, gpu, full_boot=full_boot, job=job)
            _log.info("boot check %s: %s", gpu, r.to_dict())
            reports.append(r)
            if on_report is not None:
                on_report(r)
        return reports
    finally:
        lock.release()
