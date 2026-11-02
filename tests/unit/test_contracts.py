"""Every type named in ARCHITECTURE.md §3 exists in orcus.contracts (acceptance A7)."""

import dataclasses
import re

import orcus.contracts as c
from conftest import ARCHITECTURE


def section3_names() -> set[str]:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    start = text.index("## 3. 핵심 인터페이스")
    block = text[text.index("```python", start): text.index("```\n", text.index("```python", start) + 9)]
    return set(re.findall(r"^(?:class|type)\s+(\w+)", block, flags=re.MULTILINE))


def test_all_section3_types_are_defined() -> None:
    names = section3_names()
    assert {"ErrorCode", "SessionApi", "HeadlessSession", "DeviceSessionFactory", "CancellableIter"} <= names
    missing = sorted(n for n in names if not hasattr(c, n))
    assert not missing, missing
    not_exported = sorted(n for n in names if n not in c.__all__)
    assert not not_exported, not_exported


def test_dataclasses_are_frozen() -> None:
    for name in c.__all__:
        obj = getattr(c, name)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            params = getattr(obj, "__dataclass_params__")
            assert params.frozen, name


def test_settings_defaults_follow_section_11_3() -> None:
    s = c.Settings()
    assert s.engine.gpu_mode is c.GpuMode.HOST
    assert (s.engine.ram_mb, s.engine.cores, s.engine.idle_grpc_timeout_s, s.engine.keepalive_s) == (2560, 4, 120, 30)
    assert (s.engine.ports.console, s.engine.ports.grpc, s.engine.ports.adb_server) == (5640, 8640, 15037)
    assert (s.engine.locale, s.engine.timezone) == ("ko-KR", "Asia/Seoul")
    assert (s.apk.grant_all, s.apk.reset_before_each_run, s.apk.staging_keep) == (False, False, 20)
    assert s.input.paste_method == "paste_key" and s.input.esc_is_back and s.input.right_click_back
    assert (s.frame.flip_rows, s.frame.max_fps) == (False, 60)
    assert s.sensors.shake_default is c.Sensitivity.MODERATE and dict(s.sensors.shake_sensitivity) == {}
    assert s.sensors.location_presets[0] == (37.5665, 126.9780, "서울시청")   # SESSION_INIT's place comes first
    assert [name for _, _, name in s.sensors.location_presets] == ["서울시청", "남산서울타워", "부산시청"]
    assert s.privacy.mic_allowed_packages == () and s.ui.confirm_close_data and not s.session.keep_state


def test_gpu_mode_has_no_auto() -> None:
    # the emulator's own -gpu auto stays out (§5.6); "ladder" is Orcus stepping down by itself [P4]
    assert {m.value for m in c.GpuMode} == {"host", "host_hidden", "software", "ladder"}


def test_orcus_failure_carries_the_error() -> None:
    err = c.OrcusError(c.ErrorCode.ENGINE_IN_USE, "error.engine_in_use", detail="pid 42")
    exc = c.EngineNotReady(err)
    assert isinstance(exc, c.OrcusFailure) and exc.error is err and "pid 42" in str(exc)
