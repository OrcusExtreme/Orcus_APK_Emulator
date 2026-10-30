import os
import sys
from pathlib import Path

# Keep bytecode out of the Drive-synced folder whichever interpreter runs pytest (an IDE's default
# Python wrote cpython-311 caches here once). tools/dev.py already sets PYTHONPYCACHEPREFIX.
if sys.pycache_prefix is None:
    sys.pycache_prefix = str(Path(os.environ.get("ORCUS_DEV_ROOT", r"D:\OrcusDev")) / "pycache")

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO / "docs" / "ARCHITECTURE.md"
SRC = REPO / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
