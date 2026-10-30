"""S1 follow-up (throwaway): does Google TTS already hold Korean voice data, and can the guest reach the internet?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s1_tts_files.py
"""

import json

from harness import Live, LaunchMode

report: dict[str, object] = {}
with Live(mode=LaunchMode.GOLDEN) as live:
    def sh(cmd: str, timeout_s: float = 30) -> list[str]:
        r = live.adb("shell", cmd, timeout_s=timeout_s)
        return [line for line in (r.out + r.err).strip().splitlines() if line][:30]

    report["tts_data_dirs"] = sh("su 0 ls /data/data/com.google.android.tts/files")
    report["korean_files"] = sh("su 0 find /data/data/com.google.android.tts /product /system -iname '*ko*kr*' "
                                "-o -iname '*kor*' 2>/dev/null | grep -i -E 'tts|voice|speech' | head -20", 60)
    report["superpacks"] = sh("su 0 ls /data/data/com.google.android.tts/files/superpacks 2>/dev/null | head -30")
    report["internet"] = sh("ping -c 1 -W 3 8.8.8.8 | tail -2; cmd connectivity airplane-mode")
    report["locale"] = sh("getprop persist.sys.locale")
print(json.dumps(report, ensure_ascii=False, indent=2))
