"""orcus-cli: headless commands.

    orcus-cli setup  [--home DIR] [--resume]   install steps 1-8 (the license needs a human at the console)
    orcus-cli doctor [--home DIR]              read-only health report
    orcus-cli run APK [--policy P] [--watch S] boot the phone, install and launch, print problems for S seconds
    orcus-cli smoke [--apk HelloPurr.apk]      start, frame, key, install, tap, paste, reset, stop (§12.5)
    orcus-cli engine status|shot [--out PNG]   look at a phone another Orcus owns, without touching it

Exit codes: 0 done, 1 failed, 2 waiting for a Windows restart, 3 stopped by the user.
"""

import argparse
import json
import queue
import sys
import time
from pathlib import Path

from orcus.app import compose
from orcus.app import smoke as smoke_mod
from orcus.app.session import Session
from orcus.common import hostinfo, messages_ko, win32
from orcus.common.messages_ko import text
from orcus.common.pins import EMULATOR, EMULATOR_BUILD
from orcus.contracts import (
    EngineState,
    EngineStatus,
    ErrorCode,
    GpuMode,
    IssueLevel,
    OrcusError,
    RemedyAction,
    RunEvent,
    RunPolicy,
    RunStage,
    SetupEvent,
    SetupStep,
)

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


# --- run / smoke / engine ---------------------------------------------------------------------------------------
POLICIES = {"keep": RunPolicy.KEEP_DATA, "fresh-app": RunPolicy.FRESH_APP, "fresh-phone": RunPolicy.FRESH_PHONE}
ENDED = (RunStage.FAILED, RunStage.CANCELLED, RunStage.CRASHED, RunStage.EXITED)


def _headless(args: argparse.Namespace, log_name: str) -> Session:
    p = compose.make_paths(args.home)
    settings = compose.load_settings(p)
    log_path = compose.start_logging(p, log_name)
    _out(f"    로그: {log_path}")
    compose.ensure_job()   # before the engine starts the adb server (§1.2)
    return Session(p, settings, owner_kind="cli")


def _problem(err: OrcusError, indent: str = "    ") -> None:
    _out(f"{indent}{text(err.message_key)}")
    if err.detail:
        _out(f"{indent}({err.code}: {err.detail})")


def _engine_line(st: EngineStatus) -> str:
    pct = f" {st.progress:.0%}" if st.progress is not None else ""
    return f"[휴대폰] {text(st.text_key)}{pct}"


def _show_run(ev: RunEvent) -> None:
    line = f"[앱 {ev.elapsed_s:5.1f}s] {text(f'run.stage.{ev.stage}')}"
    if ev.stage is RunStage.INSPECTING and ev.info is not None:
        i = ev.info
        line += f" — {i.label} ({i.package}) {i.version_name or ''} · targetSdk {i.target_sdk} · {i.ai_era or '?'}"
    if ev.stage is RunStage.RUNNING and ev.pid:
        line += f" (pid {ev.pid})"
    if ev.prompt is None:
        _out(line)
    if ev.stage is RunStage.CHECKING:
        for issue in ev.issues:
            mark = {IssueLevel.BLOCK: "막힘", IssueLevel.WARN: "주의", IssueLevel.INFO: "참고"}[issue.level]
            _out(f"    [{mark}] {text(issue.text_key)}")
    if ev.problem is not None:
        _problem(ev.problem)


def cmd_run(args: argparse.Namespace) -> int:
    apk = Path(args.apk)
    s = _headless(args, "run")
    runs: "queue.Queue[RunEvent]" = queue.Queue()
    states: "queue.Queue[EngineStatus]" = queue.Queue()
    s.apps.subscribe(runs.put)
    s.engine.subscribe(states.put)
    code = 1
    try:
        s.engine.start()
        run_id = s.apps.submit(apk, POLICIES.get(args.policy or ""))
        last_key = ""
        watch_until: float | None = None
        give_up = time.monotonic() + args.timeout
        while True:
            while not states.empty():
                st = states.get()
                if st.text_key != last_key:
                    last_key = st.text_key
                    _out(_engine_line(st))
                if st.state is EngineState.FAILED and st.error is not None:
                    _problem(st.error)
            try:
                ev = runs.get(timeout=0.2)
            except queue.Empty:
                now = time.monotonic()
                if watch_until is not None and now >= watch_until:
                    break
                if watch_until is None and now >= give_up:
                    _out(f"    {args.timeout:.0f}초 안에 실행되지 않았습니다.")
                    break
                continue
            if ev.run_id != run_id:
                continue
            _show_run(ev)
            if ev.prompt is not None:
                _out(f"    {text(ev.prompt.text_key)}")
                answer = _ask("    바꾸려면 '예'를 입력하세요: ") or ""
                s.apps.answer(ev.run_id, ev.prompt.prompt_id, "replace" if answer in ("예", "y", "yes") else "cancel")
            elif ev.stage is RunStage.RUNNING and watch_until is None:
                code = 0
                watch_until = time.monotonic() + args.watch
                if args.watch:
                    _out(f"    {args.watch:.0f}초 동안 앱 로그를 지켜봅니다 (Ctrl+C로 끝내기)")
            elif ev.stage in ENDED:
                code = 1
                break
    except KeyboardInterrupt:
        code = 3
    finally:
        _out("[휴대폰] 끄는 중…")
        s.shutdown()
    return code


def cmd_smoke(args: argparse.Namespace) -> int:
    apk = Path(args.apk) if args.apk else None
    if apk is not None and not apk.exists():
        _out(f"APK가 없습니다: {apk}")
        return 1
    s = _headless(args, "smoke")
    p = compose.make_paths(args.home)
    _out("스모크: 시작 → 화면 → 키 → 설치·실행 → 탭 → 붙여넣기 → 초기화 → 정지"
         + ("" if apk else "  (--apk 없음: 앱 단계는 SKIP)"))
    try:
        steps = smoke_mod.run(s, p.sdk, apk, lambda st: _out(f"{st.status:<4} {st.name:<8} {st.seconds:6.1f} s  "
                                                            f"{st.detail}"))
    finally:
        s.shutdown()
    report = smoke_mod.table(steps)
    out = p.logs / f"smoke-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    out.write_text(f"orcus-cli smoke  emulator {EMULATOR.revision} ({EMULATOR_BUILD})\n{report}\n", encoding="utf-8")
    _out(report.splitlines()[-1] + f"   ({out})")
    return 1 if report.endswith("FAIL") else 0


def cmd_engine(args: argparse.Namespace) -> int:
    p = compose.make_paths(args.home)
    vm = compose.attach_readonly(p)   # no engine of our own: a look at the owner's phone only (§1.3)
    if vm is None:
        _out("다른 Orcus가 켠 휴대폰이 없습니다.")
        return 1
    try:
        st = vm.status()
        started = time.strftime("%H:%M:%S", time.localtime(vm.owner.started))
        _out(f"소유자: {vm.owner.kind} (pid {vm.owner.pid}, {started}부터) · 콘솔 {vm.owner.console} · "
             f"gRPC {vm.owner.grpc}")
        if st is None:
            _out("휴대폰이 응답하지 않습니다.")
            return 1
        uptime = vm.uptime_s()
        _out(f"상태: {text(st.text_key)}" + (f" · 켜진 지 {uptime:.0f}초" if uptime is not None else ""))
        if args.action == "shot":
            png = vm.screenshot_png()
            if png is None:
                _out("스크린샷을 받지 못했습니다.")
                return 1
            out = Path(args.out) if args.out else p.logs / f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
            out.write_bytes(png)
            _out(f"스크린샷: {out} ({len(png):,} B)")
        return 0
    finally:
        vm.close()


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
    r = sub.add_parser("run", help="boot the phone, install and launch an APK, print problems")
    r.add_argument("apk")
    r.add_argument("--home")
    r.add_argument("--policy", choices=list(POLICIES), help="default: settings (keep, or fresh-phone)")
    r.add_argument("--watch", type=float, default=10.0, help="seconds to keep printing problems once running")
    r.add_argument("--timeout", type=float, default=900.0, help="give up if not running after this many seconds")
    m = sub.add_parser("smoke", help="upgrade gate: start, frame, key, install, tap, paste, reset, stop")
    m.add_argument("--home")
    m.add_argument("--apk", help="your HelloPurr build (the tap is checked through its vibration)")
    e = sub.add_parser("engine", help="look at a phone another Orcus owns (read-only)")
    e.add_argument("action", choices=["status", "shot"])
    e.add_argument("--home")
    e.add_argument("--out", help="with shot: PNG path (default logs\\shot-*.png)")
    args = parser.parse_args(argv)
    return {"setup": cmd_setup, "doctor": cmd_doctor, "selftest": cmd_selftest, "run": cmd_run,
            "smoke": cmd_smoke, "engine": cmd_engine}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
