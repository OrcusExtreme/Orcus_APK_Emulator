import socket
from pathlib import Path

import pytest

from orcus.common import ports
from orcus.common.paths import OrcusPaths
from orcus.contracts import ErrorCode, OrcusFailure, PortSettings


def test_defaults_when_free() -> None:
    chosen = ports.choose(PortSettings(), bindable=lambda p: True)
    assert chosen == ports.Ports(5640, 5641, 8640, 15037)
    assert chosen.serial == "emulator-5640"


def test_fallbacks_skip_busy_ports() -> None:
    busy = {5641, 8640, 8641, 15037}
    chosen = ports.choose(PortSettings(), bindable=lambda p: p not in busy)
    assert chosen == ports.Ports(5642, 5643, 8642, 15038)


def test_saved_ports_are_kept_while_free() -> None:
    saved = ports.Ports(5650, 5651, 8645, 15040)
    assert ports.choose(PortSettings(), saved, bindable=lambda p: True) == saved
    assert ports.choose(PortSettings(), saved, bindable=lambda p: p != 8645).grpc == 8640


def test_nothing_free_is_pre_ports() -> None:
    with pytest.raises(OrcusFailure) as e:
        ports.choose(PortSettings(), bindable=lambda p: p < 8000)
    assert e.value.error.code is ErrorCode.PRE_PORTS


def test_can_bind_sees_a_listener() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert not ports.can_bind(s.getsockname()[1])


def test_save_and_load(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    assert ports.load_saved(p) is None
    ports.save(p, ports.Ports(5640, 5641, 8640, 15037))
    assert ports.load_saved(p) == ports.Ports(5640, 5641, 8640, 15037)
