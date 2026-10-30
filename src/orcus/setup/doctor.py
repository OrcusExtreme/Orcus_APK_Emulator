"""`orcus-cli doctor`: read-only health report. Changes nothing on the PC."""

import hashlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from orcus.common import accel, childenv, hostinfo, jsonio, messages_ko, pins, ports
from orcus.common.avd import config_ini_text, config_sha256
from orcus.common.paths import OrcusPaths, home_problems, interpreter_problems
from orcus.contracts import Settings

from . import extract, license

GIB = 1 << 30
RUN_FREE_GIB = 6


@dataclass(frozen=True)
class DoctorLine:
    name: str
    status: str   # "ok" | "warn" | "fail" | "info"
    detail: str


def collect(paths: OrcusPaths, settings: Settings, *,
            emulator_check: Callable[[], accel.AccelReport] | None = None) -> list[DoctorLine]:
    lines: list[DoctorLine] = []

    def add(name: str, status: str, detail: str) -> None:
        lines.append(DoctorLine(name, status, detail))

    py = interpreter_problems()
    add("python", "fail" if py else "ok", " ".join(messages_ko.text(p) for p in py) or sys.version.split()[0])
    home = home_problems(paths.root)
    add("home", "fail" if home else "ok",
        f"{paths.root} " + " ".join(messages_ko.text(p) for p in home))
    free = hostinfo.disk_free(paths.root)
    add("disk", "ok" if free >= RUN_FREE_GIB * GIB else "warn", f"{free / GIB:.1f} GiB free on {paths.root.anchor}")

    state = accel.query_install_state()
    add("whpx", "ok" if state is accel.WhpxState.ENABLED else "fail",
        f"InstallState={int(state) if state is not None else '?'} ({state.name if state else 'query failed'})")
    add("reboot_pending", "info", str(accel.reboot_pending()))
    add("elevation", "info", hostinfo.elevation())
    av = hostinfo.antivirus()
    add("antivirus", "info", f"V3={av.v3} EPP={av.epp_managed}")

    installed = True
    for archive in pins.ARCHIVES:
        v = extract.verify_installed(paths, archive)
        installed &= v.ok
        add(f"sdk.{archive.key}", "ok" if v.ok else "fail",
            f"{archive.revision} " + ("" if v.ok else f"missing={list(v.missing)} revision_ok={v.revision_ok}"))
    records = license.load_records(paths)
    add("licenses", "ok" if license.covers(records, [a.package_path for a in pins.ARCHIVES]) else "fail",
        ", ".join(f"{r.license_id} by {r.accepted_by} at {r.accepted_at}" for r in records) or "none")

    expected = config_ini_text(settings.engine)
    actual = config_sha256(paths)
    add("avd", "ok" if actual == hashlib.sha256(expected.encode()).hexdigest() else "fail",
        str(paths.avd_data) if actual else "not written")
    add("adbkey", "ok" if paths.adbkey.exists() else "fail", str(paths.adbkey))

    if installed and emulator_check is not None:
        report = emulator_check()
        add("accel", "ok" if report.usable else "fail", report.text.replace("\n", " | ")[:300])
    else:
        cached = (jsonio.read_json(paths.state_file("setup.json")) or {}).get("accel")
        add("accel", "info", f"cached: {cached.get('usable') if cached else None}")

    p = settings.engine.ports
    for name, port in (("console", p.console), ("adb", p.console + 1), ("grpc", p.grpc), ("adb_server", p.adb_server)):
        add(f"port.{name}", "ok" if ports.can_bind(port) else "warn", str(port))
    proxy = childenv.decide_proxy(os.environ)
    add("proxy", "warn" if proxy.warning_key else "info",
        f"{proxy.source} {proxy.http_proxy_flag or ''} {messages_ko.text(proxy.warning_key) if proxy.warning_key else ''}")
    return lines
