"""GUI entry point (OrcusAPK.exe).

    OrcusAPK.exe [file.apk]      the window; a second start hands the file to the running one (§1.3)
    OrcusAPK.exe --selftest      built-in checks of the packaged build -> <home>\\logs\\selftest-*.txt
    OrcusAPK.exe --boot-check    short cold boots per GPU mode      -> <home>\\logs\\bootcheck-*.json

The windowed EXE has no console, so the self-checks report through files and the exit code (0 = pass).
"""

import json
import sys
import time

from orcus.app import compose
from orcus.common import messages_ko  # noqa: F401  (the packaged build must carry the string table)


def main() -> int:
    compose.prepare_process()
    p = compose.make_paths(None)
    p.logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if "--selftest" in sys.argv:
        report = compose.selftest()
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
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    settings = compose.load_settings(p)
    compose.start_logging(p, "gui")

    from orcus.app.session import Session
    from orcus.gui import app as gui_app

    def make_session() -> Session:
        compose.ensure_job()   # before the first child (§1.2); only the first instance gets here
        return Session(p, settings, owner_kind="gui")
    return gui_app.run(make_session, p.logs, files)


if __name__ == "__main__":
    sys.exit(main())
