from pathlib import Path

import pytest

from orcus.contracts import GpuMode
from orcus.engine import args, discovery
from orcus.engine.args import LaunchConfig, LaunchMode

EXE = Path(r"D:\OrcusAPK\sdk\emulator\emulator.exe")
CFG = LaunchConfig(EXE, console_port=5640, grpc_port=8640)
COMMON = [str(EXE), "-avd", "orcus36"]
TAIL = ["-port", "5640", "-grpc", "8640", "-grpc-use-token", "-idle-grpc-timeout", "120", "-no-boot-anim",
        "-no-metrics", "-timezone", "Asia/Seoul", "-feature", "-Vulkan"]


def test_golden_host_matches_section_5_2() -> None:
    assert args.build(CFG, LaunchMode.GOLDEN, GpuMode.HOST) == [
        *COMMON, "-no-window", "-gpu", "host", *TAIL,
        "-snapshot", "orcus_golden", "-force-snapshot-load", "-no-snapshot-save"]


def test_mode_and_gpu_rows() -> None:
    assert args.build(CFG, LaunchMode.COLD, GpuMode.SOFTWARE)[3:6] == ["-no-window", "-gpu", "software"]
    assert args.build(CFG, LaunchMode.GOLDEN, GpuMode.HOST_HIDDEN)[3:6] == ["-qt-hide-window", "-gpu", "host"]
    assert args.build(CFG, LaunchMode.COLD, GpuMode.SOFTWARE)[-2:] == ["-no-snapshot-load", "-no-snapshot-save"]
    assert args.build(CFG, LaunchMode.PROVISION, GpuMode.HOST)[-4:] == [
        "-no-snapshot-load", "-no-snapshot-save", "-change-locale", "ko-KR"]
    assert args.build(CFG, LaunchMode.REBUILD, GpuMode.HOST)[-1] == "-wipe-data"
    assert args.build(CFG, LaunchMode.KEEP_STATE, GpuMode.HOST, keep_slot="b")[-3:] == [
        "-snapshot", "orcus_user_b", "-no-snapshot-save"]
    with pytest.raises(ValueError):
        args.build(CFG, LaunchMode.KEEP_STATE, GpuMode.HOST)


def test_every_combination_keeps_the_invariants() -> None:
    for mode in LaunchMode:
        for gpu in GpuMode:
            if gpu == GpuMode.LADDER:   # the engine launches at the ladder's level, never with "ladder" itself
                with pytest.raises(ValueError):
                    args.build(CFG, mode, gpu, keep_slot="a")
                continue
            argv = args.build(CFG, mode, gpu, keep_slot="a")
            assert args.invariant_violations(argv) == []
            assert "-ports" not in argv and "auto" not in argv and "ladder" not in argv
            if "-snapshot" in argv:
                assert "-no-snapshot-save" in argv


def test_proxy_flag_only_when_given() -> None:
    assert "-http-proxy" not in args.build(CFG, LaunchMode.GOLDEN, GpuMode.HOST)
    with_proxy = args.build(LaunchConfig(EXE, 5640, 8640, http_proxy="http://h:80"), LaunchMode.GOLDEN, GpuMode.HOST)
    assert with_proxy[with_proxy.index("-http-proxy") + 1] == "http://h:80"


def test_invariant_checker_catches_violations() -> None:
    good = args.build(CFG, LaunchMode.GOLDEN, GpuMode.HOST)
    bad_gpu = [("auto" if a == "host" else a) for a in good]
    assert any("-gpu" in v for v in args.invariant_violations(bad_gpu))
    no_save = [a for a in good if a != "-no-snapshot-save"]
    assert any("-snapshot" in v for v in args.invariant_violations(no_save))
    no_token = [a for a in good if a != "-grpc-use-token"]
    assert any("token" in v for v in args.invariant_violations(no_token))
    odd = [("5641" if a == "5640" else a) for a in good]
    assert any("console port" in v for v in args.invariant_violations(odd))
    with pytest.raises(ValueError):
        args.build(LaunchConfig(EXE, 5641, 8640), LaunchMode.GOLDEN, GpuMode.HOST)
    with pytest.raises(ValueError):
        args.build(LaunchConfig(EXE, 5640, 50000), LaunchMode.GOLDEN, GpuMode.HOST)


AVD_DIR = Path(r"D:\OrcusAPK\avd\orcus36.avd")
RECORD = ("\ufeffport.serial=5640\r\nport.adb=5641\r\navd.name=orcus36\r\n"
          "avd.dir=D:\\OrcusAPK\\avd\\orcus36.avd\r\ncmdline=\"emulator.exe\" -avd orcus36\r\n"
          "grpc.port=8640\r\ngrpc.token=abc==\r\n")


def write(d: Path, pid: int, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"pid_{pid}.ini").write_bytes(text.encode("utf-8"))


def test_parse_bom_crlf() -> None:
    rec = discovery.parse(RECORD, 4242, Path("pid_4242.ini"))
    assert rec is not None
    assert (rec.serial_port, rec.adb_port, rec.grpc_port, rec.grpc_token) == (5640, 5641, 8640, "abc==")
    assert rec.avd_dir == r"D:\OrcusAPK\avd\orcus36.avd" and rec.cmdline is not None


def test_parse_incomplete_file() -> None:
    assert discovery.parse("port.ser", 1, Path("p")) is None
    partial = discovery.parse("port.serial=5640\ngrpc.port=86", 1, Path("p"))
    assert partial is not None and partial.grpc_port is None   # half-written last line ignored


def test_find_matches_serial_dir_and_live_pid(tmp_path: Path) -> None:
    running = tmp_path / "avd" / "running"
    write(running, 100, RECORD)                                              # ours, dead
    write(running, 200, RECORD.replace("orcus36.avd", "Pixel_9.avd"))       # Android Studio's
    write(running, 300, RECORD.replace("5640", "5554"))                      # other serial
    write(running, 400, RECORD)                                              # ours, alive
    write(running, 500, "garbage")
    found = discovery.find([running, tmp_path / "missing"], serial_port=5640, avd_dir=AVD_DIR,
                           alive=lambda pid: pid in (200, 300, 400))
    assert found is not None and found.pid == 400
    assert sorted(r.pid for r in discovery.ours([running], avd_dir=AVD_DIR)) == [100, 300, 400]


def test_korean_avd_dir_is_compared_exactly(tmp_path: Path) -> None:
    running = tmp_path / "running"
    write(running, 7, RECORD.replace(r"D:\OrcusAPK\avd\orcus36.avd", r"D:\한글\orcus36.avd"))
    assert discovery.find([running], serial_port=5640, avd_dir=Path(r"D:\한글\orcus36.avd"),
                          alive=lambda pid: True) is not None
    assert discovery.find([running], serial_port=5640, avd_dir=AVD_DIR, alive=lambda pid: True) is None


def test_candidate_dirs() -> None:
    env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local", "TMP": r"D:\OrcusAPK\tmp", "TEMP": r"D:\OrcusAPK\tmp"}
    assert discovery.candidate_dirs(env) == [Path(r"C:\Users\x\AppData\Local\Temp\avd\running"),
                                             Path(r"D:\OrcusAPK\tmp\avd\running")]
