"""Every ErrorCode has a Korean message, exactly one channel per §10.2, and a remedy label."""

import re

from conftest import SRC
from orcus.common import messages_ko
from orcus.common.messages_ko import ERRORS, REMEDY_LABELS, TEXT, Channel
from orcus.contracts import ErrorCode, RemedyAction

BANNER_CODES = {ErrorCode.ENGINE_GPU_SOFTWARE, ErrorCode.ENGINE_ADB_LOST, ErrorCode.ENGINE_RESET_FAILED,
                ErrorCode.ENGINE_DISK_LOW, ErrorCode.ENGINE_NO_DISPLAY, ErrorCode.ENGINE_KEEP_FAILED,
                ErrorCode.ENGINE_KEEP_GONE, ErrorCode.ENGINE_KEEP_SAVE_FAILED, ErrorCode.ENGINE_GPU_LADDER,
                ErrorCode.APP_ORIENTATION_MISMATCH}   # §10.2 "방향 불일치", from RunEvent.warnings


def expected_channel(code: ErrorCode) -> Channel:
    name = code.name
    if name.startswith("SETUP_"):
        return Channel.SETUP_LINE
    if code in BANNER_CODES:
        return Channel.BANNER
    if name.startswith(("PRE_", "ENGINE_")):
        return Channel.ENGINE_PANEL
    if name.startswith(("FILE_", "STAGE_", "INSTALL_")) or code is ErrorCode.APK_PARSE:
        return Channel.MODAL
    if code is ErrorCode.APP_CRASH:
        return Channel.RED_BANNER
    assert name.startswith("APP_"), name
    return Channel.PROBLEM_CARD


def test_every_code_has_a_message_and_the_right_channel() -> None:
    assert set(ERRORS) == set(ErrorCode)
    for code, spec in ERRORS.items():
        assert spec.text.strip() and re.search(r"[가-힣]", spec.text), code
        assert spec.channel is expected_channel(code), code


def test_every_remedy_has_a_label() -> None:
    assert set(REMEDY_LABELS) == set(RemedyAction)
    for code, spec in ERRORS.items():
        if spec.remedy is not None:
            assert spec.remedy in REMEDY_LABELS, code


def test_error_builder_and_text() -> None:
    err = messages_ko.error(ErrorCode.SETUP_UAC_DENIED, "launched=False last_error=1223")
    assert err.remedy is not None and err.remedy.action is RemedyAction.COPY_IT_REQUEST
    assert messages_ko.text(err.message_key) == ERRORS[ErrorCode.SETUP_UAC_DENIED].text
    assert messages_ko.text(err.remedy.label_key) == "IT 요청문 복사"
    assert messages_ko.error(ErrorCode.APP_TTS_MISSING).remedy is None


def test_text_keys_used_in_code_exist() -> None:
    used: set[str] = set()
    for path in (SRC / "orcus").rglob("*.py"):
        used |= set(re.findall(r"""text\(\s*["']([a-z_.]+)["']""", path.read_text(encoding="utf-8")))
        used |= set(re.findall(r'"((?:path|python|proxy)\.[a-z_]+)"', path.read_text(encoding="utf-8")))
    actions = {f"remedy.{a}" for a in RemedyAction}   # resolved through REMEDY_LABELS, not TEXT (used by the wizard)
    missing = sorted(k for k in used if k not in TEXT and k not in actions)
    assert not missing, missing


def test_the_it_request_fits_the_problem() -> None:
    gone = messages_ko.error(ErrorCode.SETUP_QUARANTINE, "adb.exe")
    v3 = messages_ko.it_request_for(gone, "E:\\Orcus")
    assert "E:\\Orcus\\sdk" in v3 and "adb.exe" in v3
    denied = messages_ko.error(ErrorCode.SETUP_UAC_DENIED, "last_error=1223")
    assert "HypervisorPlatform" in messages_ko.it_request_for(denied, "E:\\Orcus")
    assert "아직 시도 전" in messages_ko.it_request_for(None, "E:\\Orcus", "아직 시도 전")


def test_it_request_fills_fields() -> None:
    text = messages_ko.it_request(messages_ko.IT_REQUEST_WHPX, "exit_code=3017")
    assert "HypervisorPlatform" in text and "exit_code=3017" in text and "{" not in text
    v3 = messages_ko.it_request(messages_ko.IT_REQUEST_V3, "missing", build="37.2.12", home="E:\\Orcus")
    assert "37.2.12" in v3 and "E:\\Orcus\\sdk" in v3 and "{" not in v3   # the folder setup really uses
