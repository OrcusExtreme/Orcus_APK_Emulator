"""Log files under D:\\OrcusAPK\\logs: one file per name and day, kept 14 days.

Daily file names instead of rotation: several processes (GUI, CLI, tests) may write at once and
Windows cannot rename a file another process has open.
"""

import logging
import time
from pathlib import Path

KEEP_DAYS = 14
FORMAT = "%(asctime)s %(levelname)-5s %(threadName)s %(name)s: %(message)s"


def prune(logs_dir: Path, name: str, keep_days: int = KEEP_DAYS, now: float | None = None) -> None:
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    for f in logs_dir.glob(f"{name}-*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def setup_file_logging(logs_dir: Path, name: str, level: int = logging.INFO) -> Path:
    """Attach a UTF-8 file handler for `<name>-YYYYMMDD.log` to the 'orcus' logger."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    prune(logs_dir, name)
    path = logs_dir / f"{name}-{time.strftime('%Y%m%d')}.log"
    logger = logging.getLogger("orcus")
    logger.setLevel(level)
    if not any(isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path for h in logger.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
    return path


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"orcus.{name}")
