"""Download the public test fixtures into D:\\OrcusDev\\fixtures and check their pinned SHA-256.

Only files we may fetch automatically are here. App Inventor builds (HelloPurr, TalkToMe, OrcusProbe ...)
are made by the user; see docs/fixtures.md.

Usage: py tools/fetch_fixtures.py [--pin]   (--pin prints the hashes of what was downloaded)
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
import devenv  # noqa: E402

FIXTURES = devenv.DEV_ROOT / "fixtures"
# (file name, url, size, sha256). The companion URL always serves MIT's latest build; a new build shows
# up as a hash mismatch, and the pin is then updated on purpose (parser regression fixture).
PINNED = [
    ("MITAI2Companion.apk", "https://ai2.appinventor.mit.edu/companions/MITAI2Companion.apk", 18_365_393,
     "1e104351417455816d739e63401f051d30e624e0527a9aff37af1715868ecb32"),   # 2.81u, pinned 2026-10-30
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    pin_mode = "--pin" in sys.argv
    failed = 0
    for name, url, size, digest in PINNED:
        dest = FIXTURES / name
        if not dest.exists() or dest.stat().st_size != size:
            print(f"downloading {name} ...", flush=True)
            part = dest.with_name(name + ".part")
            with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as out:
                while block := r.read(1 << 20):
                    out.write(block)
            part.replace(dest)
        actual = sha256(dest)
        if pin_mode:
            print(f"{name}: size={dest.stat().st_size} sha256={actual}")
        elif actual != digest:
            print(f"{name}: sha256 {actual} != pinned {digest}")
            failed += 1
        else:
            print(f"{name}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
