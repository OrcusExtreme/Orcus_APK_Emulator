from orcus.engine import bootcheck

REAL_LOG = """INFO         |      Ok: Hypervisor compatibility to run avd: `orcus36` are met
ERROR        | Unable to connect to adb daemon on port: 15037
WHPX on Windows 10.0.26100 detected.
Windows Hypervisor Platform accelerator is operational
WARNING      | Token/JWT auth requested without tls, restricting access to localhost.
"""


def test_accelerator_comes_from_the_emulator_log() -> None:
    """37.2.12 leaves getStatus.vmConfig.hypervisorType UNKNOWN even when WHPX runs."""
    assert bootcheck.accelerator_from_log(REAL_LOG) == "WHPX"
    assert bootcheck.accelerator_from_log("WHPX on Windows 10.0.26100 detected.\n") is None
    assert bootcheck.accelerator_from_log("") is None
