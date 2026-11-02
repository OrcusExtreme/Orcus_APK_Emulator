"""GUI entry point (OrcusAPK.exe).

    OrcusAPK.exe [file.apk]      the window; a second start hands the file to the running one (§1.3)
    OrcusAPK.exe --home DIR      another runtime folder (default: orcus_home.txt next to the EXE, else D:\\OrcusAPK)
    OrcusAPK.exe --setup-resume  RunOnce after the reboot setup asked for: the wizard goes on by itself
    OrcusAPK.exe --selftest      built-in checks of the packaged build -> <home>\\logs\\selftest-*.txt
    OrcusAPK.exe --boot-check    short cold boots per GPU mode      -> <home>\\logs\\bootcheck-*.json

The windowed EXE has no console, so the self-checks report through files and the exit code (0 = pass). On a PC
without the phone's files the setup wizard opens first (§10.5).
"""

import json
import sys
import time

from orcus.app import compose
from orcus.common import i18n, messages_ko, win32   # messages_ko: the packaged build must carry the string table
from orcus.common.i18n import t
from orcus.common.paths import home_problems


def _arguments(argv: list[str]) -> tuple[str | None, list[str]]:
    """(--home value, files): every other --flag is for main(); a file name never starts with --."""
    home, files = None, []
    rest = iter(argv)
    for arg in rest:
        if arg == "--home":
            home = next(rest, None)
        elif not arg.startswith("--"):
            files.append(arg)
    return home, files


def main() -> int:
    compose.prepare_process()
    i18n.set_language(i18n.resolve("auto"))   # until the settings are read
    home, files = _arguments(sys.argv[1:])
    p = compose.make_paths(home)
    try:
        p.logs.mkdir(parents=True, exist_ok=True)
    except OSError as e:   # no D:, or a bad orcus_home.txt: say so instead of dying without a window or a log
        reasons = " ".join(messages_ko.text(r) for r in home_problems(p.root)) or str(e)
        win32.message_box("Orcus APK", t("{root} 폴더를 쓸 수 없어 시작하지 못했습니다.\n{reasons}", root=p.root,
                                         reasons=reasons))
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if "--selftest" in sys.argv:
        report = compose.selftest()
        from orcus.gui import app as gui_app   # the trimmed build still has Qt's Korean buttons
        words = gui_app.qt_words_file()
        report.add("qt korean buttons", words is not None, str(words or "qtbase_ko.qm not found"))
        lines = [f"{'OK  ' if ok else 'FAIL'} {name:<32} {detail}" for name, ok, detail in report.lines]
        (p.logs / f"selftest-{stamp}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0 if report.ok else 1
    if "--boot-check" in sys.argv:
        settings = compose.load_settings(p)
        compose.start_logging(p, "bootcheck")
        reports = compose.boot_check(p, settings)
        (p.logs / f"bootcheck-{stamp}.json").write_text(
            json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if all(r.ok for r in reports) else 1
    settings = compose.load_settings(p)
    i18n.set_language(i18n.resolve(settings.ui.language))
    compose.start_logging(p, "gui")

    from orcus.app.session import Session
    from orcus.gui import app as gui_app

    def make_session() -> Session:
        compose.ensure_job()   # before the first child (§1.2); only the first instance gets here
        return Session(p, settings, owner_kind="gui")
    return gui_app.run(make_session, p.logs, files, setup_resume="--setup-resume" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
