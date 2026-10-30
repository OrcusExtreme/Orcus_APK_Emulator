"""GUI entry point (OrcusAPK.exe). The window arrives in Phase 3a.

Phase 2 ships only the self-checks of the packaged build. The windowed EXE has no console, so the
results go to <home>\\logs\\selftest-*.txt / bootcheck-*.json and the exit code (0 = pass).

    OrcusAPK.exe --selftest
    OrcusAPK.exe --boot-check
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
    print("Orcus APK Emulator 창은 Phase 3a에서 들어옵니다. 지금은 orcus-cli를 사용하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
