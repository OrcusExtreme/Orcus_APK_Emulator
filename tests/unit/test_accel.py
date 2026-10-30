from orcus.common import accel
from orcus.common.accel import WhpxState
from orcus.contracts import CmdResult, ErrorCode

OK_OUTPUT = "accel:\n0\nWHPX(10.0.26100) is installed and usable.\naccel\n"
OFF_OUTPUT = "accel:\n1\nWHPX is not installed or not usable.\nPlease enable Windows Hypervisor Platform.\naccel\n"


def fake_run(out: str, code: int = 0, timed_out: bool = False):
    def run(argv, **kw):   # type: ignore[no-untyped-def]
        return CmdResult(tuple(argv), code, out, "", 0.1, timed_out)
    return run


def test_install_state_values() -> None:
    assert accel.query_install_state(fake_run("1\r\n")) is WhpxState.ENABLED
    assert accel.query_install_state(fake_run("2\r\n")) is WhpxState.DISABLED
    assert accel.query_install_state(fake_run("3")) is WhpxState.ABSENT
    assert accel.query_install_state(fake_run("4")) is WhpxState.UNKNOWN
    assert accel.query_install_state(fake_run("", code=1)) is None
    assert accel.query_install_state(fake_run("2", timed_out=True)) is None


def test_dll_and_hypervisor_present_do_not_matter() -> None:
    """This PC: WinHvPlatform.dll exists and HypervisorPresent=1, yet InstallState=2 means off."""
    assert accel.query_install_state(fake_run("2")) is WhpxState.DISABLED


def test_query_on_this_pc() -> None:
    assert accel.query_install_state() in set(WhpxState)


def test_emulator_check_parsing() -> None:
    ok = accel.parse_emulator_check(0, OK_OUTPUT)
    assert ok.usable and ok.hypervisor == "WHPX"
    off = accel.parse_emulator_check(1, OFF_OUTPUT)
    assert not off.usable
    hv = accel.parse_emulator_check(2, "hyper-v:\n2\nHyper-V is enabled\nhyper-v\n" + OK_OUTPUT)
    assert hv.usable, "the hyper-v block's code 2 must not hide a usable WHPX"
    aehd = accel.parse_emulator_check(0, "accel:\n0\nAEHD(2.2) is installed and usable.\naccel\n")
    assert not aehd.usable and aehd.hypervisor == "AEHD"


def test_failure_classification() -> None:
    boot = 1_000_000.0
    assert accel.classify_accel_failure(reboot_is_pending=True, dism_succeeded_at_boot=boot,
                                        current_boot=boot) is ErrorCode.SETUP_REBOOT_PENDING
    assert accel.classify_accel_failure(reboot_is_pending=False, dism_succeeded_at_boot=boot,
                                        current_boot=boot + 3600) is ErrorCode.SETUP_WHPX_REVERTED
    assert accel.classify_accel_failure(reboot_is_pending=False, dism_succeeded_at_boot=None,
                                        current_boot=boot) is ErrorCode.SETUP_ACCEL_FAILED
    assert accel.same_boot(boot, boot + 1.5) and not accel.same_boot(boot, boot + 120)
