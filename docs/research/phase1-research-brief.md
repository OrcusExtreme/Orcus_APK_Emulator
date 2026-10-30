<!--
  Phase 1 조사 요약(원문 영어, 2026-10-29). 설계·적대적 검토·수정 점검 단계에서 확인된 정정 사항을 아래에 먼저 적는다.
  본문과 정정이 다르면 정정과 docs/ARCHITECTURE.md(v3)가 우선한다.
-->

> **정정 사항 (Phase 1 설계·심사·적대적 검토·수정 점검에서 확인. 본문보다 우선)**
> 1. **§7 "Ship pinned platform-tools 37.0.1"** → **틀림.** platform-tools도 android-sdk-license §3.4(재배포 금지) 대상이다. 다른 SDK 구성요소처럼 사용자 PC에서 사람이 라이선스에 동의한 뒤 다운로드한다.
> 2. **§2 `emulator-check.exe accel hyper-v`의 종료 코드에 의존하지 않는다.** 검사마다 결과 블록이 따로 나오고, Hyper-V가 켜진 PC에서는 hyper-v 블록이 코드 2를 낸다. `emulator-check.exe accel`을 단독으로 실행하고 출력에서 `WHPX(`와 `is installed and usable`을 확인한다(Windows 빌드 번호는 고정하지 않음).
> 3. **§2 "`-port N` without `-grpc` ⇒ no gRPC"** → 다른 소스 해석(`-ports a,b`일 때만 해당)과 충돌한다. 우리는 항상 `-grpc <port> -grpc-use-token`을 주므로 영향이 없다. 실제 동작은 S6에서 기록한다.
> 4. **§4 흔들기** → "repeat no sooner than 400 ms"를 한 번 누를 때마다 반복하면 Shaking이 여러 번 울린다. **클릭당 펄스 1회**로 하고, Δ는 감도별(Moderate 16 / Weak 25 / Strong 9)로 둔다. 펄스는 **중력 방향**으로 준다(`sign(b)·Δ`). 역방향 자세에서 0을 넘으면 프레임 회전이 뒤집히기 때문이다. "다른 감도에서는 안 울림"은 성립하지 않는다(10개 표본 평균 때문).
> 5. **§7 QProcess 권장** → Qt를 모르는 core는 `subprocess` 기반 자체 실행기(타임아웃 보장)와 스레드를 쓴다. 그래야 CLI·테스트와 코드를 공유할 수 있다.
> 6. **§7 adb 스캔·재등록** →
>    - private 서버는 `ADB_LOCAL_TRANSPORT_MAX_PORT=5553`으로 시작 스캔을 끈다. 스캔은 5555부터 2씩 하나씩 연결을 시도하는데, 이 PC에서는 닫힌 포트마다 약 2 s가 걸려 5641까지 약 86 s다.
>    - 서버를 띄운 뒤에는 `host:emulator:5641` 요청(`0012host:emulator:5641`)을 직접 보내 바로 재등록한다. 에뮬레이터가 시작할 때 보내는 것과 같은 요청이다.
>    - `ADB_EMU=0`은 쓰지 않는다. 끊긴 연결을 다시 잇는 스레드까지 꺼지기 때문이다.
>    - `ADB_USB=0`으로 학생의 USB 휴대폰을 잡지 않는다. 계정 간에는 공유 adb 키(`ADB_VENDOR_KEYS`)를 쓴다(S9).
> 7. **gRPC 채널 대상은 `localhost:<port>`**, 채널 옵션은 `grpc.enable_http_proxy=0`. 토큰 서버는 ::1 또는 127.0.0.1에 바인드할 수 있다. 에뮬레이터 프로세스의 `http_proxy`는 지우지 않는다(게스트 프록시로 쓰인다).
> 8. **adb 서버 기동** → `adb -P <port> nodaemon server`로 직접 띄우는 것을 우선한다. 일회성 클라이언트는 `-H 127.0.0.1`로 부른다. 그러면 서버가 내려가 있을 때 클라이언트가 서버를 몰래 띄우지 않는다(adb 소스로 확인). "start-server를 PIPE로 받으면 멈춘다"는 전제는 adb가 표준 핸들을 상속하지 않게 바뀐 뒤로 낡았을 수 있다(추론).
> 9. **§2 "`-snapshot <name>` … never overwrites it"** → 공식 문서는 "종료할 때 같은 이름으로 다시 저장한다"고 해서 서로 충돌한다. 골든 보호는 **항상 함께 주는 `-no-snapshot-save`**에 맡긴다(불변식). 같은 이름으로 다시 저장할 때의 동작도 경로마다 다르다(36.1·master 소스):
>    - gRPC `SaveSnapshot`은 이미 있는 이름을 거부한다(`success=false`).
>    - 콘솔·QEMU 경로는 이전 스냅샷을 먼저 지우고 쓰며, 실패하면 지운다.
>    - `DeleteSnapshot` 응답은 항상 성공이다.
>    - 그래서 저장 전에 Delete를 부르고 List로 확인하며, 보관 상태는 두 슬롯에 번갈아 저장한다.
> 10. **WHPX 상태 판정** →
>     - `WinHvPlatform.dll`이 있는지나 `WHvGetCapability(HypervisorPresent)`로 판단하지 않는다. 이 PC는 기능이 꺼져 있는데도 DLL이 있고, Hyper-V가 돌고 있어 HypervisorPresent가 1이다.
>     - Win32_OptionalFeature `HypervisorPlatform`의 InstallState(1 켜짐, 2 꺼짐, 3 없음, 4 알 수 없음)로 판단한다. CBS `RebootPending` 키는 재부팅을 기다리는 서비스 작업이 있다는 뜻일 뿐이다(Windows 업데이트일 수도 있다). 그래서 가속 확인이 실패했을 때 원인을 설명하는 데만 쓴다.
>     - dism은 `ShellExecuteExW`로 실행한다. **UAC를 거절하면 dism 종료 코드가 아니라 실행 실패(FALSE + `GetLastError()==1223`)로 나타난다.** dism 종료 코드는 0/3010(성공), 3017(실패, 되돌리려면 재부팅) 등이다.
> 11. **§4 "Android typically samples GPS at 1 Hz"** → GNSS HAL 기준이다. App Inventor LocationSensor 기본값은 TimeInterval 60000 ms·DistanceInterval 5 m라서, 첫 위치 뒤에는 약 1분마다·5 m 이상 움직였을 때만 갱신된다. '대략적인 위치'(coarse)만 허용하면 10분 간격이다.
> 12. **§2 프록시** → 에뮬레이터는 Windows 시스템 프록시(WinINET 레지스트리)를 읽지 않는다. 시스템 프록시가 켜져 있고 `http_proxy`가 없으면 `-http-proxy`로 넘긴다. PAC/WPAD는 지원하지 않는다.
> 13. **§1 AhnLab V3** → EPP로 중앙 관리된다(EPP 에이전트와 자기 보호 서비스가 실행 중). 사용자가 예외를 직접 넣지 못할 수 있으므로 IT 요청문을 준비한다.
> 14. **§2 에뮬레이터 패키지** → 37.2.12에는 `netsimd.exe`와 `crashpad_handler.exe`가 들어 있고 `WiFiPacketStream=on`이다. 추가 프로세스가 생기는지, Job에 속하는지, 얼마나 남는지는 S4·S6·S9에서 확인한다.
> 15. **§6 "Suppress the old-SDK dialog … (userdebug images)"** → shell 사용자는 **user 빌드에서도** `debug.*` 속성을 설정할 수 있다(AOSP sepolicy). 그래서 playstore 이미지에서도 대화상자 억제가 된다. playstore로 바꿀 때 잃는 것은 root와 `-d` 다운그레이드뿐이다.
> 16. **§1 "1920x1200 display"** → 이 PC는 모니터가 3대다. 주 모니터 1920×1080(작업 영역 높이 1032 px), 둘째 1920×1080, 그리고 1920×1200 모니터를 세로(1200×1920)로 쓴다. 폰 화면 크기는 창이 놓인 화면 기준으로 계산한다(주 모니터에서 약 432×768).
> 17. **§2 `-force-snapshot-load`** → 로드 검증에 실패하면 에뮬레이터가 그 스냅샷을 **지운 뒤** 종료한다(36.1·master 소스 `Quickboot.cpp`. 37.2.12는 S4에서 확인). 그래서 골든 로드 실패는 재시도하지 않고 재구축한다. 보관 슬롯은 강제 로드 없이 부팅하고 RAM 표식으로 감지한다.

---

# Research brief for the Phase 1 design (verified 2026-10-29)

Confidence markers: (P) = read in primary source/official docs; (I) = inferred; (U) = unverified / must be tested.

## 0. Product goal and constraints
- A lightweight Windows 11 app that runs APKs exported from MIT App Inventor: drop an .apk on a phone-framed window → the app installs and runs interactively.
- Required decoupling: (1) Core Engine Manager (VM lifecycle, ports, ADB), (2) APK Lifecycle & Automation (parse manifest → package + launcher activity, install, am start), (3) Host GUI Client (phone frame, live screen, mouse→touch, Back/Home/Recents, drag-and-drop / file picker).
- Must be faster to start and lighter than BlueStacks/Nox. Do not reimplement Android/ART; orchestrate an existing virtualization engine.
- The user works phase by phase with approval at each boundary. Phase 2 = scaffolding + setup scripts; Phase 3 = implementation (APK parser, engine controller, GUI + input).

## 1. This machine
- Windows 11 Education 24H2 (10.0.26100). Intel i7-10700 (8C/16T). 32 GB RAM. NVIDIA GTX 1660 4 GB (driver 32.0.15.9579) plus a "Mirage Driver" mirror adapter (remote-control software). 1920x1200 display.
- Hyper-V and VBS (Credential Guard) are running. **Windows Hypervisor Platform (HypervisorPlatform) is DISABLED.**
- **C: has only 17.6 GB free**; D: has 1.6 TB free. The project folder `D:\Project_Lab\09_프로그램_개발\Orcus_APK_Emulator` is inside a **Google Drive for desktop synced tree** and its path is **non-ASCII (Hangul)**. AhnLab V3 is the active real-time antivirus.
- Python: `py -3.14` = 3.14.6, the install-manager runtime (`%LOCALAPPDATA%\Python\pythoncore-3.14-64`); `py -3.13` = Microsoft Store build (AppData virtualization trap); `py -3.11` = 3.11.9 (too old for numpy 2.5 / av 19). Locale encoding cp949.
- .NET: runtimes 8.0.31 only (EOL 2026-11-10), no SDK or Visual Studio. Inno Setup 6 is installed (`%LOCALAPPDATA%\Programs\Inno Setup 6`). winget, Git available. No Android SDK, adb, Java or scrcpy yet.
- The user is Korean, so Hangul text input inside apps matters. They have built Python apps and a C# WinForms controller + Inno installer before, and were bitten by packaging mismatches between a dev Python and an installer-bundled Python.

## 2. Engine: Google Android Emulator (classic "emulator" package)
- (P) Stable **37.2.12** (build 16428233, 2026-10-01). Windows zip 434 MiB → 1.07 GiB installed. Dev 37.3.6 (preview license) fixes two 37.2.12 Windows defects: **non-ASCII AVD path hang (#568816248)** and **guest always GMT (#574875267; `-timezone` works around it)**.
- (P) The next-gen "Android Emulator (Preview)" 37.1.6 (upstream QEMU 11) is the default only for API 37+ phone AVDs and ships no .proto files. Avoid API 37 images; keep the classic engine behind an adapter.
- (P) Acceleration: **WHPX is the only option here** (Hyper-V running). Enable optional feature `HypervisorPlatform` (admin) and **reboot**. AEHD cannot run while Hyper-V/VBS is on and sunsets 2026-12-31. HAXM was removed in 36.2.11. TCG (`-accel off` + `ANDROID_I_WANT_MY_TCG=yes`) is unsupported and very slow, so refuse to run without WHPX. `emulator-check.exe accel hyper-v` → code 0 "WHPX(10.0.26100) is installed and usable." when ready (I: code 6 today).
- (P) **Java-free install** works: parse `https://dl.google.com/android/repository/repository2-4.xml` (emulator on channel-0; platform-tools 37.0.1 = `platform-tools_r37.0.1-win.zip`, 8.0 MB) and `.../sys-img/<tag>/sys-img2-5.xml` (fall back to the -3 versions). Each archive has a size, a SHA-1 and a relative URL; the license text is embedded (`<license id>`) and referenced by `<uses-license>`.
- (P) **android-sdk-license §3.4 forbids redistribution**: download on the user's machine after explicit license acceptance. Never bundle SDK parts. Do not trim packages without legal review. Use only the stable channel.
- (P) Minimal SDK layout: `<sdk>\emulator\`, `<sdk>\platform-tools\`, `<sdk>\system-images\android-NN\<tag>\x86_64\` (move the zip's top-level `x86_64` folder there) and an **empty `<sdk>\platforms\`**, which 36.1-era code requires.
- (P) Hand-written AVD:
  - `<AVD_HOME>\orcus.ini`: `avd.ini.encoding=UTF-8`, `path=<abs .avd dir>`, `target=android-NN`.
  - `config.ini`: `hw.cpu.arch=x86_64` (**default is arm!**), `abi.type=x86_64`, `image.sysdir.1=system-images\android-NN\<tag>\x86_64\`, `tag.id=<tag>`, `hw.lcd.width=720`, `hw.lcd.height=1280`, `hw.lcd.density=320`, `hw.ramSize=2048`, `hw.cpu.ncore=4`, `hw.keyboard=yes`, `hw.gpu.enabled=yes`, `hw.gpu.mode=host`, `PlayStore.enabled=false`.
  - AVD names may only use `[A-Za-z0-9_.-]`. Userdata is forced to ≥ 6 GiB and creation needs 1.2× that free.
- (P) Set these **in child-process environments only**:
  - `ANDROID_HOME` = `ANDROID_SDK_ROOT` = `<root>\sdk`.
  - `ANDROID_AVD_HOME` = `<root>\avd`. The folder must exist; otherwise Git Bash's `HOME\.android\avd` can win.
  - `ANDROID_EMULATOR_HOME` = `<root>\home`. The 36.1 code ignores `ANDROID_USER_HOME`.
  - `TMP` = `TEMP` = `<root>\tmp`, which moves the emulator temp dir and its crashpad DB.

  Some KB-sized files stay on C: anyway: `%USERPROFILE%\.emulator_console_auth_token`, `%USERPROFILE%\.android\adbkey` (adb ignores `ANDROID_USER_HOME`) and the discovery dir `%LOCALAPPDATA%\Temp\avd\running` (fixed location).
- (P) Headless:
  - `-no-window` runs `qemu-system-x86_64-headless.exe` (`-nographic`). `-qt-hide-window` is Android Studio's mode (full Qt loaded, window hidden).
  - `emulator.exe` spawns qemu with `_wspawnv` and waits, so **killing emulator.exe leaves qemu running**. Use a Job Object (KILL_ON_JOB_CLOSE).
- (P/U) GPU: the documented modes are `auto|host|software|lavapipe|swiftshader|swangle`; `swiftshader_indirect`, `guest` and `swangle_indirect` are deprecated. Under `-no-window`, `auto` may silently become SwiftShader (2025 gfxstream code, unverified for 37.x). So:
  - pass `-gpu host` explicitly and verify the GLES renderer after boot;
  - fall back to `-qt-hide-window -gpu host`, then `-gpu software` (snapshots are unreliable with software rendering);
  - remote sessions and the Mirage adapter can push `auto` to software.
- (P/U) Audio: `-no-window` does not disable audio. The hda/virtio-snd card plays to the host through DirectSound (untested on this PC). The host microphone is zeroed unless `-allow-host-audio` is passed or `setMicrophoneState(realAudioEnabled=true)` is called.
- (P) **Port/flag traps:**
  - `-port N` **without** `-grpc` ⇒ **no gRPC server and no discovery file**.
  - A bare `-grpc M` ⇒ binds `[::]:M` with **no auth**, exposed to the LAN.
  - `-grpc M -grpc-use-token` ⇒ a random 88-char token and a loopback-only bind. If M is busy, the emulator exits, so test-bind M first.
- (I) Recommended launch:
  - `emulator.exe -avd orcus -no-window -gpu host -port 5640 -grpc 8640 -grpc-use-token -idle-grpc-timeout 120 -no-boot-anim -no-metrics -timezone Asia/Seoul [-feature -Vulkan]`.
  - Use an even console port in 5586–5682, so foreign adb servers that scan 5555–5585 at startup do not attach.
  - Keep ports < 49152 to stay clear of the WinNAT/Hyper-V reserved ranges.
- (P) Discovery and auth:
  - The discovery file is `%LOCALAPPDATA%\Temp\avd\running\pid_<pid>.ini`. Keys: `port.serial`, `port.adb`, `emulator.version`/`emulator.build`, `avd.name`, `avd.dir`, `cmdline`, `grpc.port`, `grpc.token`, `grpc.allowlist`.
  - Match it on `port.serial`; the PID may be the qemu child. Stale files remain after crashes.
  - Auth: metadata `authorization: Bearer <grpc.token>`, which maps to the `android-studio` allowlist in `<sdk>\emulator\lib\emulator_access.json`. If that file is missing, all access is blocked.
- (P) `-idle-grpc-timeout S`: graceful VM shutdown after S seconds with no active RPC or stream. It acts as an orphan watchdog if the GUI dies. Keep a stream open or send a `getVmState` keepalive (Android Studio uses 2 min keepalive / 300 s timeout).
- (P/I) Quickboot:
  - The file-backed RAM snapshot is about `hw.ramSize` on disk.
  - It is invalidated by emulator/image updates, NVIDIA driver string changes, GPU-mode/RAM/core/feature changes and `-change-locale`.
  - `-snapshot <name>` boots a named snapshot and never overwrites it. `-no-snapshot-save`, `-no-snapshot-load` and `-force-snapshot-load` also exist.
  - Graceful stop (`setVmState SHUTDOWN` or `adb emu kill`) saves state.
  - (U) Expected times: cold boot 20–45 s (first boot 1–2 min), quickboot 2–8 s.
- (P) Disk per AVD: emulator 1.07 GiB + image + userdata (up to 6 GiB) + RAM file 2 GiB ⇒ **12–14 GiB worst case, which must be on D:**. Images: google_apis 36 r7 is 1.90 GB to download and 4.37 GiB installed; google_apis 35 r9 is 1.74 GB and 3.5 GiB; AOSP default 36 r2 is 0.84 GB and 1.9 GiB.
- (I) RAM: idle ≈ 2.5–3.5 GB committed for a 2 GB AVD. Play images have CPU spikes from auto-updates and hotword detection.

## 3. System images
- (P) x86_64 tags: `default` (AOSP), `google_apis`, `google_apis_playstore`, `aosp_atd`/`google_atd` (ATD = no SystemUI/LatinIME/launcher, unusable interactively) and ps16k variants.
- (P) **API 37 phone AVDs are forced to ≥ 4 GB RAM** and use the next-gen engine, so avoid them.
- (P) ARM translation: google_apis/playstore 34–37 offer x86_64 + arm64-v8a through libndk_translation, with no 32-bit ARM. google_apis API 30 offers all ABIs including armeabi-v7a. AOSP default and ATD images have none.
- (P) `adb root`: google_apis and default are userdebug (root works). playstore is a user build: no root, and no `-d` downgrade of non-debuggable apps.
- (P) TTS: **AOSP default has NO TTS engine** (Pico was removed after 8.1).
- (U) Google images have been observed with `com.google.android.tts` on sdk_gphone64 builds, but this is **not verified** for google_apis (non-Play) API 35/36 x86_64, and neither are an offline ko-KR voice, a RecognitionService (Google app) or Gboard Korean. Verify on first boot with `pm list packages com.google.android.tts`, `cmd package query-services -a android.intent.action.TTS_SERVICE`, `cmd package query-activities -a android.speech.action.RECOGNIZE_SPEECH` and `settings get secure tts_default_synth`.
- (P) Licenses: android-sdk-license covers the stable emulator, platform-tools and API 35/36 images. The preview license covers the dev emulator, Preview builds and API 31/32 google_apis. arm-dbt covers API 30/31 Play images; ARM translation is for dev/debug only, not commercial hosted services.

## 4. Emulator gRPC API (package android.emulation.control; protos in `<sdk>\emulator\lib\*.proto`; canonical source platform/hardware/google/aemu `protos/services`, emu-main-dev)
- (P) EmulatorController RPCs:
  - Screen: `streamScreenshot` / `getScreenshot`.
  - Input: `streamInputEvent` (client stream: KeyboardEvent, TouchEvent, MouseEvent, WheelEvent, AndroidEvent…); `sendKey` / `sendTouch` / `sendMouse` (marked deprecated in the 37.3.6 text); `injectWheel`.
  - Device state: `setSensor`/`getSensor`, `setPhysicalModel`/`getPhysicalModel`, `setGps`/`getGps`, `setBattery`, `sendFingerprint`.
  - Clipboard and audio: `setClipboard`/`getClipboard`/`streamClipboard`; `streamAudio`/`injectAudio`, `get`/`setMicrophoneState`.
  - Logs and VM control: `streamLogcat` (LogcatEntry pid/tid/level/tag/msg); `setVmState`/`getVmState` (RUNNING, PAUSED, SHUTDOWN, TERMINATE, RESET, RESTART = restarts emulator).
  - Status and notifications: `getStatus` (version, booted, vmConfig.hypervisorType WHPX=5, hardwareConfig); `streamNotification` (BootCompleted, DisplayPowerMode…; TextViewFocus is **never emitted** by the classic engine).
  - Telephony: `sendSms`, `sendPhone`.
  - Displays: `set`/`getDisplayConfigurations` (secondary displays only); `setDisplayMode` (resizable AVDs only).

  Other services:
  - **SnapshotService**: ListSnapshots, SaveSnapshot, LoadSnapshot, DeleteSnapshot, Push/PullSnapshot, UpdateSnapshot, GetScreenshot.
  - **UiController** needs a Qt window (`-qt-hide-window`).
- (P) Frames:
  - Request: `ImageFormat{format RGB888|RGBA8888|PNG, width, height, display, transport}`. The server scales, keeps the aspect ratio and never exceeds the device resolution.
  - Response: `Image{format(+rotation: SkinRotation + axis degrees), image bytes, seq (gaps = dropped frames), timestampUs}`.
  - Frames are sent only on change (polled every ≤ 125 ms), and the first frame may be 0×0.
  - (I) **Treat rows as top-down** (Android Studio does) despite the proto's "bottom up"; verify in a spike.
  - gRPC's default receive limit is 4 MiB, so raise it to ≥ w×h×3 + 1 KiB.
  - Android Studio uses **in-band RGB888 sized to the view** and re-requests on rotation or size change.
- (P/U) MMAP transport (optional later):
  - A handle "x" makes the emulator open the named page-file section "SHM_x". The client must pre-create it at worst-case size (Python `mmap.mmap(-1, size, tagname='SHM_x')`) and keep it open.
  - There is no synchronisation, so frames can tear.
  - It is untested end to end on Windows, and Android Studio does not use it.
- (P) Input:
  - Use one long-lived `streamInputEvent`.
  - Mouse → `MouseEvent{x,y,buttons=1 press/drag, 0 lift}` in **natural (unrotated) panel pixels**; inverse-rotate in landscape.
  - Multi-touch: TouchEvent ids 0–9, pressure 0x400/0.
  - **The classic engine never releases contacts when a stream closes** (120 s expiry, never with NEVER_EXPIRE). Always send the release on mouse-up, leave, focus loss, exit and reconnect.
  - Do not mix mouse and touch within one gesture. WheelEvent dy = 120 per notch.
- (P) Keys: `KeyboardEvent{key ∈ 'GoBack'|'GoHome'|'AppSwitch'|'Power'|'AudioVolumeUp'|'AudioVolumeDown'|'Paste', eventType=keypress}`. eventType defaults to keydown, which is never released, so always set it. 'Home' means the text-cursor Home key.
- (P) **Hangul text cannot be typed through key injection** (`KeyboardEvent.text` handles printable ASCII only; scrcpy INJECT_TEXT and `adb shell input text` are ASCII too, and AOSP LatinIME has no Korean). Path for Korean:
  1. The user composes the text with the host Windows IME in a Qt input box.
  2. Call `setClipboard(ClipData{text})`.
  3. Wait about 100 ms.
  4. Send `KeyboardEvent{key:'Paste', keypress}`, with Ctrl+V as fallback.

  This overwrites the guest clipboard.
- (P) Sensors:
  - `setSensor(ACCELERATION,[x,y,z])` overrides the sensor until any `setPhysicalModel` call.
  - App Inventor's Shaking fires when |current − mean(last 10)| on any axis falls in the band for the sensitivity: Moderate (default) 13–20, Weak > 20, Strong 5–13 m/s². MinimumInterval is 400 ms, and the sensor is read at SENSOR_DELAY_GAME.
  - Shake button: hold a baseline ≥ 200 ms, spike about 16 m/s² on the gravity axis for about 60 ms, return to baseline, repeat no sooner than 400 ms, then call `setPhysicalModel` to restore.
  - Rotation: `setPhysicalModel(ROTATION, …, STEP)`. GPS: `setGps`. Light/proximity/pressure: `setPhysicalModel`. Battery: `setBattery`.
- (P) Python client: grpcio 1.85.0, grpcio-tools 1.85.0, protobuf 7.36.2 (cp314 wheels).
  - Generate stubs at build time from **vendored protos of the pinned emulator build**, ship the `*_pb2.py` files and fix their top-level imports.
  - Google's aemu-grpc client is not on PyPI, so write a small in-house discovery + token client (about 100 lines) or vendor it (Apache-2.0, NOTICE).
  - Android Studio's Running Devices code (Kotlin, Apache-2.0) is the reference implementation.
- (P) Version skew: pin the emulator build ID, vendor the matching protos, smoke-test every upgrade, and handle UNIMPLEMENTED / INVALID_ARGUMENT.

## 5. scrcpy (secondary)
- (P) Version 5.0.1 (2026-10-08) is Apache-2.0, has a 733,930 B server jar and requires an exact client/server version match. The 4.0 release changed the wire format, and no maintained Python client supports 4.0+.
- (P) On the emulator, encoding runs in the guest with software `c2.android.avc.encoder` (≤ 2048 px, ≤ 12 Mbps), competing with the app for vCPUs.
- (P) It has no sensor, GPS or battery injection.
- (P) Decoding needs PyAV 19.0.2, which bundles GPL x264/x265 FFmpeg (about 70 MB), a licensing concern.
- **Use only as a later backend for physical phones.**

## 6. MIT App Inventor APKs (current release nb207, 2026-10-28)
- (P) SDK levels:
  - **targetSdk 36** (since nb206, Aug 2026); default minSdk 14.
  - **AndroidMinSdk is selectable 14–36 since Jul 2026** (FilePicker needs ≥ 19), so **only an API 36 image installs every current AI2 APK** (otherwise INSTALL_FAILED_OLDER_SDK).
- (P) Legacy APKs:
  - targetSdk was 7 before nb172 (Dec 2018), so those APKs are blocked on Android 14+ (INSTALL_FAILED_DEPRECATED_SDK_VERSION). Bypass with `adb install --bypass-low-target-sdk-block`.
  - Suppress the old-SDK dialog with `setprop debug.wm.disable_deprecated_target_sdk_dialog true` (userdebug images).
  - Era map (targetSdk → release): 26 = nb172–177, 28 = nb178–183, 29 = nb184–186, 30 = nb187–189, 31 = nb190–193, 33 = nb194–197, 34 = nb198–201, 35 = nb202–205, 36 = nb206+.
- (P) Manifest:
  - The package is `appinventor.ai_<email-local>.<Project>` (ASCII). **The launcher is always `<pkg>/.Screen1`** (relative name) with MAIN+LAUNCHER. Other screens use fully qualified names, are exported, and have MAIN only.
  - application = `com.google.appinventor.components.runtime.multidex.MultiDexApplication` (the Companion uses `ReplApplication`).
  - The label is a literal string (can be Hangul). The icon is `@mipmap/ic_launcher`, so read `res/mipmap-xxxhdpi-v4/ic_launcher.png` directly.
  - Parser traps: a UTF-16 string pool, `<queries>` intent actions placed **before** `<application>`, and possible duplicate components.
  - Signatures are v1+v2+v3 (old builds v1 only, which is fine for target < 30).
- (P) Native code: standard components produce pure DEX with no `lib/`. Extensions can add .so files since nb203 (AAR JNI) or hide ELF files in `assets/`. Scan both and compare with `getprop ro.product.cpu.abilist`.
- (P) Size: an empty app is about 3.3–3.5 MB with 2–3 dex files.
- (P/I) What works where:
  - AOSP default suffices for UI, Canvas, Clock, TinyDB/File, Web, TinyWebDB, CloudDB, FirebaseDB, Map (osmdroid/OSM tiles), Navigation, LocationSensor (LocationManager + setGps), all sensors, Sound/Player/VideoPlayer, Camera/Camcorder (Camera2), ImagePicker, Sharing, Texting/PhoneCall (emulated; inbound via sendSms/sendPhone) and WebViewer.
  - BarcodeScanner works only with UseExternalScanner=false; the default (true) needs a ZXing app, otherwise error 1501.
  - A Google image or sideloaded APKs is needed for TextToSpeech, SpeechRecognizer, Geocoder address blocks and Korean typing. SpeechRecognizer's default UseLegacy=true calls RECOGNIZE_SPEECH without catching ActivityNotFoundException, so the app errors without a provider; it also needs the mic.
  - Impossible: NFC, real Bluetooth (NXT/EV3/HC-05) and USB Serial.
- (P) Beginner apps define the must-haves:
  - Hello Purr (Sound + Vibrate), TalkToMe (**TTS + Shaking**), Fake Voices (TTS + SpeechRecognizer), PaintPot (Canvas Touched/Dragged), MoleMash, BallBounce (Flung).
  - So the GUI needs TTS, a shake control, a dense single-pointer DOWN/MOVE/UP stream with real timestamps (for fling velocity), audio, camera, mic and location.
  - MIT's own tutorial says "The emulator can not respond to shaking!", which makes a Shake button a differentiator.
  - Back must arrive as KEYCODE_BACK, which raises Screen.BackPressed.
  - The frame must rotate: orientation defaults to unspecified, and BarcodeScanner's capture activity is landscape.
- (I) There is no gRPC signal for guest vibration (logcat parsing would be needed).
- (P/I) Future "Companion mode":
  - Install `MITAI2Companion.apk` 2.81u (18.4 MB, includes x86_64 WebRTC) into the AVD and connect with `am start -a android.intent.action.VIEW -d aicompanion://comp/<CODE>` (WebRTC via rendezvous/TURN, untested).
  - Alternatively, serve aiStarter-compatible endpoints on `127.0.0.1:8004` (`/ping/`, `/echeck/`, `/ucheck/`, `/start/`, `/replstart/<dev>`, `/reset/`, `/emulatorreset/`) using our own adb.
  - MIT's own Windows emulator bundle (emulator 30.3.5 + API 30, about 1.5 GB) is marked "Not Recommended" and often fails on Windows 11. nb207's "Test in Web" (beta) runs projects, not APKs.
- (P) Intake of other file types: an .aab needs bundletool + Java (explain to the user); .aia and .aix are a project source and an extension (explain).

## 7. Host stack
- (P) Python:
  - **Python 3.14 install-manager runtime.** Not the Store 3.13: new top-level AppData folders it creates are invisible to emulator.exe/adb.exe children, and Nuitka refuses it. Not 3.11: too old for the current wheels.
  - Decode child output as UTF-8 (or run with `-X utf8`), because the locale encoding is cp949.
- (P) cp314/abi3 win_amd64 wheels:
  - Qt: **PySide6-Essentials 6.12.0** (82 MB, LGPL; the meta-package pulls 132 MB of WebEngine, so avoid it).
  - gRPC and data: grpcio 1.85.0, protobuf 7.36.2, numpy 2.5.4.
  - Optional: av 19.0.2 (GPL codecs bundled).
  - APK parsing: **apkInspector 1.3.7** (pure Python, no deps, Apache-2.0; `apkInspector.axml.parse_apk_for_manifest()` returns XML text). androguard 4.1.4 has 36 deps and 23 MB, so use it in dev only; pyaxmlparser is stale.
  - adbutils 2.12.0 (MIT; deps requests + Pillow; honors `ANDROID_ADB_SERVER_PORT` and `ADBUTILS_ADB_PATH`).
  - qasync 0.28.0 predates 3.14 (deprecation warnings).
  - PyInstaller 6.22.3 (3.14 OK; its exception permits non-free apps). Nuitka would need MSVC (absent).
- (P) PySide6 rendering:
  - A QImage over a Python buffer is zero-copy (GIL-safe cleanup). The fast raster formats are RGB32, ARGB32_Premultiplied and RGB16.
  - **Request frames at the widget's physical pixel size and blit 1:1 in a plain QWidget.paintEvent.**
  - Avoid QOpenGLWidget (whole-window GL composition, WGL driver dependency, Mirage driver).
  - QVideoWidget (Addons, +40 MB) only makes sense for a YUV path.
  - **QtAsyncio is a technical preview with no subprocess or socket support, so avoid it.** Use QProcess plus worker threads plus queued signals (latest frame wins).
- (P) C# alternative:
  - .NET 10 LTS: SDK 10.0.403, 215 MB, installs to C: unless dotnet-install targets D:.
  - WPF fits best; Grpc.Net.Client 2.85.0; AdvancedSharpAdbClient 3.6.16.
  - .NET AXML parsers are weak or stale. WPF cannot be trimmed (self-contained ≈ 60–80 MB).
- (P/I) Packaging:
  - PyInstaller onedir + Inno Setup. A widgets-only bundle is about 55–70 MB; the installer about 20–30 MB.
  - LGPL: keep the Qt DLLs replaceable.
  - Expect AV false positives (AhnLab V3).
  - Code signing: Azure Artifact Signing is unavailable to Korean individuals, so use an OV cert, SignPath (OSS) or unsigned personal builds.
- (P) ADB:
  - Ship pinned platform-tools 37.0.1 (adb server version 41) and run a **private adb server** through `ANDROID_ADB_SERVER_PORT`. adb.exe, adbutils and emulator.exe all honor it; the emulator self-registers via `host:emulator:<port>`.
  - Every adb server scans 5555–5585 at startup, so the console on 5586–5682 keeps foreign servers off our device. `ADB_LOCAL_TRANSPORT_MAX_PORT` can extend our own server's scan.
  - Never run kill-server on 5037. Always pass `-s emulator-<port>`.
  - (U) Whether the emulator re-registers when our private adb server restarts is unknown; fall back to `adb connect 127.0.0.1:<adbport>` (which changes the serial) or the extended scan.

## 8. Alternatives (rejected, with reason)
- WSA: removed on 2025-03-05.
- Upstream QEMU + Android-x86/BlissOS: stale, and no virgl on a Windows host.
- Genymotion: VirtualBox needs Hyper-V off, its QEMU backend is experimental, and it is commercial.
- Waydroid in WSL2: the WSL kernel has no binder.
- Cuttlefish: Linux/KVM only.
- Play Games Developer Emulator: licensed for games only.
- Robolectric Simulator / web previews: they need sources. Running YAIL/Kawa outside Android is a non-goal.
- MIT's own Windows emulator bundle is itself Google's emulator.

## 9. Open questions that only a spike on this PC can settle
1. Which image tag (google_apis vs playstore, API 36) has a TTS engine with ko-KR, a speech recognizer and Gboard Korean.
2. Whether headless `-gpu host` renders on the GTX 1660 with the Mirage adapter present; fps, latency and CPU of in-band RGB888 at 540×960 and 720×1280; row order.
3. Whether audio from headless qemu reaches the speakers on this PC.
4. Cold and quickboot times, idle RAM, and disk growth (are the images sparse?).
5. accel-check output before and after enabling WHPX; whether this Education PC grants admin rights / policy; AhnLab V3 interference.
6. Which PID appears in pid_*.ini; which shutdown path saves quickboot reliably.
7. Whether injected samples trigger Shaking reliably; whether auto-rotate is on by default.
8. Clipboard-paste Korean in an AI2 TextBox and WebViewer; any toasts.
9. Whether the emulator re-registers when the private adb server restarts.
