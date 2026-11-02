import json
from pathlib import Path

import pytest

from orcus.common import config
from orcus.common.paths import OrcusPaths
from orcus.contracts import ApplyResult, GpuMode, Sensitivity, Settings


def test_defaults_when_nothing_is_stored(tmp_path: Path) -> None:
    assert config.load(OrcusPaths(tmp_path), env={}) == Settings()


def test_layers_cli_beats_env_beats_file(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    p.state.mkdir(parents=True)
    p.state_file("settings.json").write_text(json.dumps(
        {"engine": {"gpu_mode": "host_hidden", "cores": 2}, "frame": {"max_fps": 30}}), encoding="utf-8")
    env = {"ORCUS_ENGINE__GPU_MODE": "software", "ORCUS_FRAME__MAX_FPS": "45", "ORCUS_HOME": "D:\\X"}
    s = config.load(p, env=env, cli={"frame.max_fps": 50})
    assert s.engine.gpu_mode is GpuMode.SOFTWARE
    assert s.engine.cores == 2
    assert s.frame.max_fps == 50


def test_bad_entries_are_skipped(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    p.state.mkdir(parents=True)
    p.state_file("settings.json").write_text(json.dumps(
        {"engine": {"gpu_mode": "auto", "ram_mb": 3072}, "nope": {"x": 1}}), encoding="utf-8")
    warnings: list[str] = []
    s = config.load(p, env={"ORCUS_INPUT__ESC_IS_BACK": "maybe"}, warnings=warnings)
    assert s.engine.gpu_mode is GpuMode.HOST and s.engine.ram_mb == 3072 and s.input.esc_is_back
    assert len(warnings) == 3


def test_an_unreadable_settings_file_starts_from_defaults(tmp_path: Path) -> None:
    """A hand edit saved as ANSI (CP949), a stray comma or Infinity must not stop the app."""
    p = OrcusPaths(tmp_path)
    p.state.mkdir(parents=True)
    f = p.state_file("settings.json")
    for raw in ('{"engine": {"locale": "한국어"}}'.encode("cp949"), b'{"engine": {"ram_mb": 3072,}}'):
        f.write_bytes(raw)
        assert config.load(p, env={}) == Settings()
        assert not f.exists() and p.state_file("settings.json.corrupt").read_bytes() == raw   # kept for a look
    f.write_text('{"engine": {"ram_mb": Infinity}}', encoding="utf-8")
    warnings: list[str] = []
    assert config.load(p, env={}, warnings=warnings).engine.ram_mb == Settings().engine.ram_mb and warnings


def test_apply_and_classify() -> None:
    base = Settings()
    assert config.classify(base, config.apply(base, {"engine.gpu_mode": "software"})) == ApplyResult(True, True)
    assert config.classify(base, config.apply(base, {"engine.ram_mb": 3072})) == ApplyResult(True, True)
    assert config.classify(base, config.apply(base, {"engine.idle_grpc_timeout_s": 300})) == ApplyResult(True, False)
    assert config.classify(base, config.apply(base, {"frame.max_fps": 30})) == ApplyResult(False, False)
    with pytest.raises(ValueError):
        config.apply(base, {"engine.no_such_key": 1})
    with pytest.raises(ValueError):
        config.apply(base, {"apk.grant_all": "sometimes"})


def test_structured_values() -> None:
    s = config.apply(Settings(), {
        "sensors.shake_sensitivity": {"appinventor.ai_x.HelloPurr": "strong"},
        "sensors.location_presets": [[35.1, 129.0, "부산"]],
        "privacy.mic_allowed_packages": ["a.b"],
        "engine.ports.grpc": 8641,
    })
    assert s.sensors.shake_sensitivity["appinventor.ai_x.HelloPurr"] is Sensitivity.STRONG
    assert s.sensors.location_presets == ((35.1, 129.0, "부산"),)
    assert s.privacy.mic_allowed_packages == ("a.b",)
    assert s.engine.ports.grpc == 8641 and s.engine.ports.console == 5640


def test_save_overrides_roundtrip(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    config.save_overrides(p, {"engine.gpu_mode": GpuMode.HOST_HIDDEN, "ui.confirm_close_data": False})
    config.save_overrides(p, {"frame.max_fps": 30})
    s = config.load(p, env={})
    assert (s.engine.gpu_mode, s.ui.confirm_close_data, s.frame.max_fps) == (GpuMode.HOST_HIDDEN, False, 30)
    with pytest.raises(ValueError):
        config.save_overrides(p, {"engine.gpu_mode": "auto"})
