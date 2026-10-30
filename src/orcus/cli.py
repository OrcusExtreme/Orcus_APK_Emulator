"""orcus-cli: headless commands.

    orcus-cli setup  [--home DIR] [--resume]   install steps 1-8 (the license needs a human at the console)
    orcus-cli doctor [--home DIR]              read-only health report

Exit codes: 0 done, 1 failed, 2 waiting for a Windows restart, 3 stopped by the user.
"""

import argparse
import json
import queue
import sys
import time

from orcus.app import compose
from orcus.common import hostinfo, messages_ko, win32
from orcus.common.messages_ko import text
from orcus.common.pins import EMULATOR, EMULATOR_BUILD
from orcus.contracts import ErrorCode, GpuMode, RemedyAction, SetupEvent, SetupStep

T = messages_ko
STEPS = [s for s in SetupStep if s not in (SetupStep.PROVISION, SetupStep.DONE)]


def _out(line: str = "", end: str = "\n") -> None:
    print(line, end=end, flush=True)


def _ask(prompt: str) -> str | None:
    if not win32.stdin_is_console():
        return None
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def _label(step: SetupStep) -> str:
    n = STEPS.index(step) + 1 if step in STEPS else len(STEPS)
    return f"[{n}/{len(STEPS)}] {text(f'setup.step.{step}')}"


def _fmt_mb(n: int) -> str:
    return f"{n / 1_000_000:,.0f} MB"


class Renderer:
    def __init__(self) -> None:
        self.console = sys.stdout.isatty()
        self.last: dict[SetupStep, str] = {}
        self.last_pct = -1
        self.inline = False

    def end_inline(self) -> None:
        if self.inline:
            _out()
            self.inline = False

    def show(self, ev: SetupEvent) -> None:
        if ev.state == "running" and ev.bytes_total:
            pct = int(100 * ev.bytes_done / ev.bytes_total)
            rate = f"  {ev.rate_bps / 1_000_000:.1f} MB/s" if ev.rate_bps else ""
            eta = f"  남은 시간 약 {int(ev.eta_s // 60)}분 {int(ev.eta_s % 60)}초" if ev.eta_s else ""
            msg = f"    {pct:3d}%  {_fmt_mb(ev.bytes_done)} / {_fmt_mb(ev.bytes_total)}{rate}{eta}"
            if self.console:
                _out("\r" + msg.ljust(78), end="")
                self.inline = True
            elif pct // 10 != self.last_pct // 10:
                _out(msg)
            self.last_pct = pct
            return
        if self.last.get(ev.step) == ev.state:
            return
        self.last[ev.step] = ev.state
        self.end_inline()
        word = {"running": "...", "done": "완료", "waiting_user": "사용자 확인 필요", "failed": "실패"}.get(ev.state)
        if word and ev.step is not SetupStep.DONE:
            _out(f"{_label(ev.step)} {word}")
        if ev.problem is not None and ev.state != "failed":
            _out(f"    알림: {text(ev.problem.message_key)}")


def _show_failure(ev: SetupEvent) -> None:
    err = ev.problem
    if err is None:
        return
    _out(f"    {text(err.message_key)}")
    if err.detail:
        _out(f"    ({err.code}: {err.detail})")
    if err.remedy is not None and err.remedy.action is RemedyAction.COPY_IT_REQUEST:
        if err.code is ErrorCode.SETUP_QUARANTINE:
            request = T.it_request(T.IT_REQUEST_V3, err.detail, build=f"{EMULATOR.revision} ({EMULATOR_BUILD})")
        else:
            request = T.it_request(T.IT_REQUEST_WHPX, err.detail)
        _out("\n----- IT 요청문 (복사해서 보내세요) -----")
        _out(request)
        _out("------------------------------------------")


def _handle_license(api: compose.SetupService) -> bool:
    lt = api.license()
    if lt is None:
        return True
    if not win32.stdin_is_console():
        _out(text("setup.license.console_only"))
        return False
    _out("\n" + text("setup.license.intro"))
    _out(text("setup.license.packages", packages=", ".join(lt.packages)))
    _out("=" * 78)
    _out(lt.text)
    _out("=" * 78)
    answer = _ask(text("setup.license.prompt"))
    if answer not in ("동의", "동의합니다"):
        _out(text("setup.license.declined"))
        return False
    api.accept_license(lt.sha256, T.current_user())
    return True


def _handle_whpx(api: compose.SetupService) -> bool:
    elevation = hostinfo.elevation()
    if elevation == "default":
        _out("    " + text("setup.check.admin_default"))
        _out(T.it_request(T.IT_REQUEST_WHPX, "아직 시도 전"))
    elif elevation == "limited":
        _out("    " + text("setup.check.admin_limited"))
    answer = _ask(text("setup.whpx.prompt"))
    if answer is None or answer.lower() in ("n", "no", "아니오"):
        return False
    api.enable_whpx()
    _out("    " + text("setup.whpx.running"))
    return True


def cmd_setup(args: argparse.Namespace) -> int:
    p = compose.make_paths(args.home)
    warnings: list[str] = []
    settings = compose.load_settings(p, warnings)
    log_path = compose.start_logging(p, "setup")
    _out(text("setup.title") + f"  ({p.root})")
    _out(f"    로그: {log_path}")
    for w in warnings:
        _out(f"    설정 무시: {w}")
    av = hostinfo.antivirus()
    if av.epp_managed:
        _out("    " + text("setup.check.v3_epp"))
    api = compose.build_setup(p, settings)
    events: "queue.Queue[SetupEvent]" = queue.Queue()
    api.subscribe(events.put)
    renderer = Renderer()
    api.run()
    try:
        while True:
            try:
                ev = events.get(timeout=1.0)
            except queue.Empty:
                if api.join(0):
                    return 1   # worker ended without a final event (cancelled)
                continue
            renderer.show(ev)
            if ev.state == "waiting_user":
                renderer.end_inline()
                if ev.step is SetupStep.LICENSE and not _handle_license(api):
                    api.cancel()
                    return 3
                if ev.step is SetupStep.WHPX and not _handle_whpx(api):
                    api.cancel()
                    return 3
                if ev.step is SetupStep.REBOOT:
                    _out("    " + text("setup.reboot.needed"))
                    return 2
            elif ev.state == "failed":
                renderer.end_inline()
                _show_failure(ev)
                if ev.step is SetupStep.WHPX and (_ask(text("setup.whpx.continue_prompt")) or "") in ("예", "y", "yes"):
                    api.resume()
                    continue
                return 1
            elif ev.step is SetupStep.DONE and ev.state == "done":
                _out(text("setup.done"))
                _out(text("setup.provision.later"))
                return 0
    except KeyboardInterrupt:
        api.cancel()
        return 3
    finally:
        if args.resume and win32.stdin_is_console():
            _ask("\nEnter를 누르면 창을 닫습니다.")


def cmd_doctor(args: argparse.Namespace) -> int:
    p = compose.make_paths(args.home)
    settings = compose.load_settings(p)
    compose.start_logging(p, "doctor")
    lines = compose.doctor(p, settings)
    for line in lines:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "    "}[line.status]
        _out(f"{mark} {line.name:<20} {line.detail}")
    failed = any(line.status == "fail" for line in lines)
    if args.boot_check:
        gpus = tuple(GpuMode(g) for g in args.gpu) if args.gpu else (GpuMode.HOST, GpuMode.HOST_HIDDEN)
        _out(f"\n부팅 확인: {', '.join(gpus)} ({'부팅 완료까지' if args.full else 'getStatus까지'})")

        def show(r: object) -> None:
            d = r.to_dict()   # type: ignore[attr-defined]
            mark = "OK  " if d["ok"] else "FAIL"
            _out(f"{mark} boot.{d['gpu']:<15} hypervisor={d['hypervisor']} discovery={d['t_discovery_s']}s "
                 f"status={d['t_status_s']}s booted={d['t_booted_s']}s stop={d['stop_s']}s ({d['stop_method']})")
            if d["error"]:
                _out(f"     error: {d['error']}")
            _out(f"     processes: {', '.join(d['sdk_processes'])}")
            for line in d["command_lines"]:
                _out(f"       {line[:200]}")
            _out(f"     listening: {', '.join(d['listening'])}")
            _out(f"     leftovers after stop: {d['leftovers'] or 'none'}")
            for line in d["leftover_command_lines"]:
                _out(f"       {line[:200]}")
            _out(f"     log: {d['log_file']}")
        reports = compose.boot_check(p, settings, gpus=gpus, full_boot=args.full, on_report=show)
        failed = failed or not all(r.ok for r in reports)
        out = p.logs / f"bootcheck-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2), encoding="utf-8")
        _out(f"     report: {out}")
    return 1 if failed else 0


def cmd_selftest(args: argparse.Namespace) -> int:
    report = compose.selftest()
    for name, ok, detail in report.lines:
        _out(f"{'OK  ' if ok else 'FAIL'} {name:<32} {detail}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    compose.prepare_process()
    parser = argparse.ArgumentParser(prog="orcus-cli", description="Orcus APK Emulator (headless)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="install steps 1-8")
    s.add_argument("--home", help=r"runtime folder (default D:\OrcusAPK)")
    s.add_argument("--resume", action="store_true", help="started by RunOnce after the reboot")
    d = sub.add_parser("doctor", help="health report; --boot-check also boots the phone cold")
    d.add_argument("--home")
    d.add_argument("--boot-check", action="store_true", help="cold-boot the AVD per GPU mode, then stop it")
    d.add_argument("--full", action="store_true", help="with --boot-check: wait until Android finished booting")
    d.add_argument("--gpu", action="append", choices=[g.value for g in GpuMode], help="GPU mode(s) to try")
    sub.add_parser("selftest", help="built-in checks of this build (no device needed)")
    args = parser.parse_args(argv)
    return {"setup": cmd_setup, "doctor": cmd_doctor, "selftest": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
