from pathlib import Path

from orcus.common.paths import OrcusPaths
from orcus.contracts import ErrorCode, LicenseText
from orcus.setup import license, whpx
from orcus.setup.whpx import DismOutcome, Launch


def outcome(launched: bool, last_error: int | None, exit_code: int | None) -> DismOutcome:
    return DismOutcome(launched, last_error, exit_code, None, 1.0)


def test_dism_verdicts() -> None:
    assert whpx.classify(outcome(False, 1223, None)) == whpx.DismVerdict(False, False, ErrorCode.SETUP_UAC_DENIED)
    assert whpx.classify(outcome(False, 5, None)).error is ErrorCode.SETUP_DISM_FAILED
    assert whpx.classify(outcome(True, None, 0)) == whpx.DismVerdict(True, False, None)
    assert whpx.classify(outcome(True, None, 3010)) == whpx.DismVerdict(True, True, None)
    assert whpx.classify(outcome(True, None, 3017)).error is ErrorCode.SETUP_DISM_FAILED
    assert whpx.classify(outcome(True, None, 87)).error is ErrorCode.SETUP_DISM_FAILED
    assert whpx.classify(outcome(True, None, None)).error is ErrorCode.SETUP_DISM_FAILED


def test_run_dism_reports_uac_refusal_as_launch_failure() -> None:
    calls: list[tuple[str, str]] = []

    def launch(file: str, params: str) -> Launch:
        calls.append((file, params))
        return Launch(False, 1223, None)
    result = whpx.run_dism(probe_job=False, launch=launch)
    assert not result.launched and result.last_error == 1223 and result.exit_code is None
    (file, params), = calls
    assert file.lower().endswith(r"\system32\dism.exe")
    assert params == "/online /enable-feature /featurename:HypervisorPlatform /all /norestart"


LT = LicenseText("android-sdk-license", "text v1", "sha-v1", ("platform-tools", "emulator"))
LT2 = LicenseText("android-sdk-license", "text v2", "sha-v2", ("platform-tools", "emulator"))


def test_license_records(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    assert license.pending(license.load_records(p), [LT]) == [LT]
    rec = license.record_acceptance(p, LT, r"LAB\student1")
    assert rec.accepted_by == r"LAB\student1" and rec.packages == LT.packages
    records = license.load_records(p)
    assert license.is_accepted(records, LT) and license.pending(records, [LT]) == []
    assert license.covers(records, ["platform-tools", "emulator"])
    assert not license.covers(records, ["system-images;android-36;google_apis;x86_64"])
    assert license.changed_since_acceptance(records, LT2)
    assert not license.changed_since_acceptance(records, LT)
    license.record_acceptance(p, LT, "again")   # same text twice: one record
    assert len(license.load_records(p)) == 1
