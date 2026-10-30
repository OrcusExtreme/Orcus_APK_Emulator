"""GUI entry point (OrcusAPK.exe). The window arrives in Phase 3a.

Phase 2 ships only the self-checks of the packaged build (--selftest, --boot-check), which are
added together with orcus.emugrpc.
"""

import sys

from orcus.common import messages_ko  # noqa: F401  (keeps the packaged build's string table importable)


def main() -> int:
    print("Orcus APK Emulator 창은 Phase 3a에서 들어옵니다. 지금은 orcus-cli를 사용하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
