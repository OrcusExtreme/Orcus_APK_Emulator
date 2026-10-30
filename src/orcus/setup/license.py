"""License acceptance records (state\\licenses.json).

Acceptance is recorded only when a human typed it (★D2). There is no --yes flag and no
environment variable; the CLI refuses unless stdin is a real console.
"""

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from orcus.common import jsonio
from orcus.common.paths import OrcusPaths
from orcus.contracts import LicenseText

FILE_NAME = "licenses.json"


@dataclass(frozen=True)
class LicenseRecord:
    license_id: str
    sha256: str
    accepted_at: str
    accepted_by: str
    packages: tuple[str, ...]


def load_records(paths: OrcusPaths) -> list[LicenseRecord]:
    data = jsonio.read_json(paths.state_file(FILE_NAME))
    if not isinstance(data, dict):
        return []
    records: list[LicenseRecord] = []
    for item in data.get("accepted", []):
        try:
            records.append(LicenseRecord(item["license_id"], item["sha256"], item["accepted_at"],
                                         item["accepted_by"], tuple(item.get("packages", ()))))
        except (KeyError, TypeError):
            continue
    return records


def is_accepted(records: Sequence[LicenseRecord], text: LicenseText) -> bool:
    return any(r.license_id == text.license_id and r.sha256 == text.sha256 for r in records)


def changed_since_acceptance(records: Sequence[LicenseRecord], text: LicenseText) -> bool:
    """An older text of the same license was accepted: the new one needs a fresh agreement."""
    return not is_accepted(records, text) and any(r.license_id == text.license_id for r in records)


def pending(records: Sequence[LicenseRecord], texts: Sequence[LicenseText]) -> list[LicenseText]:
    return [t for t in texts if not is_accepted(records, t)]


def record_acceptance(paths: OrcusPaths, text: LicenseText, accepted_by: str) -> LicenseRecord:
    record = LicenseRecord(text.license_id, text.sha256, time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           accepted_by, text.packages)
    records = [r for r in load_records(paths) if not (r.license_id == record.license_id and r.sha256 == record.sha256)]
    records.append(record)
    jsonio.write_json_atomic(paths.state_file(FILE_NAME),
                             {"accepted": [asdict(r) | {"packages": list(r.packages)} for r in records]})
    return record


def covers(records: Sequence[LicenseRecord], package_paths: Sequence[str]) -> bool:
    """Every package path appears in some acceptance record (used when nothing needs downloading)."""
    accepted = {p for r in records for p in r.packages}
    return all(p in accepted for p in package_paths)
