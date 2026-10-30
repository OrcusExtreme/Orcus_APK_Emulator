"""S3, human half (throwaway): does the headless emulator's sound come out of this PC's speakers?

Run it yourself with the speakers/headphones on and listen:
    D:\\OrcusDev\\venv\\Scripts\\python.exe tools\\spikes\\fx_s3_listen.py
1. HelloPurr: the cat meows 5 times, 2 s apart.
2. OrcusProbe: "안녕하세요" in Korean, twice.
No Orcus window exists yet (Phase 3), so this is the "no window at all" case of S3.
"""

import sys
import time

from fx_common import FIXTURES, Live, dump, install, launch, package_of, scroll_to, tap

from harness import LaunchMode

sys.stdout.reconfigure(encoding="utf-8")
HELLO, PROBE = FIXTURES / "HelloPurr.apk", FIXTURES / "OrcusProbe.apk"


def say(msg: str) -> None:
    print(f"\n>>> {msg}", flush=True)


say("휴대폰을 켜는 중입니다(약 10초). 스피커를 켜 두세요.")
with Live(mode=LaunchMode.GOLDEN) as live:
    install(live, HELLO, grant=True)
    install(live, PROBE, grant=True)
    launch(live, package_of(HELLO))
    time.sleep(1.5)
    kitty = dump(live).find(cls="android.widget.Button")
    assert kitty is not None
    say("지금부터 고양이 소리가 2초 간격으로 5번 납니다.")
    for i in range(5):
        tap(live, *kitty.center)
        print(f"    야옹 {i + 1}/5", flush=True)
        time.sleep(2)
    probe = package_of(PROBE)
    launch(live, probe)
    time.sleep(2)
    speak = scroll_to(live, "speak", probe)
    say("이제 한국어로 '안녕하세요'가 두 번 들립니다.")
    for i in range(2):
        tap(live, *speak.center)
        print(f"    안녕하세요 {i + 1}/2", flush=True)
        time.sleep(3)
say("끝났습니다. 고양이 소리와 '안녕하세요'가 들렸는지(작았는지, 끊겼는지) 알려 주세요.")
