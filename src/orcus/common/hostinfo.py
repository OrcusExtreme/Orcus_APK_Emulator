"""Facts about this Windows PC that setup records (S0a) and doctor prints."""

import os
import shutil
import winreg
from dataclasses import dataclass
from pathlib import Path

from . import win32

SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"


def service_exists(name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{SERVICES_KEY}\\{name}"):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class AntivirusInfo:
    v3: bool            # AhnLab V3 service present
    epp_managed: bool   # AhnLab EPP agent present: exclusions likely need IT


def antivirus() -> AntivirusInfo:
    return AntivirusInfo(v3=service_exists("V3Svc"), epp_managed=service_exists("EPPAgentSvc"))


def disk_free(path: Path) -> int:
    """Free bytes on the drive holding `path` (the path itself may not exist yet)."""
    return shutil.disk_usage(path.anchor).free


def computer_name() -> str:
    return os.environ.get("COMPUTERNAME", "?")


def elevation() -> str:
    return win32.elevation_type()
