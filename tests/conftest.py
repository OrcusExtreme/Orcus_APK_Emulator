import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO / "docs" / "ARCHITECTURE.md"
SRC = REPO / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
