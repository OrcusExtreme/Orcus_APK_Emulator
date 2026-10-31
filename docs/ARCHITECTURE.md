# Orcus APK Emulator — 아키텍처 명세 (Phase 1, v3)

- 상태: **Phase 1 승인됨** (2026-10-30, ★D0–★D4 권장값). Phase 2 스파이크 결과에 따라 고친다.
- v2 변경: 독립 검토자 4명과 검증자 4명이 적대적 검토를 했다. 66건이 나왔고 반박된 것은 0건이다. 추적 표는 §16에 있다.
- v3 변경: v2 수정을 다시 점검했다(반영 범위, 회귀, 새 주장 사실 확인으로 52건). 상태 기계, epoch, nonce, 골든 로드 실패, adb 재등록, 화면 크기 등을 고쳤다(§15.3, §16). 최종 점검 29건(잠금 없는 FAILED, 조기 종료 분류 순서, 준비 단계의 adb 준비 등)도 반영했다.
- 읽는 사람: Phase 2·3 구현자(사람 또는 Claude). 결정 요약과 승인 요청은 [plan.md](../plan.md), 조사 근거는 [research/phase1-research-brief.md](research/phase1-research-brief.md)에 있다.
- 검증 표기:
  - **(P)** 공식 문서·소스로 확인했다.
  - **(I)** 추론이다.
  - **(U)** 검증하지 않았다. Phase 2 스파이크 S0–S9에서 확정한다.
  - 스파이크 결과가 이 문서와 다르면 문서를 고치고 plan.md 진행 기록에 남긴다.
- 단계 표기: 각 기능이 언제 들어오는지 **[P2]** Phase 2, **[P3a]** / **[P3b]** Phase 3 전반 / 후반, **[P4]** Phase 4로 적는다. 표기가 없으면 plan.md §3의 단계별 산출물 목록을 따른다.

---

## 0. 설계 원칙

1. **Android를 다시 만들지 않는다.** Google Android Emulator(QEMU 기반)를 창 없이 띄우고, 에뮬레이터 자체 gRPC API로 조종한다.
2. **가볍게.** 상주 프로세스는 GUI, private adb server, `emulator.exe`, `qemu` 4개다. 에뮬레이터 패키지의 `netsimd`·`crashpad_handler`가 더 뜨는지는 S4에서 측정한다. 런타임 의존성도 PySide6-Essentials, grpcio, protobuf, apkInspector 4개뿐이다. 디스크는 Google 이미지 때문에 BlueStacks 최소 사양보다 많이 쓴다(plan.md §1.7).
3. **빠르게.** 매 세션 **골든 스냅샷**에서 부팅한다(목표 ≤ 10 s, U). 창이 뜨면 바로 엔진을 예열하고, APK 복사·파싱은 부팅과 병렬로 진행한다.
4. **분리.** Core(engine / device / apk / setup)는 Qt를 모른다. 패키지 사이에서 쓰는 타입은 표준 라이브러리만 쓰는 `orcus.contracts` 하나에서 정의한다. 이 규칙은 AST 테스트와 pyright로 강제한다.
5. **이 PC에 맞춤.** 하이퍼바이저는 WHPX만 쓴다. 큰 데이터는 전부 `D:\OrcusAPK`(ASCII 경로, Drive 밖)에 둔다. 한글·Drive 경로는 스테이징 복사로 격리하고, 문자 인코딩은 cp949 대신 UTF-8을 쓴다.
6. **버전 고정.** 에뮬레이터 build 16428233, platform-tools 37.0.1, 이미지 r7, 해시로 잠근 휠을 쓴다. 자동 업데이트는 하지 않는다. 업그레이드는 스모크 테스트를 통과해야 한다.
7. **먼저 검증하고 만든다.** Phase 2는 골격, 설치 스크립트, 그리고 버리는 스파이크 하네스로 가정을 검증하는 단계다. 엔진 본체는 스파이크 결과를 반영해 Phase 3에서 만든다.

---

## 1. 시스템 개요

### 1.1 구성도

```
+-- OrcusAPK.exe : Python 3.14 + PySide6, one process, inside a KILL_ON_JOB_CLOSE Job --+
|                                                                                       |
|   gui (only Qt importer) <--queued signals-- bridge <-- events (worker threads)       |
|        | non-blocking calls                                                           |
|        v                                                                              |
|   SessionApi (app.Session)                                                            |
|     |-- ApkService    (apk)    --> DeviceShell (adb CLI, epoch-bound) -----+          |
|     |-- EngineControl (engine) --> process, job, ports, discovery, snapshots,         |
|     |                              its own gRPC channel (status/notify/snapshots)     |
|     '-- DeviceSession (device) --> its own gRPC channel: frames, input,  -+--+       |
|                                    sensors, logcat (built by an injected   |  |       |
|                                    factory from DeviceEndpoint)            |  |       |
+---------------------------------------------------------------------------|--|-------+
                                                                            |  |
   [child] adb.exe -P 15037 nodaemon server  <------------------------------+  |
              | adb transport :5641                                            | localhost:8640
              v                                                                | Bearer token
   [child] emulator.exe -avd orcus36 -no-window -gpu host ...                  |
              '--> [grandchild] qemu-system-x86_64-headless.exe  <-------------+
                     Android 16 / API 36 google_apis x86_64, WHPX, host GPU (GTX 1660)
```

### 1.2 프로세스 모델

| 프로세스 | 시작 주체 | 수명 | 종료 보장 |
|---|---|---|---|
| `OrcusAPK.exe` (개발 중에는 `python.exe`) | 사용자 | 앱 | Job 핸들의 유일한 소유자 |
| `adb.exe -P 15037 nodaemon server` | engine (에뮬레이터보다 **먼저**) | 앱 | Job 소속. 종료 시 PID로 TerminateProcess. 5037은 절대 건드리지 않는다 |
| `emulator.exe` → `qemu-system-x86_64-headless.exe` | engine | 엔진 세션 | 정상 정지 → (시간 초과 시) qemu → emulator 순 TerminateProcess → 최후에 Job |
| (U) `netsimd.exe`, `crashpad_handler.exe` | emulator | 엔진 세션(netsimd는 유휴 15 s 뒤 스스로 종료한다는 기록이 있다) | Job 소속인지 S4/S6/S9에서 확인한다. 정지 후 남으면 우리 SDK 경로의 이미지만 종료한다 |
| 단발 `adb.exe` 클라이언트, `emulator-check.exe`, `powershell.exe`(WHPX 상태 조회) | 작업 스레드 | 수 초 | 호출마다 타임아웃(§5.5 실행기), 넘으면 TerminateProcess |
| `adb.exe logcat -v threadtime -T 1` (§7.8) | engine(`EngineManager.logcat()`), apk가 epoch마다 하나 | epoch | Job 소속. DeviceSession을 닫을 때(SESSION_INIT·STOPPING·FAILED) 종료 |
| `dism.exe` (WHPX 켜기, 1회) | setup, `ShellExecuteExW` runas | 1회 | 핸들로 기다린 뒤 종료 코드를 읽는다. **권한 상승한 dism도 호출자의 Job을 물려받는다**(S0a 실측: `IsProcessInJob`=true, 2026-10-30). CLI setup은 첫 SDK 자식을 띄우는 7단계에서야 우리 Job을 만들므로 3단계의 dism은 우리 KILL_ON_JOB_CLOSE와 무관하다(개발 중에는 venv 런처의 Job 안이다). [P4] GUI 마법사는 Job 안에서 dism을 띄우게 되므로, GUI가 죽으면 dism도 죽는다 → BREAKAWAY 도우미 프로세스로 띄우거나, dism이 끝날 때까지 창을 닫지 못하게 한다 |

**Job Object 규칙**
- GUI와 엔진을 쓰는 CLI는 시작 직후, 자식을 만들기 전에 다음 순서로 호출한다. `orcus-cli setup`은 첫 SDK 자식(7단계 `emulator-check`)을 띄우기 직전에 한다:
  1. `CreateJobObjectW`
  2. `SetInformationJobObject(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)` — **BREAKAWAY_OK는 기본으로 켜지 않는다**
  3. `AssignProcessToJobObject(GetCurrentProcess())`
- Job 핸들은 상속 불가로 둔다(기본값). 이후 모든 자식이 자동으로 Job에 들어가므로, `emulator.exe`가 qemu를 띄우는 순간과도 경합하지 않는다(`emulator.exe`만 죽이면 qemu가 남는다, P).
- **중첩 Job:** venv 실행기나 터미널의 상위 Job 안에서도 자기 할당은 성공한다. 브레이크어웨이는 직속 Job이 허용할 때만 일어난다(P, MSDN nested jobs).
- **`TerminateJobObject` 금지:** 우리 프로세스도 그 Job 안에 있다. 강제 종료는 `JobObjectBasicProcessIdList` 또는 discovery로 PID를 얻어 **qemu → emulator.exe** 순으로 `TerminateProcess`한다.
- **자기 할당이 실패하면:** `emulator.exe`를 만든 직후 그 프로세스만 할당하고, discovery 뒤 `IsProcessInJob(qemu)`를 확인해 경고를 남긴다.
- **보조 프로그램(폴더·로그·도움말 URL 열기):** `explorer.exe "<경로 또는 URL>"`를 CreateProcess로 실행한다. explorer는 실행 중인 셸에 요청을 넘기고 바로 끝나므로 브레이크어웨이가 필요 없다. 이 동작은 문서화되어 있지 않아 S9에서 확인한다(특히 URL과 '폴더 창을 별도 프로세스로' 설정).
  - 안 되면 그때만 BREAKAWAY_OK + `CREATE_BREAKAWAY_FROM_JOB`을 쓰고, 에뮬레이터 자손이 빠져나가지 않는지 §5.7의 프로세스 점검으로 감시한다.
  - `QDesktopServices`, `os.startfile`, `ShellExecute`는 브레이크어웨이 플래그를 줄 수 없으므로 쓰지 않는다.

### 1.3 소유권과 단일 인스턴스

- **엔진 소유권 잠금(GUI·CLI·테스트 공통).**
  - VM을 띄우는 프로세스는 `D:\OrcusAPK\state\engine.lock`을 배타적으로 열어 VM 수명 동안 쥔다. 프로세스가 죽으면 OS가 풀어 준다.
  - 같은 프로세스가 `state\engine_owner.json`(pid, 시작 시각, 종류 gui|cli|detached, 포트, adb 서버 pid)을 쓴다.
  - 파일 잠금이라 Windows 계정이 달라도 동작한다. `D:\OrcusAPK`는 모든 계정이 공유한다.
- **잠금을 얻지 못하면:** 다른 Orcus나 orcus-cli가 VM을 쓰고 있는 것이다.
  - GUI와 `run`/`smoke`/기기 테스트는 `ENGINE_IN_USE`("다른 Orcus가 휴대폰을 사용 중입니다")로 멈춘다.
  - `orcus-cli engine status`/`shot`만 소유자 기록으로 읽기 전용 접속을 한다.
- **고아의 정의:** 우리가 잠금을 쥐었는데도 우리 AVD의 VM이 살아 있으면 그것이 고아다. 예외는 소유자 기록이 `detached`인 경우다(**[P4]** 개발용 detached 모드 → attach). 이 판정 규칙 덕분에 GUI와 CLI가 서로의 VM을 죽이지 않는다.
- **외부 정지:** 소유자가 요청하지 않은 종료라도 게스트 전원 끄기(전원 메뉴의 종료)는 크래시가 아니라 STOPPED로 분류한다(§5.7). **[P4]** detached VM을 `orcus-cli engine stop`이 끌 때는 소유자 기록에 `external_stop`을 남기고, 소유자도 이것을 STOPPED로 분류한다.
- **GUI 단일 인스턴스.**
  1. `__main__`이 app.Session을 만들기 **전에** named mutex `Local\OrcusAPK.Instance`를 확인한다.
  2. 이미 있으면 사용자·세션별 이름의 QLocalServer(`OrcusAPK.ipc.<사용자 SID>`, `UserAccessOption`)에 최대 3 s 동안 재시도하며 접속해 파일 인자를 넘기고 끝낸다.
  3. 접속하지 못하면 "Orcus가 이미 실행 중입니다"를 표시한다.
  - 리스너는 `gui/instance.py`에 있고 `SessionApi.open_paths()`를 부른다.
- **Windows 로그오프·종료.** `gui/instance.py`의 `commitDataRequest`/`aboutToQuit`가 `SessionApi.shutdown(fast=True)`를 부른다.
  - 빠른 정지는 2 s 안에 끝내며 **절대 스냅샷을 저장하지 않는다.** 남은 프로세스는 강제 종료한다.
  - 이 Qt 연결은 gui에만 있고 app에는 없다(레이어 규칙).

---

## 2. 패키지 구조와 의존 규칙

```
src/orcus/
  contracts/  stdlib only. enums, frozen dataclasses, Protocols, events, errors (OrcusError/OrcusFailure)
  common/     paths(+guards) config pins avd childenv accel(WHPX state, emulator-check) proc(runner)
              winjob(job, locks, process scan) ports messages_ko log jsonio clock
  emugrpc/    vendored 37.2.12 .proto + generated *_pb2*.py + channel factory + fake.py (FakeEmulatorServer)
  setup/      repo_index license download extract whpx steps (SetupApi implementation, step model)
  engine/     manager args discovery adb snapshots provision gpu seams bootcheck selftest  (1) Core Engine
  device/     session frames input controls logcat keymap shake rotation  gRPC control-plane adapter
  apk/        intake inspect compat install launch watcher eras service   (2) APK Lifecycle & Automation
  app/        compose (P2: setup API, selftest, boot-check), session (SessionApi / HeadlessSession)
  gui/        main_window phone_view frame_pump input_mapper bezel panels/ text_bar overlays
              dialogs/ wizard/ (P4) instance bridge keymap               (3) Host GUI Client
  __main__.py GUI entry: mutex check -> builds app.Session -> injects it into gui
  cli.py      orcus-cli (headless): setup | doctor | engine | run | smoke | selftest
```

**요구사항의 세 컴포넌트와의 대응**

| 요구 컴포넌트 | 패키지 | 책임 |
|---|---|---|
| (1) Core Engine Manager | `engine` (설치는 `setup`, 통신 기반은 `emugrpc`) | VM 수명주기, 포트, private adb, discovery, 스냅샷, 기기 준비 |
| (2) APK Lifecycle & Automation | `apk` | 파일 판정·스테이징, 매니페스트(package + launcher), 사전 점검, `pm install`, `am start`, 감시 |
| (3) Host GUI Client | `gui` (화면·입력 어댑터는 `device`) | 폰 프레임, 9:16 실시간 화면, 마우스→터치, Back/Home/Recents, 드래그앤드롭/파일 선택 |
| 접착 | `contracts`, `app` | 인터페이스 정의, 조립 |

**import 규칙: 허용 목록 방식.** `tests/unit/test_layering.py`가 `ast`로 검사한다. 표준 라이브러리(`sys.stdlib_module_names`)는 항상 허용하고, 표에 없는 것은 전부 금지한다.

| 패키지 | 허용 import |
|---|---|
| `contracts` | (표준 라이브러리만) |
| `common` | `contracts` |
| `emugrpc` | `common`, `contracts`, grpc, protobuf |
| `setup` | `common`, `contracts` |
| `engine` | `common`, `contracts`, `emugrpc`, grpc, protobuf |
| `device` | `common`, `contracts`, `emugrpc`, grpc, protobuf |
| `apk` | `common`, `contracts`, apkInspector |
| `app` | `contracts`, `common`, `setup`, `engine`, `device`, `apk` |
| `gui` | `contracts`, `common`, PySide6 |
| `__main__` | `app`, `gui`, `common` |
| `cli` | `app`, `contracts`, `common` |

- **타입 경계:** **pyright**(contracts와 패키지 경계는 strict)로 강제한다. 테스트에 `_: EngineControl = EngineManager(...)` 같은 대입 검사를 둔다.
- **GUI의 엔진 접근 범위:** GUI는 `SessionApi.engine`을 `EngineView`(비차단 메서드만) 타입으로만 본다.

**소유 규칙** (리뷰 체크리스트)
- 고정 버전(build id, URL, 크기, SHA-1)은 `common/pins.py` 한 곳에만 있다. vendored proto 폴더 이름이 pin과 같은지 유닛 테스트한다.
- 에뮬레이터 플래그 문자열은 `engine/args.py`만 안다. 순수 함수이며 불변식은 §5.2에서 테스트한다.
- 자식 프로세스 환경과 AVD 파일 내용은 `common/childenv.py`, `common/avd.py`만 만든다. setup과 engine이 같이 쓴다.
- 사용자에게 보이는 문자열과 `ErrorCode` → 메시지·해결 동작·피드백 채널 표는 `common/messages_ko.py` 한 곳에 둔다(Qt 없음). GUI와 CLI가 같이 쓴다.
- WHPX 상태 조회와 `emulator-check accel` 실행·파싱은 `common/accel.py` 한 곳에 둔다. setup(설치)과 engine(PREFLIGHT)이 같이 쓴다.
- **[P2] 최소 조립:** `app/compose.py`(setup API 만들기, `--selftest`, `--boot-check`), `engine/bootcheck.py`, `engine/selftest.py`. 기본 `ChannelFactory`는 engine이 제공한다(engine은 emugrpc를 import할 수 있다).
- 앱 수명주기 셸 명령(`install`, `am`, `pm clear/grant/uninstall`, `force-stop`, `pidof`, `dumpsys window`)은 `apk`만 안다.
- 기기 준비 명령(`settings`, `setprop`, `svc`, `locksettings`, 기능 탐지)은 `engine/provision.py`만 안다.
- proto 메시지는 `emugrpc`·`engine`·`device`만 안다. GUI는 serial, 포트, 토큰을 보지 못한다.
- **engine 내부 seam:** `ProcessHost`, `JobControl`, `DiscoverySource`, `AdbRunner`, `PortProber`, `Clock`, `ChannelFactory`(`engine/seams.py`). EngineManager는 이것들을 주입받아서만 만든다. 그래서 상태 기계를 가짜 객체로 테스트할 수 있다.
- **다른 백엔드:** 나중에 scrcpy·실기기를 붙여도 `device`의 `DeviceSession` 구현만 바꾸면 된다. 센서를 못 하는 백엔드는 `controls=None`이고, GUI는 해당 패널을 숨긴다.

---

## 3. 핵심 인터페이스 (`orcus.contracts`, 표준 라이브러리만) — Phase 2에서 전부 정의

```python
# errors.py ---------------------------------------------------------------
class ErrorCode(StrEnum):
    # SETUP_ 설치
    SETUP_PATH = auto(); SETUP_DISK = auto(); SETUP_NETWORK = auto(); SETUP_CHECKSUM = auto()
    SETUP_LICENSE_CHANGED = auto(); SETUP_LICENSE_NOT_STABLE = auto()
    SETUP_UAC_DENIED = auto(); SETUP_DISM_FAILED = auto(); SETUP_REBOOT_PENDING = auto()
    SETUP_WHPX_REVERTED = auto(); SETUP_QUARANTINE = auto(); SETUP_ACCEL_FAILED = auto()
    # PRE_ 실행 전 점검
    PRE_NOT_INSTALLED = auto(); PRE_WHPX_OFF = auto(); PRE_SDK_MISSING = auto()
    PRE_DISK = auto(); PRE_PATH = auto(); PRE_PORTS = auto()
    # ENGINE_ 에뮬레이터
    ENGINE_IN_USE = auto(); ENGINE_EXIT = auto(); ENGINE_AVD_LOCKED = auto()
    ENGINE_CONNECT_TIMEOUT = auto(); ENGINE_BOOT_TIMEOUT = auto(); ENGINE_GOLDEN_LOAD_FAILED = auto()
    ENGINE_RESET_FAILED = auto(); ENGINE_PROVISION_FAILED = auto(); ENGINE_GPU_SOFTWARE = auto()
    ENGINE_ADB_LOST = auto(); ENGINE_CRASHED = auto(); ENGINE_UNAVAILABLE = auto(); ENGINE_NO_FRAMES = auto()
    ENGINE_DISK_LOW = auto()
    # FILE_ / STAGE_ 드롭한 파일
    FILE_NOT_ANDROID = auto(); FILE_IS_AAB = auto(); FILE_IS_AIA = auto(); FILE_IS_AIX = auto()
    STAGE_DRIVE_TIMEOUT = auto(); STAGE_COPY_FAILED = auto(); APK_PARSE = auto()
    # INSTALL_ 앱 설치
    INSTALL_OLDER_SDK = auto(); INSTALL_NO_ABI = auto(); INSTALL_INCOMPATIBLE = auto()
    INSTALL_STORAGE = auto(); INSTALL_INVALID = auto(); INSTALL_OTHER = auto()
    # APP_ 실행 중
    APP_LAUNCH_NOT_FOUND = auto(); APP_CRASH = auto(); APP_ANR = auto(); APP_DIED = auto()
    APP_AI_COMPONENT_ERROR = auto(); APP_AI_RUNTIME_ERROR = auto()
    APP_TTS_MISSING = auto(); APP_SPEECH_MISSING = auto()

class RemedyAction(StrEnum):
    RETRY = auto(); ENABLE_WHPX = auto(); COPY_IT_REQUEST = auto(); REPAIR_SETUP = auto()
    OPEN_LOG = auto(); RESET_PHONE = auto(); RUN_AGAIN = auto(); GRANT_ALL = auto()
    CHANGE_GPU_MODE = auto(); FREE_DISK = auto(); CONTINUE_ANYWAY = auto()

@dataclass(frozen=True)
class Remedy: action: RemedyAction; label_key: str

@dataclass(frozen=True)
class OrcusError:                       # 주 해결책은 하나. [로그]/[자세히]/[보고서 복사]는 GUI가 채널별로 붙인다
    code: ErrorCode; message_key: str; detail: str = ""
    remedy: Remedy | None = None; log_excerpt: str = ""

class OrcusFailure(Exception):          # 패키지 경계를 넘는 유일한 예외. grpc·subprocess 예외는 안에서 변환한다
    error: OrcusError
class EngineNotReady(OrcusFailure): ...

# engine.py ---------------------------------------------------------------
class EngineState(StrEnum):
    NOT_INSTALLED = auto(); STOPPED = auto(); PREFLIGHT = auto(); LAUNCHING = auto()
    CONNECTING = auto(); BOOTING = auto(); SESSION_INIT = auto(); READY = auto()
    RESETTING = auto(); PROVISIONING = auto(); STOPPING = auto(); FAILED = auto()

class SessionMode(StrEnum): GOLDEN = auto(); COLD = auto(); KEEP_STATE = auto()   # KEEP_STATE는 [P4]
class GpuMode(StrEnum): HOST = "host"; HOST_HIDDEN = "host_hidden"; SOFTWARE = "software"  # [P4] LADDER
class EpochCause(StrEnum):
    BOOT = auto(); RESET = auto(); CRASH_RESTART = auto(); REBUILD = auto(); RESET_RECOVERY = auto()

@dataclass(frozen=True)
class DeviceCaps:                       # state\device_caps.json 캐시
    api: int; abis: tuple[str, ...]; panel: tuple[int, int, int]   # (w, h, dpi), getStatus.hardwareConfig
    renderer: str; gpu_mode: GpuMode; hypervisor: str; locale: str; timezone: str
    tts_engine: str | None; tts_ko: bool | None; speech_recognizer: bool; imes: tuple[str, ...]

@dataclass(frozen=True)
class EngineStatus:
    state: EngineState; text_key: str; progress: float | None
    mode: SessionMode; epoch: int        # SESSION_INIT에서만 +1(부팅 뒤·초기화 뒤 모두 지남)
    epoch_cause: EpochCause              # ApkService 재설치와 GUI 크래시 안내는 CRASH_RESTART일 때만
    display_on: bool
    warnings: tuple[ErrorCode, ...]      # READY 중 경고(ENGINE_GPU_SOFTWARE 등)는 상태가 아니라 배너
    error: OrcusError | None; caps: DeviceCaps | None

@dataclass(frozen=True)
class DeviceEndpoint:                    # GUI에는 노출되지 않음(SessionApi에 없음)
    target: str; token: str; serial: str; epoch: int

type DeviceSessionFactory = Callable[[DeviceEndpoint], "DeviceSession"]   # app이 device.connect를 주입

class EngineView(Protocol):              # GUI가 보는 표면. 모든 메서드는 명령만 넣고 바로 반환
    def status(self) -> EngineStatus: ...
    def start(self) -> None: ...
    def stop(self, *, fast: bool = False) -> None: ...      # 완료는 EngineStatus(STOPPED)로 알 수 있다
    def reset_device(self) -> int: ...                      # 목표 epoch(= 현재 + 1). COLD에서는 재구축 요청(GUI가 미리 확인)
    def rebuild(self, reason: str) -> None: ...             # 사용자 확인 뒤에만 호출
    def retry(self) -> None: ...                            # 엔진 실패 패널의 [다시 시도]. 실패 재구축 한도도 초기화(§5.1) [P3a 추가]
    def subscribe(self, cb: Callable[[EngineStatus], None]) -> Callable[[], None]: ...

class EngineControl(EngineView, Protocol):   # apk·app·cli만 사용
    def wait_ready(self, timeout_s: float, *, min_epoch: int | None = None,
                   cancel: threading.Event | None = None) -> EngineStatus: ...
        # READY이면서 epoch ≥ min_epoch일 때 반환. 취소되면 CancelledError.
        # FAILED, NOT_INSTALLED(PRE_NOT_INSTALLED), 대기 중인 start·재시작이 없는 STOPPED이면 OrcusFailure.
        # supervisor 스레드에서 부르면 assert로 실패한다
    def shell(self) -> "DeviceShell": ...      # 현재 epoch에 묶인 객체. READY가 아니면 EngineNotReady
    def session(self) -> "DeviceSession": ...  # SessionApi.device()와 같은 객체

@dataclass(frozen=True)
class CmdResult:
    argv: tuple[str, ...]; code: int; out: str; err: str; elapsed_s: float; timed_out: bool

class DeviceShell(Protocol):             # apk가 쓰는 유일한 기기 표면(adb)
    serial: str; epoch: int              # 옛 epoch의 객체를 쓰면 EngineNotReady
    def get_state(self) -> str: ...
    def shell(self, argv: Sequence[str], timeout_s: float = 30) -> CmdResult: ...
        # argv 요소마다 기기 셸용 따옴표 처리를 한다(shlex.quote). adb는 인자를 공백으로 이어 붙이기만 하므로
        # 이렇게 해야 빈 문자열('')이나 공백이 든 값이 그대로 전달된다
    def install(self, apk: Path, flags: Sequence[str], timeout_s: float = 180) -> CmdResult: ...
    def uninstall(self, package: str) -> CmdResult: ...

# device.py ---------------------------------------------------------------
class Rotation(IntEnum): PORTRAIT = 0; LANDSCAPE = 1; REVERSE_PORTRAIT = 2; REVERSE_LANDSCAPE = 3
class HwKey(StrEnum):
    BACK = "GoBack"; HOME = "GoHome"; RECENTS = "AppSwitch"; POWER = "Power"
    VOL_UP = "AudioVolumeUp"; VOL_DOWN = "AudioVolumeDown"
class Sensitivity(StrEnum): WEAK = "weak"; MODERATE = "moderate"; STRONG = "strong"

@dataclass(frozen=True, slots=True)
class Frame:                             # RGB888, 행은 위→아래(S2에서 확인), stride = width*3
    rgb888: bytes; width: int; height: int; rotation: Rotation; seq: int; ts_us: int

@dataclass(frozen=True, slots=True)
class PointerAt:                         # 표시 중인 프레임 안의 정규 좌표 + 그 프레임의 seq
    u: float; v: float; frame_seq: int   # 패널 픽셀 변환(회전, 패널 크기)은 device가 맡는다

@dataclass(frozen=True)
class LogEntry: ts_us: int; pid: int; tid: int; level: str; tag: str; msg: str

class CancellableIter[T](Protocol):      # 정상 종료·취소는 StopIteration, 실패는 OrcusFailure
    def __iter__(self) -> Iterator[T]: ...
    def cancel(self) -> None: ...

class FrameSource(Protocol):
    def start(self, width: int, height: int, on_frame: Callable[[Frame], None],
              on_end: Callable[[OrcusError | None], None]) -> None: ...
    def retarget(self, width: int, height: int) -> None: ...    # 스트림을 취소하고 다시 요청
    def stop(self) -> None: ...

class InputSink(Protocol):               # 모든 호출은 큐에 넣고 바로 반환
    def pointer(self, at: PointerAt, pressed: bool) -> None: ...
    def wheel(self, at: PointerAt, dy: int) -> None: ...
    def key(self, key: HwKey | str, *, long_press: bool = False) -> None: ...  # 항상 keypress
    def chord(self, modifier: str, key: str) -> None: ...                     # 예: ("Control", "a")
    def type_ascii(self, text: str) -> None: ...
    def paste_text(self, text: str) -> None: ...     # setClipboard → 100 ms → Paste, 순서 보장
    def release_all(self) -> None: ...

class DeviceControls(Protocol):          # 모두 device-controls FIFO 스레드에 넣고 바로 반환
    def shake(self, sensitivity: Sensitivity) -> None: ...
    def rotate(self, to: Rotation) -> None: ...
    def tilt(self, pitch_deg: float, roll_deg: float) -> None: ...
    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None: ...
    def set_microphone(self, enabled: bool) -> None: ...

class DeviceSession(Protocol):
    epoch: int
    frames: FrameSource; input: InputSink
    controls: DeviceControls | None      # 센서를 지원하지 않는 백엔드면 None
    def logcat(self) -> CancellableIter[LogEntry]: ...
    def screenshot_png(self, on_done: Callable[[bytes | None], None]) -> None: ...  # 비차단(device-io 스레드)
    def close(self) -> None: ...         # release_all → 스트림 취소 → 채널 닫기 (engine이 SESSION_INIT·STOPPING·FAILED에서 호출)

# apk.py ------------------------------------------------------------------
class FileKind(StrEnum): APK = auto(); AAB = auto(); AIA = auto(); AIX = auto(); NOT_ANDROID = auto()

@dataclass(frozen=True)
class ApkInfo:
    sha256: str; staged: Path; package: str; launcher: str | None; label: str
    launcher_orientation: str | None     # android:screenOrientation of the launcher activity
    version_code: int; version_name: str | None; min_sdk: int; target_sdk: int
    permissions: frozenset[str]; queries: frozenset[str]; features: frozenset[str]
    native_abis: frozenset[str]; asset_elf: bool
    is_app_inventor: bool; is_companion: bool; ai_era: str | None; icon_png: bytes | None

class IssueLevel(StrEnum): BLOCK = auto(); WARN = auto(); INFO = auto()
class IssueCode(StrEnum):
    HW_NFC = auto(); HW_BLUETOOTH = auto(); HW_SERIAL = auto(); NEEDS_MIC = auto(); SCANNER = auto()
    LEGACY = auto(); ARM_TRANSLATION = auto(); VIBRATE = auto(); LOCATION = auto()
    ORIENTATION_LOCKED = auto(); TTS_STATUS = auto(); SPEECH_STATUS = auto()
@dataclass(frozen=True)
class Issue: level: IssueLevel; code: ErrorCode | IssueCode; text_key: str   # BLOCK이면 ErrorCode

class RunPolicy(StrEnum): KEEP_DATA = auto(); FRESH_APP = auto(); FRESH_PHONE = auto()
class RunStage(StrEnum):
    STAGING = auto(); INSPECTING = auto(); CHECKING = auto(); WAITING_ENGINE = auto()
    INSTALLING = auto(); LAUNCHING = auto(); RUNNING = auto(); EXITED = auto()
    CRASHED = auto(); FAILED = auto(); CANCELLED = auto()

@dataclass(frozen=True)
class Prompt: run_id: str; prompt_id: str; text_key: str; choices: tuple[str, ...]

@dataclass(frozen=True)
class RunEvent:
    run_id: str; stage: RunStage; info: ApkInfo | None; issues: tuple[Issue, ...]
    problem: OrcusError | None; prompt: Prompt | None; elapsed_s: float
    pid: int | None; epoch: int

class ApkService(Protocol):              # 모든 메서드는 명령만 넣고 바로 반환
    def submit(self, path: Path, policy: RunPolicy | None = None) -> str: ...  # None이면 설정 기본값. 최신 드롭 우선
    def answer(self, run_id: str, prompt_id: str, choice: str) -> None: ...
    def cancel(self, run_id: str) -> None: ...
    def rerun(self) -> None: ...
    def stop_app(self) -> None: ...
    def clear_data(self) -> None: ...
    def uninstall(self) -> None: ...
    def grant_all(self) -> None: ...
    def match_orientation(self) -> None: ...          # [화면 방향 맞추기]
    def subscribe(self, cb: Callable[[RunEvent], None]) -> Callable[[], None]: ...
    def subscribe_log(self, cb: Callable[[LogEntry], None], *, app_only: bool = True) -> Callable[[], None]: ...

# setup.py ----------------------------------------------------------------
class SetupStep(StrEnum):
    CHECK = auto(); LICENSE = auto(); WHPX = auto(); DOWNLOAD = auto(); EXTRACT = auto()
    REBOOT = auto(); ACCEL = auto(); AVD = auto(); PROVISION = auto(); DONE = auto()

@dataclass(frozen=True)
class SetupEvent:
    step: SetupStep; state: str          # "pending" | "running" | "waiting_user" | "done" | "failed"
    progress: float | None; bytes_done: int; bytes_total: int; rate_bps: float; eta_s: float | None
    problem: OrcusError | None

@dataclass(frozen=True)
class LicenseText: license_id: str; text: str; sha256: str; packages: tuple[str, ...]

class SetupApi(Protocol):                # CLI와 [P4] GUI 마법사가 같은 단계 모델을 렌더링한다. 모든 메서드는 비차단
    def events(self) -> tuple[SetupEvent, ...]: ...
    def run(self) -> None: ...           # 사람의 결정이 필요하면 해당 단계가 waiting_user로 멈춘다
    def license(self) -> LicenseText | None: ...   # LICENSE 단계가 waiting_user일 때 run()이 미리 받아 둔 전문
    def accept_license(self, sha256: str, accepted_by: str) -> None: ...  # 사람이 명시적으로 동의할 때만. 결과는 SetupEvent
    def enable_whpx(self) -> None: ...   # UAC 요청을 보내고 반환. 결과는 SetupEvent
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def subscribe(self, cb: Callable[[SetupEvent], None]) -> Callable[[], None]: ...

# session.py --------------------------------------------------------------
@dataclass(frozen=True)
class Settings:                          # 섹션별 frozen dataclass. 각 섹션 타입은 §11.3의 키·기본값을 필드로 갖는다
    engine: "EngineSettings"; apk: "ApkSettings"; session: "SessionSettings"; input: "InputSettings"
    frame: "FrameSettings"; sensors: "SensorSettings"; privacy: "PrivacySettings"; ui: "UiSettings"

@dataclass(frozen=True)
class ApplyResult: needs_restart: bool; needs_rebuild: bool

class SessionApi(Protocol):              # GUI가 호출하는 유일한 객체. 모든 메서드는 비차단
    @property
    def engine(self) -> EngineView: ...
    @property
    def apps(self) -> ApkService: ...
    @property
    def setup(self) -> SetupApi: ...
    def device(self) -> DeviceSession | None: ...       # epoch가 바뀌면 다시 받는다
    def settings(self) -> Settings: ...
    def preview_settings(self, changes: Mapping[str, object]) -> ApplyResult: ...  # 저장하지 않고 재시작·재구축 필요 여부만
    def update_settings(self, changes: Mapping[str, object]) -> ApplyResult: ...  # 'engine.gpu_mode' 같은 점 표기 키
    def report(self) -> str: ...                        # '보고서 복사' 본문
    def open_paths(self, paths: Sequence[Path]) -> None: ...
    def shutdown(self, *, fast: bool = False) -> None: ...

class ReadOnlyEngine(Protocol):                         # 다른 프로세스가 소유한 VM을 보기만 한다
    def status(self) -> EngineStatus | None: ...
    def screenshot_png(self) -> bytes | None: ...

class HeadlessSession(SessionApi, Protocol):            # cli 전용: wait_ready/shell 등이 필요하다
    @property
    def engine(self) -> EngineControl: ...
    def attach_readonly(self) -> ReadOnlyEngine | None: ...   # `orcus-cli engine status|shot`: 소유자 기록 + discovery
```

**콜백 규칙:**
- 콜백은 생산자 스레드에서 락 밖에서 호출되며, 콜백 안에서는 **큐에 넣는 일만** 한다.
  - 예외는 프레임 콜백(`gui.frame_pump`) 하나다. `frames` 스레드에서 QImage로 감싸고 변환하는 일까지 한다. 위젯에는 손대지 않는다(§4, §9.1).
- `gui/bridge.py`가 콜백을 받아 Qt 신호(QueuedConnection)로 다시 내보낸다.
- 모든 `ErrorCode`에는 다음이 있어야 하며, 유닛 테스트로 검사한다:
  - 한국어 메시지(`common/messages_ko.py`)
  - 피드백 채널(§10.2의 코드 → 채널 표)
  - 해결 동작 처리기
- §3에 이름이 나오는 모든 타입은 `orcus.contracts`에 정의되어 있어야 한다(Phase 2 A7에서 확인).

---

## 4. 스레드 모델

| 스레드 | 소유 | 하는 일 |
|---|---|---|
| Qt main | gui | 위젯 표시, 페인트, 입력 캡처. 5 ms를 넘는 작업 금지. adb·gRPC 호출 금지. `SessionApi` 호출은 모두 비차단 |
| `engine-supervisor` | engine.manager | 엔진 상태를 바꾸는 **유일한** 스레드다. 명령 큐(start/stop/reset/rebuild)와 주입된 `Clock`이 주는 1 s tick을 처리한다. 이 스레드에서 2 s를 넘는 블로킹 호출은 금지 |
| `engine-worker` | engine | 오래 걸리는 단계를 cancel Event와 함께 실행하고 결과를 supervisor 큐로 보낸다: 부팅 대기, 준비 단계, Save/LoadSnapshot, 정지 |
| `proc-wait` ×N | engine | `emulator.exe`·qemu 핸들을 기다리다 종료되면 supervisor 큐로 이벤트를 보낸다(직접 아무것도 바꾸지 않음) |
| `engine-adbwait` | engine | READY 동안 adb 서버 핸들을 기다리다 종료되면 supervisor 큐로 이벤트를 보낸다. 재시작은 supervisor가 결정하고 `engine-worker` 작업으로 한다(§5.5) |
| `grpc-notify` | engine | `streamNotification`(부팅 완료, 화면 전원)을 받아 supervisor 큐로 보낸다 |
| `frames` | device (콜백은 gui.frame_pump) | `streamScreenshot` 수신. 크기는 `Image.format.width/height`에서 읽는다(37.2.12는 `Image.width/height`를 항상 0으로 보낸다, S2). 데이터 길이가 크기와 맞지 않는 프레임만 버린다. QImage로 감싸고 필요하면 RGB32로 바꾼다. 1칸 우편함에 최신 프레임만 두고, 페인트가 대기 중이면 신호를 생략한다 |
| `grpc-input` | device | `streamInputEvent`의 요청 제너레이터가 큐를 소비한다. 붙여넣기 순서(setClipboard → 대기 → Paste)도 여기서 보장한다 |
| `device-controls` | device | 센서 명령을 FIFO로 처리한다. 회전·기울기 상태의 원본을 가진다. 흔들기 파형(약 310 ms)은 끊지 않고 실행하며, 그동안 들어온 기울기 갱신은 합쳐서 뒤에 적용한다 |
| `apk-logcat` | device(스트림) → apk.service·watcher | epoch마다 logcat 스트림 하나(READY 때 연다. 원천은 engine의 `adb logcat` 자식 프로세스, §7.8). 줄을 링 버퍼(4,000줄)에 넣고, 지금 실행의 watcher가 앱 PID·태그로 걸러 분류한다. 발견은 `apk-pipeline` 큐로 보내므로 RunEvent는 모두 파이프라인 스레드에서 나간다 |
| `apk-pipeline` | apk.service | 실행 작업을 하나씩 처리한다. 기다릴 때는 cancel Event를 쓴다. 새 드롭이 오면 대기 중인 작업과 진행 중인 대기를 취소한다 |
| `device-io` | device | 서로 독립인 단발 읽기 호출만 처리한다(스크린샷용 `getScreenshot` 등). 센서·클립보드·스냅샷 호출은 여기서 하지 않는다 |

**규칙**
- **I/O와 락:** I/O 중에는 락을 잡지 않는다. 스레드 사이 데이터는 불변 스냅샷이나 큐로만 주고받는다.
- **타임아웃:** 모든 블로킹 호출에 타임아웃이 있다.
- **스냅샷 RPC:** Save/Load/DeleteSnapshot은 supervisor가 명령으로 내리고 `engine-worker`에서만 실행한다. apk·device·gui 스레드에서는 부르지 않는다.
- **gRPC 채널:** 2개다. engine 채널(상태, 알림, 스냅샷, keepalive)과 device 채널(프레임, 입력, 센서, logcat)이며, 각각 그 패키지의 스레드들이 공유한다(thread-safe, P).
- **종료 순서:**
  1. GUI가 구독을 해제하고 프레임 펌프를 멈춘다.
  2. apk-pipeline을 취소한다(실행 중인 adb 클라이언트는 종료).
  3. 입력에 `release_all`을 보내고 닫는다.
  4. engine을 정지한다(DeviceSession.close → SHUTDOWN → 시간 초과 시 강제 종료).
  5. adb 서버를 종료한다.
  6. 이름 있는 스레드를 타임아웃을 두고 join한다.
  7. bridge와 QApplication을 정리한다.

---

## 5. 엔진 수명주기 (Core Engine Manager)

단계: 상태 기계(§5.1), GPU 감지(§5.6), 감시(§5.7)는 **[P3a]**. 명령줄 빌더(§5.2), 포트(§5.3), 자식 환경(§5.4), 실행기(§5.5 끝), discovery 파서(§5.7)는 **[P2]**.

### 5.1 상태 기계 (전이는 `engine-supervisor`에서만) [P3a]

**공통 규칙** — 활성 상태(PREFLIGHT, LAUNCHING, CONNECTING, BOOTING, SESSION_INIT, RESETTING, PROVISIONING)에 모두 적용한다.
- **`stop`:** 진행 중인 단계를 cancel하고 STOPPING(의도 = stop)으로 간다. 준비를 중단할 때는 `provision.json`을 쓰지 않는다. CONNECTING 이전이면 프로세스를 바로 종료한다.
- **요청하지 않은 종료**(§5.7에서 크래시로 분류된 경우):
  1. 먼저 로그 끝부분을 아래 **조기 종료 분류** 표로 분류하고, 그 행의 대응을 따른다. 재시도 행이면 재시도하고, 한도를 넘었거나 `PRE_*` 행이면 그 코드로 FAILED로 간다.
  2. **골든 로드 실패**로 보는 경우는 셋이다. 이때는 STOPPING(의도 = rebuild)으로 가고, 실패 재구축 한도를 적용한다.
     - 표의 스냅샷 행에 맞는 경우.
     - GOLDEN 모드의 LAUNCHING·CONNECTING·BOOTING에서 분류되지 않은 종료가 났고, `avd\orcus36.avd\snapshots\orcus_golden`이 사라진 경우. 강제 로드 실패는 스냅샷을 지우므로 확실한 신호다(36.1 소스).
     - **GOLDEN 모드의 BOOTING이 한도(30 s)를 넘긴 경우.** S4에서 `ram.bin`이 망가진 골든을 강제 로드하자 에뮬레이터는 종료도 삭제도 하지 않고 gRPC만 연 채 멈췄다(96 s 동안 부팅 완료 없음, 로그에 오류 없음). 정상 골든 부팅은 5–6 s라 30 s 초과는 이상이다.
  3. 그 밖의 경우는 FAILED(`ENGINE_EXIT`).
- **게스트 전원 끄기**(전원 메뉴의 종료 등)는 크래시가 아니다. READY → STOPPING(의도 = stop)으로 가고, 프로세스가 이미 없으므로 SHUTDOWN은 건너뛴다. 감지 신호(종료 직전의 VM 상태·알림·로그 줄)는 S4·S6에서 정한다.
- **STOPPING은 의도에 따라 나간다:**
  - stop → STOPPED(잠금·소유자 기록 해제)
  - rebuild → PROVISIONING(REBUILD)
  - restart → LAUNCHING(잠금 유지)
  - fail → FAILED(코드). 예: 디스크가 1 GiB 아래로 내려가 빠른 정지를 한 뒤 `PRE_DISK`
  - 재시작과 재구축은 STOPPED를 거치지 않는다. 그래서 기다리는 쪽(`wait_ready`)이 중간에 STOPPED를 보지 않는다.
- **epoch는 SESSION_INIT에서만 1 오른다.** 부팅 뒤와 초기화 뒤가 모두 SESSION_INIT을 지나므로, 초기화 한 번에 정확히 1 오른다.
  - 오른 이유는 `epoch_cause`로 함께 알린다: BOOT(정상 시작), RESET(사용자 초기화), CRASH_RESTART(크래시 자동 재시작), REBUILD(재구축 뒤), RESET_RECOVERY(초기화 실패 뒤 재시작).

| 상태 | 들어오면 하는 일 | 성공 → | 실패·명령 → |
|---|---|---|---|
| NOT_INSTALLED | setup 1–8단계가 끝나지 않음(SDK 파일, AVD, 공유 adb 키, 가속 확인). `start()` 때마다 다시 평가한다 | 끝남 → STOPPED | `start` → FAILED(`PRE_NOT_INSTALLED`, 해결: 설치) |
| STOPPED | 대기 | `start` → PREFLIGHT | `rebuild` → PREFLIGHT(재구축 표시) |
| PREFLIGHT | `engine.lock` 획득 → 루트 가드, SDK 파일, WHPX(`common/accel`, 부팅마다 캐시), D: 여유 ≥ 6 GiB(준비·재구축으로 가면 ≥ 10 GiB, §6.8), 포트 test-bind(IPv4·IPv6), 프록시 결정(§5.4), 공유 adb 키 확인(§6.4) → 모드 결정(아래) | GOLDEN 유효 → LAUNCHING(GOLDEN), COLD 기록 유효 → LAUNCHING(COLD), 없음·무효·재구축 → PROVISIONING | 잠금 실패 → FAILED(`ENGINE_IN_USE`), 그 외 FAILED(`PRE_*`) |
| LAUNCHING | private adb 서버 확인·기동·재등록(§5.5) → 모드별 argv로 에뮬레이터 실행 → 소유자 기록 | 프로세스 생존 → CONNECTING | 조기 종료 → 공통 규칙 |
| CONNECTING | discovery를 250 ms 간격으로 폴링(`port.serial` 일치 + PID 생존) → engine 채널 → `getStatus` | → BOOTING | 첫 실행은 60 s(V3 검사), 이후 30 s 초과 → FAILED(`ENGINE_CONNECT_TIMEOUT`) |
| BOOTING | BootCompleted 또는 `getStatus.booted` → adb `device` → `sys.boot_completed=1`. 한도: 골든 30 s, 콜드 240 s | → SESSION_INIT | 시간 초과 → FAILED(`ENGINE_BOOT_TIMEOUT`). 프로세스 종료 → 공통 규칙 |
| SESSION_INIT | 이전 DeviceSession이 있으면 닫는다 → 검증(아래 표) → `session_init()`(§6.6) → 새 DeviceSession(주입 팩토리) → epoch +1. **검증 부팅**(PROVISIONING에서 온 경우)이면 메모리의 provision_id로 표식을 확인한 뒤 `provision.json`을 쓴다. 이때 원인은 첫 준비면 BOOT, 재구축이면 REBUILD | → READY | 아래 검증 표 |
| READY | 상태 공개, 감시 | `stop` → STOPPING(stop), `reset` → RESETTING, `rebuild` → STOPPING(rebuild) | 크래시 → STOPPING(restart, 원인 CRASH_RESTART). 5분에 1회까지이고, 두 번째는 FAILED(`ENGINE_CRASHED`) |
| RESETTING | **GOLDEN:** `release_all` → `LoadSnapshot("orcus_golden")` → adb `device` 대기(10 s, 재등록 §5.5. 시간 초과면 `ENGINE_ADB_LOST`) → 결과 검증(§6.7). **COLD:** `reset_device()`가 재구축 요청이 된다(GUI가 미리 "수 분 걸림" 확인을 받는다) → STOPPING(rebuild) | → SESSION_INIT(원인 RESET) | 검증 실패 → 경고 `ENGINE_RESET_FAILED` + STOPPING(restart, 원인 RESET_RECOVERY) |
| PROVISIONING | LAUNCHING과 같은 준비(private adb 서버 확인·재등록, 소유자 기록)를 한 뒤 §6.5를 스스로 실행한다. 실행 → 연결 → 부팅 대기도 안에서 하며, 자체 한도는 600 s다 | GOLDEN → 검증 부팅을 위해 STOPPING(restart) → LAUNCHING(GOLDEN). COLD → 그 부팅 그대로 SESSION_INIT | FAILED(`ENGINE_PROVISION_FAILED`: 단계 이름 + 출력) |
| STOPPING | `DeviceSession.close()` → (**[P4]** keep-state 정상 정지면 저장, §6.3) → `setVmState(SHUTDOWN)` 후 대기 5 s(빠른 정지는 2 s) → 남은 우리 SDK 프로세스 정리(private adb 서버는 제외. 앱이 끝날 때만 끈다) | 의도대로: STOPPED / PROVISIONING(REBUILD) / LAUNCHING | 시간 초과 → qemu → emulator 순 TerminateProcess 후 의도대로 |
| FAILED | **잠금을 쥔 경우에만** DeviceSession을 닫고, 남은 우리 VM·SDK 프로세스를 정리한 뒤(private adb 서버는 유지) 잠금·소유자 기록을 해제한다. 잠금 없이 들어온 경우(`ENGINE_IN_USE`, `PRE_NOT_INSTALLED`)에는 어떤 프로세스도 건드리지 않고, 다른 소유자의 잠금·기록도 그대로 둔다. 그다음 `OrcusError`(코드, 해결책)와 진단 로그를 알린다 | 사용자 "다시 시도" → PREFLIGHT(재구축 한도 초기화) | `stop` → STOPPED, `rebuild` → PREFLIGHT(재구축 표시) |

**SESSION_INIT 검증**

| 검사 | 실패하면 |
|---|---|
| adb 상태가 `device` | 재등록(§5.5) 후 10 s 동안 재시도 → FAILED(`ENGINE_ADB_LOST`) |
| GOLDEN: `debug.orcus.golden` = provision id | 골든이 로드되지 않음 → STOPPING(rebuild, 한도 적용). 한도를 넘으면 FAILED(`ENGINE_GOLDEN_LOAD_FAILED`) |
| GOLDEN: 프레임워크가 살아 있음(`provision.framework_problem`: `pidof system_server`, `cmd package list packages android`, `dumpsys power`의 `mWakefulness=`) **[3a 추가]** | 망가진 골든 → 위와 같이 재구축. Phase 3a에서 게스트가 바쁠 때 저장한 골든이 로드 뒤 system_server를 Watchdog으로 잃었는데 표식은 맞았다(§6.5 7a) |
| host 모드: 렌더러가 host GPU | READY + 경고 `ENGINE_GPU_SOFTWARE`(배너 + [GPU 모드 바꾸기]) |
| 에뮬레이터 로그에 `Windows Hypervisor Platform accelerator is operational`이 있음. `getStatus.vmConfig.hypervisorType`은 37.2.12에서 항상 UNKNOWN이라 쓰지 않는다(S0b) | FAILED(`PRE_WHPX_OFF`) |
| 10 s 안에 크기(`format.width×height`)와 길이가 맞는 프레임이 옴. 스트림은 시작하자마자 현재 화면 1장을 보낸다(S2) | FAILED(`ENGINE_NO_FRAMES`) |

COLD 모드에서는 소프트웨어 렌더러가 정상이다. 그래서 항상 READY로 가고 `ENGINE_GPU_SOFTWARE` 배너("느린 그래픽 모드")와 COLD 칩을 보여준다.

**PREFLIGHT 모드 결정**
- 설정 `engine.gpu_mode=software` → COLD. `provision.json`에 COLD 기록이 있어야 하고, 없으면 PROVISIONING(COLD)으로 간다. 골든 검사는 건너뛴다.
- 그 외 → GOLDEN(유효성 키 검사).
- **[P4]** keep-state가 켜져 있으면 KEEP_STATE.

**실패 재구축 한도와 골든 로드 실패**
- **한도:** Orcus 프로세스가 한 번 실행되는 동안 실패로 인한 재구축은 1회까지다. 넘으면 FAILED(`ENGINE_GOLDEN_LOAD_FAILED`, 해결: [다시 시도]·[로그])로 간다. 사용자가 [다시 시도]하거나 명시적으로 재구축하면 한도가 초기화된다. 사용자가 확인한 GPU 모드 변경 재구축은 한도에 넣지 않는다.
- **골든 로드 실패:** `-force-snapshot-load`로 띄웠는데 검증에 실패하면, 에뮬레이터는 **그 스냅샷을 지우고** 종료한다(P: 36.1·master 소스 `Quickboot.cpp`. 37.2.12는 S4에서 확인). 그래서 재시도하지 않고 바로 재구축한다.
  - 일시적인 원인(예: 원격 세션의 렌더러)으로 골든을 잃는 일이 S4·실사용에서 보이면 방식을 바꾼다. `-force-snapshot-load` 없이 부팅하고, RAM 표식으로 조용한 콜드 부팅을 감지한다. 이 방식은 골든을 지우지 않는다(KEEP_STATE는 처음부터 이 방식, §6.3).

**조기 종료 분류** (에뮬레이터 로그 끝부분. 정확한 문구는 Phase 2에서 수집한다)

| 로그 | 판정 | 대응 |
|---|---|---|
| `PANIC: Broken AVD system path` / `Cannot find AVD system path` | `PRE_SDK_MISSING` | "설치 복구" |
| `Not enough space` | `PRE_DISK` | 공간 안내 |
| `Please use -read-only` (다른 인스턴스가 AVD를 잠금) | `ENGINE_AVD_LOCKED` | 소유권 잠금(§1.3) 기준으로 고아만 정리하고 1회 재시도 |
| 포트 사용 중 | `PRE_PORTS` | 다음 포트 세트로(최대 2회) |
| 가속 관련 오류 | `PRE_WHPX_OFF` | WHPX 안내 |
| `Failed to load snapshot` / `delete existing snapshot` (GOLDEN) | 골든 로드 실패 | 재구축(위) |
| 그 외 | `ENGINE_EXIT` | 로그 끝 20줄 + [로그] |

### 5.2 실행 명령줄 (`engine.args.build(cfg, mode, gpu)`, 순수 함수)

```
D:\OrcusAPK\sdk\emulator\emulator.exe -avd orcus36 <GPU> -port 5640 -grpc 8640 -grpc-use-token
  -idle-grpc-timeout 120 -no-boot-anim -no-metrics -timezone Asia/Seoul -feature -Vulkan
  [-http-proxy <proxy>] <MODE>
```

| `<GPU>` (§5.6) | 플래그 |
|---|---|
| host (L1) | `-no-window -gpu host` |
| host_hidden (L2) | `-qt-hide-window -gpu host` |
| software (L3) | `-no-window -gpu software` |

| `<MODE>` | 플래그 |
|---|---|
| GOLDEN (기본) | `-snapshot orcus_golden -force-snapshot-load -no-snapshot-save` |
| COLD (GPU software) | `-no-snapshot-load -no-snapshot-save` |
| PROVISION (첫 준비) | `-no-snapshot-load -no-snapshot-save -change-locale ko-KR` |
| REBUILD | PROVISION + `-wipe-data` |
| **[P4]** KEEP_STATE | `-snapshot orcus_user_<a\|b> -no-snapshot-save` (강제 로드 없음. 실패해도 슬롯이 지워지지 않게, §6.3) |

**불변식** (유닛 테스트):
- `-grpc`는 항상 `-grpc-use-token`과 함께 쓴다(맨 `-grpc`는 `[::]`에 인증 없이 열린다, P). `-ports`는 쓰지 않는다.
- `-gpu` 값은 `host` 또는 `software`만 쓴다. **`auto`는 금지**(`-no-window`에서 SwiftShader로 조용히 떨어짐).
- **`-snapshot`이 있는 argv에는 반드시 `-no-snapshot-save`가 있다.**
  - 공식 문서는 "-snapshot은 종료할 때 같은 이름으로 다시 저장"한다고 하고, 36.1 소스 메모는 반대로 말한다. 그래서 골든 보호를 `-snapshot`의 의미에 맡기지 않는다(U → S4).
- 콘솔 포트는 5586–5682 사이의 짝수다. gRPC·adb 서버 포트는 49152 미만이다.
- `-feature -Vulkan`과 `-timezone`은 항상 붙인다. 37.2.12에서는 게스트 시간대가 항상 GMT다(P).
- `-feature` 값이 바뀌면 스냅샷이 무효가 되므로 고정한다.
- `-http-proxy`는 §5.4의 프록시 규칙으로만 붙인다.

### 5.3 포트

| 용도 | 기본 | 대체 | 비고 |
|---|---|---|---|
| 콘솔 (serial `emulator-<N>`) | 5640 | 5642…5682 짝수 | 외부 adb 서버가 시작할 때 스캔하는 5555–5585 밖(P) |
| 기기 adb | 콘솔+1 (5641) | 자동 | |
| gRPC | 8640 | 8641…8659 | 토큰 + 루프백 전용. 사용 중이면 에뮬레이터가 종료되므로 사전 test-bind 필수(P) |
| private adb 서버 | 15037 | 15038…15047 | 5037은 쓰지도 종료하지도 않는다 |

- 127.0.0.1과 ::1 **둘 다** test-bind한다. Hyper-V/WinNAT 제외 범위는 bind 실패(WSAEACCES)로 드러난다. 진단용으로 `netsh int ipv4 show excludedportrange protocol=tcp` 결과를 남긴다. 이 PC의 제외 범위는 5357, 50000–50059, 61793–62859라 기본 포트와 겹치지 않는다(검토에서 확인).
- 고른 포트는 `state\ports.json`에 저장해 serial을 안정시킨다. 콘솔 포트가 바뀌면 골든 로드가 안 될 수 있다(S4). 그렇다면 유효성 키에 넣는다.

### 5.4 자식 프로세스 환경 (`common/childenv.py`, 시스템 전역 설정은 절대 바꾸지 않는다)

| 구분 | 내용 |
|---|---|
| 설정(공통) | `ANDROID_HOME` = `ANDROID_SDK_ROOT` = `D:\OrcusAPK\sdk` · `ANDROID_AVD_HOME` = `D:\OrcusAPK\avd`(미리 생성, 없으면 Git Bash의 `HOME\.android\avd`가 이길 수 있다) · `ANDROID_EMULATOR_HOME` = `ANDROID_USER_HOME` = `D:\OrcusAPK\home` · `TMP` = `TEMP` = `D:\OrcusAPK\tmp` · `ANDROID_ADB_SERVER_PORT=15037`(에뮬레이터의 자기 등록용) |
| 설정(adb 서버만) | `ADB_USB=0`(학생의 USB 휴대폰을 잡지 않는다. aiStarter의 USB 테스트와 충돌 방지) · `ADB_LOCAL_TRANSPORT_MAX_PORT=5553`(시작 스캔을 끔. 등록은 재등록 메시지로, §5.5) · `ADB_VENDOR_KEYS=D:\OrcusAPK\home\adbkey`(공유 adb 키, §6.4) · `ADB_MDNS_AUTO_CONNECT=0`(mDNS 탐색은 기본으로 켜져 있다. 페어링된 무선 디버깅 휴대폰에 자동으로 붙지 않게 한다, S9) |
| 제거 | `ANDROID_SERIAL`, `ADB_SERVER_SOCKET`(포트 변수보다 우선해 private 서버를 무력화함), `ANDROID_ADB_SERVER_ADDRESS`, `ANDROID_SDK_HOME`, `ANDROID_PREFS_ROOT`, `HOME`(Git Bash), 상속된 `ANDROID_EMULATOR_*`(예: `ANDROID_EMULATOR_FEATURES`, `-feature`와 같은 효과), `QEMU_*`, `QT_*`, `QML_IMPORT_PATH`, `QML2_IMPORT_PATH` |
| PATH 정리 | `sys._MEIPASS`, PySide6 패키지 폴더, venv 아래의 항목을 뺀다. PySide6는 import할 때 자기 폴더를 PATH 앞에 넣고, PyInstaller 런타임 훅도 PATH를 바꾼다(P) |
| 로더 위생(패키징본) | 시작 import가 끝난 뒤, 첫 자식을 만들기 전에 `kernel32.SetDllDirectoryW(None)`을 한 번 부른다. PyInstaller 부트로더의 DLL 디렉터리는 자식에게 상속된다(P) |
| 프록시 | `http_proxy`/`https_proxy`가 있으면 그대로 둔다(게스트 프록시로 쓰임). 없는데 Windows 시스템 프록시가 켜져 있으면(`ProxyEnable=1`, `urllib.request.getproxies()`) `-http-proxy http://host:port`를 붙인다. PAC(`AutoConfigURL`/WPAD)는 지원하지 않는다고 경고한다. 에뮬레이터는 Windows 시스템 프록시를 읽지 않는다(P). 이 PC에는 꺼진 프록시 설정이 있다. `-http-proxy`가 골든 로드에 영향을 주는지는 S4에서 본다 |

- **전면 허용 목록 방식**(필요한 변수만 남기기)은 GPU 드라이버·Winsock이 필요로 하는 변수를 빠뜨릴 위험이 있다. S2에서 안전하다고 확인된 뒤에만 쓴다.
- **유닛 테스트:** 위 규칙을 각각 검사한다. 특히 `QT_*`, `ANDROID_EMULATOR_*`(우리가 설정한 HOME 제외), `QEMU_*`, 앱 폴더 PATH가 자식에게 가지 않는지 확인한다.
- **우리 프로세스:** `-X utf8`로 실행한다. 모든 자식 출력은 `encoding="utf-8", errors="replace"`로 디코딩하고(로케일은 cp949), `CREATE_NO_WINDOW`로 띄운다.
- **우리 gRPC 채널:** `localhost:<grpc.port>`(서버가 ::1 또는 127.0.0.1에 바인드할 수 있음, P), `grpc.enable_http_proxy=0`.

### 5.5 private adb

- **기동:**
  - `adb.exe -P 15037 nodaemon server`를 우리가 직접 자식으로 띄운다. stdout/stderr는 `logs\adb-server.log`로 보내고, PID를 우리가 안다.
  - Windows에서 동작하는지 S9에서 확인한다. 안 되면 `adb -P 15037 start-server`를 stdin/stdout/stderr = DEVNULL로 실행한다.
- **서버 환경**(§5.4):
  - `ADB_USB=0`: USB 휴대폰을 잡지 않는다.
  - `ADB_LOCAL_TRANSPORT_MAX_PORT=5553`: 5555부터 하는 시작 스캔을 끈다. 다른 에뮬레이터에 붙지 않게 하고, 느린 스캔도 피한다. 이 PC에서는 닫힌 루프백 포트 하나에 연결 거부까지 약 2 s가 걸려, 5641까지 스캔하면 약 86 s다(검토에서 측정).
  - `ADB_VENDOR_KEYS=D:\OrcusAPK\home\adbkey`: 공유 adb 키(§6.4).
  - `ADB_EMU=0`은 **쓰지 않는다.** 끊긴 에뮬레이터 연결을 다시 잇는 재시도 스레드까지 꺼진다(P, adb 소스).
- **등록:**
  - 에뮬레이터는 시작할 때 `ANDROID_ADB_SERVER_PORT=15037`의 서버에 `host:emulator:5641`을 한 번 보내 스스로 등록한다(P). 그래서 서버를 에뮬레이터보다 먼저 띄운다.
  - **서버를 (다시) 띄운 뒤에는 우리가 같은 요청을 직접 보낸다.** `127.0.0.1:15037`에 연결해 `0012host:emulator:5641`을 쓰면 adb가 바로 `emulator-5640`으로 연결한다(P, adb 소스 `connect_emulator`). 스캔에 기대지 않으며, 걸리는 시간은 S9에서 잰다.
- **클라이언트 호출:**
  - 형식: `adb -H 127.0.0.1 -P 15037 -s emulator-5640 …`.
  - adb는 ''와 `localhost`만 로컬로 본다. 그래서 서버가 내려가 있을 때 이 클라이언트는 서버를 몰래 띄우지 않고 "cannot start server on remote host"로 실패한다(P, adb 소스. S9에서 확인).
  - `-s`는 `-H`와 상관없이 동작한다. `adb emu …`는 콘솔에 직접 붙는다.
- **서버 재시작**(supervisor가 결정):
  - 재시작 중에는 `DeviceShell` 호출이 즉시 `ENGINE_ADB_LOST`로 실패한다.
  - 15037을 우리가 띄우지 않은 adb가 쓰고 있으면 채택하지 않는다. 우리 서버가 없을 때 에뮬레이터 쪽에서 adb 서버를 스스로 띄우는데(S6 실측), 이 서버에는 `ADB_USB=0`·스캔 끄기·공유 키가 없다. 우리 Job 안의 그런 adb는 종료하고 우리 서버를 다시 띄운 뒤 재등록한다. Job 밖이면 다음 포트로 간다.
  - 재시작은 5분에 3회까지다. 넘으면 FAILED.
  - 최후 수단은 `adb connect 127.0.0.1:5641`이다. serial이 바뀌므로 `DeviceShell`을 갱신한다.
  - **구현(3a):** READY 동안 `engine-adbwait`가 서버 핸들을 기다린다. 서버가 끝나면 supervisor가 경고 `ENGINE_ADB_LOST`를 알리고, worker 작업으로 서버를 다시 띄워 재등록한다. 돌아오면 경고를 거둔다. 실측: 서버를 죽이고 0.6 s 뒤 `emulator-5640`이 다시 `device`(§12.3 11). 최후 수단(`adb connect`)은 넣지 않았다.
- **격리 테스트(양방향):**
  - 외부 5037 서버 목록에 `emulator-5640`이 보이지 않아야 한다.
  - 우리 서버 목록에는 USB 기기와 다른 에뮬레이터가 보이지 않아야 한다.
  - mDNS를 끄는 방법은 U다(S9).
- **실행기(`common/proc`) [P2]:** `Popen` + 출력 읽기 스레드 + join 타임아웃으로 만든다. 시간이 넘으면 TerminateProcess하고 스레드는 버린다.
  - Windows에서 `subprocess.run(timeout=…)`과 PIPE를 함께 쓰지 않는다. CPython은 kill 뒤 `communicate()`를 타임아웃 없이 부른다(P).
  - 오래 사는 손자 프로세스를 만들 수 있는 자식에는 PIPE를 주지 않는다.

### 5.6 GPU 모드

| 모드 | 플래그 | qemu 바이너리 | 세션 모드 |
|---|---|---|---|
| `host` (L1) | `-no-window -gpu host` | `qemu-system-x86_64-headless.exe` | GOLDEN |
| `host_hidden` (L2) | `-qt-hide-window -gpu host` (Android Studio 방식, P) | `qemu-system-x86_64.exe` (Qt 창 숨김) | GOLDEN |
| `software` (L3) | `-no-window -gpu software` | headless | COLD: 매번 콜드 부팅, 스냅샷 없음(소프트웨어 렌더링에서는 스냅샷이 불안정, P) |

- **기본값(`host` 대 `host_hidden`)은 S2에서 정한다.** `-no-window` 경로는 Android Studio가 검증한 방식이 아니다.
- **[P3a] 감지와 수동 전환:**
  - 매 SESSION_INIT마다 렌더러를 확인한다. `dumpsys SurfaceFlinger`의 GLES 줄에 `SwiftShader`, `lavapipe`, `llvmpipe` 중 하나가 있으면 소프트웨어다. 정확한 문자열은 S2에서 기록한다.
  - host 모드인데 소프트웨어면 READY 상태로 두고 배너와 [GPU 모드 바꾸기]를 보여준다.
  - 모드를 바꾸는 것은 설정 변경이다. `ApplyResult.needs_rebuild`를 보고 확인을 받은 뒤 재구축한다.
  - [P3a]에서는 툴바의 [설정]과 배너의 [GPU 모드 바꾸기]가 GPU 모드만 고르는 작은 대화상자를 연다. 전체 설정 대화상자는 [P3b]다.
  - 대화상자는 `preview_settings`로 재구축이 필요한지 먼저 보여 주고, 사용자가 확인했을 때만 `update_settings`를 부른다.
- **[P4] 자동 사다리(`ladder`):**
  - 재확인 부팅에서도 소프트웨어 렌더러가 다시 나올 때만 한 단계 내려간다.
  - 원격 세션(`GetSystemMetrics(SM_REMOTESESSION)`) 중의 하강은 저장하지 않는다. 일시적이기 때문이다.
  - `state\gpu.json`의 키는 (에뮬레이터 build, NVIDIA 드라이버, 원격 여부)다. [GPU 가속 다시 시도]는 L1을 다시 검사한다.
  - 사다리 하강은 단계마다 재구축 1회의 별도 예산을 쓴다.

### 5.7 감시와 정리

- **프로세스 종료:** `proc-wait`가 이벤트를 보낸다. 요청하지 않은 종료는 다음 순서로 분류한다.
  1. **[P4]** 소유자 기록에 `external_stop` 표시가 있으면 STOPPED.
  2. 게스트 전원 끄기(전원 메뉴의 종료)면 STOPPING(stop)으로 간다(§5.1 공통 규칙).
  3. 그 외는 크래시.
- **keepalive:** 30 s마다 `getVmState`를 보낸다(마감 5 s). 3회 연속 실패하면 크래시다. `-idle-grpc-timeout 120`에 걸리지 않게 하는 역할도 한다. UNIMPLEMENTED·INVALID_ARGUMENT는 해당 기능만 끈다.
- **adb:** 설치 전에 `get_state()`를 확인한다. 이상이 있으면 §5.5의 재시작 규칙을 따른다.
- **디스크:** D: 여유가 3 GiB 미만이면 경고(`ENGINE_DISK_LOW`, 배너)를 띄운다. 1 GiB 미만이면 빠른 정지 뒤 FAILED(`PRE_DISK`, 해결: [공간 확보])로 간다(STOPPING 의도 = fail). 빠른 정지는 스냅샷을 저장하지 않는다.
- **시작 시 정리(잠금을 쥔 뒤):**
  - discovery에서 `avd.dir`이 우리 것인 살아 있는 VM은 고아이므로 종료한다(§1.3). 예외는 **[P4]** detached 기록이 있을 때다.
  - 죽은 PID의 discovery 파일은 지운다.
  - AVD의 `*.lock`은 우리 AVD의 qemu가 하나도 없을 때만 지운다.
  - 다른 경로의 에뮬레이터(Android Studio 등)는 절대 건드리지 않는다.
- **discovery 위치:** `%LOCALAPPDATA%\Temp\avd\running\pid_*.ini`(P). TMP를 바꿔도 이 위치만 쓰인다(S6 실측. `<TMP>\avd\running`은 비어 있음). 파일 이름의 PID는 qemu다. 일치 기준은 `port.serial`과 `avd.dir`이다.
- **프로세스 점검(S4/S6/S9, 정지 후 매번):**
  - READY 직후와 정지 후에 `JobObjectBasicProcessIdList`와 "이미지가 `D:\OrcusAPK\sdk` 아래인 모든 프로세스"를 비교한다.
  - Job 밖으로 빠져나간 프로세스가 있으면 경고하고 종료한다.
  - 정지 후 N s(S4에서 정함) 뒤에도 남은 우리 SDK 프로세스(예: netsimd)는 종료한다. private adb 서버는 앱이 사는 동안 유지하므로 앱이 끝날 때만 끈다. 이 점검과 정리는 엔진 잠금을 쥔 프로세스만 한다.
  - 소유권 잠금 때문에 SDK 하나에는 VM이 하나뿐이므로, 다른 사용자의 프로세스를 죽일 일은 없다.

---

## 6. 스냅샷·세션 모델

### 6.1 세션 모드와 데이터 안내

| 모드 | 언제 | 부팅 | 닫을 때 | [휴대폰 초기화] |
|---|---|---|---|---|
| **GOLDEN** (기본) | GPU `host` / `host_hidden` | `orcus_golden` 로드(읽기 전용) | 저장 없음(약 1 s) | `LoadSnapshot(golden)`, 2–8 s (U) |
| **COLD** | GPU `software` | 매번 콜드 부팅(현재 userdata 그대로) | 저장 없음, userdata는 남음 | 확인 후 재구축(`-wipe-data`, 수 분) |
| **[P4] KEEP_STATE** | 설정 "세션 간 휴대폰 유지" | 마지막 정상 사용자 슬롯(§6.3) | 정상 종료 시 다른 슬롯에 저장 | `LoadSnapshot(golden)` |

- **GOLDEN의 의미:** 학생마다, 실행마다 같은 휴대폰에서 시작한다. 크래시나 강제 종료가 골든을 망가뜨리지 않는다.
  - 골든은 `-no-snapshot-save` 불변식이 지킨다(§5.2).
  - named 스냅샷의 RAM 파일이 공유되지 않는지, 디스크가 복원되는지는 추론(I)이므로 S4에서 확인한다.
- **모드별 안내 문구** (`common/messages_ko.py`)

| 상황 | GOLDEN | COLD | [P4] KEEP_STATE |
|---|---|---|---|
| 상태 칩 | "깨끗한 휴대폰 모드 — 닫으면 처음 상태로" | "느린 그래픽 모드 · 휴대폰 상태가 유지됨" (+ 배너) | "휴대폰 유지 모드" |
| **[P3b]** 닫을 때(이번 세션에 앱을 설치하거나 실행했으면) | 모달: "이번에 실행한 앱의 저장 데이터(TinyDB 등)는 닫으면 지워집니다." [닫기] [취소] □ 다시 묻지 않기. **[P4]**에서 [이번에는 휴대폰 유지하고 닫기] 추가 | 묻지 않음 | 묻지 않음(정상 종료 때 저장) |
| 크래시 자동 재시작(`epoch_cause = CRASH_RESTART`) | "휴대폰이 멈춰서 다시 켰습니다. 저장된 앱 데이터는 처음 상태로 돌아갔습니다." | "휴대폰이 멈춰서 다시 켰습니다." | "휴대폰이 멈춰서 마지막으로 저장한 상태로 다시 켰습니다." |

### 6.2 epoch와 실행 정책

- **epoch:** SESSION_INIT에서만 1 오른다. 부팅 뒤와 초기화 뒤가 모두 SESSION_INIT을 지나므로, 초기화 한 번에 정확히 1 오른다.
  - 오른 이유는 `EngineStatus.epoch_cause`(BOOT, RESET, CRASH_RESTART, REBUILD, RESET_RECOVERY)로 함께 알린다.
  - `DeviceShell`·`DeviceSession`은 epoch에 묶인다. 옛 객체로 호출하면 `EngineNotReady`가 난다. 그래서 apk는 단계마다 `shell()`/`session()`을 새로 받는다.
  - SESSION_INIT은 새 DeviceSession을 만들기 전에 이전 것을 닫는다. GUI는 epoch가 바뀌면 `device()`를 다시 받고 프레임 스트림을 다시 연다.
- **`reset_device()`:** 목표 epoch(현재 + 1)를 돌려준다. `FRESH_PHONE`은 `wait_ready(min_epoch=목표)`를 기다린 뒤에 설치한다.

| 정책 | 동작 | 언제 |
|---|---|---|
| `KEEP_DATA` (기본) | `install -r`. 데이터를 유지한다(휴대폰에서 앱을 업데이트하는 것과 같음) | 같은 앱을 고쳐 다시 드롭할 때 |
| `FRESH_APP` | 제거한 뒤 설치 | 앱 데이터를 비울 때 |
| `FRESH_PHONE` | `reset_device()` → 새 epoch의 READY를 기다린 뒤 설치. **COLD에서는** 실행마다 수 분짜리 재구축이 되므로 `FRESH_APP`으로 바꾸고 알린다 | 설정 "실행할 때마다 휴대폰 초기화"(수업·시험 모드) |

- **같은 파일 빠른 경로:** 다음을 모두 만족할 때만 7단계 안의 `adb install`을 건너뛴다.
  - 정책이 `KEEP_DATA`다.
  - **현재 epoch**에서 같은 SHA로 그 패키지를 설치한 기록이 ApkService에 있다. [제거]하면 기록을 지운다.
  - 빠른 경로라도 4–6단계(앱 카드, 사전 점검, READY 대기)는 항상 실행한다. `FRESH_APP`/`FRESH_PHONE`은 빠른 경로를 쓰지 않는다.
- **epoch가 바뀌었을 때 ApkService가 하는 일**
  - `CRASH_RESTART`: 마지막 실행을 다시 설치하고 실행한다. **[P3b]** 앱별 마이크 동의도 다시 적용한다.
  - `RESET`, `RESET_RECOVERY`, `REBUILD`: 다시 설치하지 않는다. 사용자가 원한 초기화이거나 재구축이기 때문이다.
  - 설치나 실행 도중 epoch가 바뀌면 진행 중이던 실행을 취소한다. 그것이 마지막 드롭이었고 원인이 `CRASH_RESTART`면 새 epoch에서 한 번 다시 시작한다.

### 6.3 [P4] KEEP_STATE: 두 슬롯 번갈아 저장

- **같은 이름으로 다시 저장할 때의 동작은 경로마다 다르다**(I. 37.2.12는 U → S4).
  - gRPC `SaveSnapshot`: 36.1·master 소스에서 **이미 있는 이름을 거부**한다(`success=false`, "already exists").
  - 콘솔·QEMU 저장 경로: 같은 이름의 이전 스냅샷을 먼저 지우고 쓰며, 실패하면 지운다(`savevm.c`, `Snapshotter.cpp`).
  - `DeleteSnapshot`: 응답이 항상 `success=true`라서 아무것도 증명하지 못한다.
  - 그래서 한 이름에 덮어쓰지 않는다. 지운 뒤에는 `ListSnapshots(All)`로 정말 사라졌는지 확인한다.
- **슬롯:** `orcus_user_a` / `orcus_user_b`. 포인터는 `state\keep_state.json` {slot, provision_id, saved_at}.
- **정상 정지:**
  1. 지금 로드되지 **않은** 슬롯을 `DeleteSnapshot`하고, `ListSnapshots(All)`로 없어졌는지 확인한다.
  2. 그 슬롯에 `SaveSnapshot`한다. `success == true`인지, `ListSnapshots`에서 Compatible 또는 Loaded인지 확인한다.
  3. 포인터를 갱신하고 옛 슬롯을 지운다.
  - 실패하면 포인터를 그대로 두고, 다음 시작 때 알린다.
- **빠른 정지**(로그오프·종료·디스크 부족)는 절대 저장하지 않는다. 이전 슬롯이 그대로 남는다.
- **시작:** 포인터 슬롯을 `-force-snapshot-load` **없이** 부팅한다. 강제 로드는 검증에 실패하면 스냅샷을 지우기 때문이다(§5.1).
  - 대신 SESSION_INIT에서 RAM 표식(`debug.orcus.golden`)으로 조용한 콜드 부팅을 감지한다.
  - 표식이 없으면 그 부팅을 멈추고 **재구축 없이** GOLDEN으로 다시 시작한다. 이때 "보관한 휴대폰을 열 수 없어 처음 상태로 시작합니다"를 알린다.
  - 그 세션에서는 사용자가 확인하지 않는 한 정지할 때 저장하지 않는다. 슬롯은 지우지 않는다. 원인이 일시적이면 다음 시작에 열릴 수 있다.
- **크래시 자동 재시작**은 포인터 슬롯으로 한다. 골든으로 재시작하면 정지할 때 보관 상태를 덮어쓰게 된다.
- **골든 재구축**(드라이버·에뮬레이터 업데이트 등)이 일어나면 슬롯은 무효가 된다. 슬롯을 지우고 사용자에게 알린다(D6 문구).
- **디스크:** 슬롯 하나가 약 2 GiB다. 전환 중에는 최대 2개가 동시에 있다.
- **S4에서 확인할 것**(실험은 골든이 아닌 임시 이름으로 한다):
  - gRPC로 이미 있는 이름에 저장하면 어떻게 되는가
  - 저장 도중 강제 종료하면 어떻게 되는가
  - 강제 로드 없이 부팅했을 때, 로드 실패가 조용한 콜드 부팅이 되는지와 슬롯이 남는지
  - 저장 시간

### 6.4 유효성 키와 무효화 (`state\provision.json`)

- **키 구성:**
  - 에뮬레이터 build, 이미지 태그·리비전, `config.ini`의 SHA-256, GPU 모드
  - **관찰한** 로케일·시간대, feature 플래그, 준비 스크립트 버전 `PROVISION_V`
  - **공유 adb 키**(`D:\OrcusAPK\home\adbkey.pub`)의 SHA-256
  - S4 결과에 따라 콘솔 포트와 프록시를 추가할 수 있다.
- **공유 adb 키를 쓰는 이유:**
  - adb는 Windows 사용자마다 `%USERPROFILE%\.android\adbkey`를 쓴다(P).
  - 사용자 키를 그대로 쓰면, 계정을 바꿀 때마다 골든 안의 adbd가 새 키를 몰라 'unauthorized'가 된다. 그러면 재구축(약 3분)해야 한다.
- **공유 adb 키 구성:**
  - setup이 `D:\OrcusAPK\home\adbkey(.pub)`를 한 번 만든다(`adb keygen`, §11.6 8단계).
  - 에뮬레이터는 `ANDROID_EMULATOR_HOME`(= `D:\OrcusAPK\home`)에 키가 있으면 그것을 게스트에 넣는다. 키가 없을 때만 `%USERPROFILE%\.android`에서 복사한다(P).
  - 우리 adb 서버는 `ADB_VENDOR_KEYS`로 같은 키를 쓴다.
  - 이 조합이 실제로 되는지는 U이며 S9에서 확인한다.
  - 안 되면 사용자 키의 해시를 유효성 키에 넣는다. 골든은 하나뿐이므로, 이 경우 **Windows 계정이 바뀔 때마다 재구축(약 3분)**이 일어난다. 이 비용을 D9와 §13에 적는다.
  - 비용을 줄이는 방법은 S9 결과를 보고 정한다: 재구축할 때 알려진 모든 계정의 키를 게스트에 등록하거나, 계정마다 골든을 따로 둔다(계정당 +2 GiB).
- **키가 다르면** 재구축한다.
- **키로 알 수 없는 변화**(NVIDIA 드라이버 문자열, 에뮬레이터 내부 호환성)는 `-force-snapshot-load` 실패로 드러난다.
  - 실패한 골든은 에뮬레이터가 지우므로, 재시도 없이 재구축한다(실패 재구축 한도 §5.1).

### 6.5 준비(provisioning): GOLDEN은 1–10단계(7a 포함), COLD는 1–7단계 (S4 실측 약 90 s)

PROVISIONING 상태가 이 단계를 스스로 실행한다. 먼저 LAUNCHING과 같은 준비(private adb 서버 확인·재등록, 소유자 기록)를 한다. 실행·연결·부팅 대기도 안에서 하며, 전체 한도는 600 s다.

1. **실행:** PROVISION 모드로 띄운다. 재구축이면 REBUILD 모드(`-wipe-data`)다. 그래야 재구축한 골든에 이전 세션의 앱·데이터·권한이 남지 않는다.
2. **대기:** 고정 시간만큼 기다리지 않는다.
   1. `sys.boot_completed=1`이 될 때까지 기다린다.
   2. 다음 두 조건이 될 때까지 최대 120 s 폴링한다.
      - `persist.sys.locale`이 `ko-KR`이다.
      - 프레임워크 재시작을 관찰했다. 재시작 신호는 `sys.system_server.start_count` 증가다(S4 실측: 부팅 완료 약 5 s 뒤 1→2, 그동안 `settings`·`lock_settings`·`location` 서비스가 사라져 명령이 `DeadObjectException`/`Can't find service`로 실패).
   3. `service check`로 `settings, lock_settings, location, package, activity, input_method`가 **5회 연속** 응답할 때까지 기다린다(S4: 부팅 뒤 6.6–7.6 s).
   - 재시작 신호가 끝내 오지 않아도 로케일이 맞고 서비스가 응답하면 진행한다.
3. **렌더러 확인:** GOLDEN인데 소프트웨어 렌더러면 `ENGINE_PROVISION_FAILED`(단계 "renderer", 해결: [GPU 모드 바꾸기])로 중단한다.
4. **1회성 기기 설정**(`engine/provision.py`. Phase 2에서 아래 10개를 모두 적용하고 다음 부팅에서 다시 읽어 확인함, 초안 `tools/spikes/provision_draft.py`). 명령마다 읽기 확인이 맞을 때까지 최대 30 s 재시도한다:
   - 자동 회전 켜기: `settings put system accelerometer_rotation 1`
   - 화면 꺼짐 시간 최대: `settings put system screen_off_timeout 2147483647`, `svc power stayon true`
   - 잠금 화면 끄기: `locksettings set-disabled true`
   - 위치 켜기: `cmd location set-location-enabled true`
   - adb 설치 검증 끄기: `settings put global verifier_verify_adb_installs 0`, `package_verifier_enable 0`
   - **3버튼 내비:** `cmd overlay enable-exclusive --category com.android.internal.systemui.navbar.threebutton`. `hw.mainKeys=yes`는 API 36에서 내비 바를 숨기지 못한다(S7, D7 변경)
   - **스타일러스 필기 끄기:** `settings put secure stylus_handwriting_enabled 0`. 가상 터치 장치가 `TOUCHSCREEN | STYLUS`를 알려, 끄지 않으면 세션마다 첫 텍스트 상자 탭에서 Gboard의 전체 화면 "스타일러스 사용해 보기" 시트가 앱을 가린다(S8 사전 확인)
   - **Bluetooth 끄기:** `cmd bluetooth_manager disable`. 켜진 채 저장하면 골든을 로드할 때마다 netsim Bluetooth 스택이 크래시한다(S5)
   - **클립보드 접근 토스트 끄기:** `settings put secure clipboard_show_access_notifications 0`. 한글 입력은 붙여넣기라서, 켜져 있으면 확정할 때마다 토스트가 앱 아래쪽을 가린다(S8. 세션에서 확인했고 골든 재구축으로는 아직 확인하지 않음)
   - **마지막에 3 s 기다린 뒤 `sync`한다.** SettingsProvider는 값을 최대 약 2 s 뒤에 파일로 쓰고 `setVmState(SHUTDOWN)`은 전원 끄기라서, 그대로 끄면 마지막 설정이 사라진다(Phase 2에서 두 번 겪음)
5. **이미지별 단계 — 없음(S1 결과).** Google TTS에 내장된 한국어 음성(`ko-kr-x-ism-seanet-embedded`)으로 기기 안에서 바로 말한다. 내려받기·로그인이 필요 없다.
   - **개인 Google 계정을 골든에 로그인하지 않는다.** 로그인하면 그 PC의 모든 학생이 계정을 공유하게 되고, 토큰이 스냅샷에 남는다.
6. **`session_init()`(§6.6)과 기능 탐지** → `state\device_caps.json`:
   - `getprop ro.build.version.sdk`, `getprop ro.product.cpu.abilist`
   - `pm list packages com.google.android.tts`
   - `cmd package query-services -a android.intent.action.TTS_SERVICE`
   - `settings get secure tts_default_synth`
   - `cmd package query-activities -a android.speech.action.RECOGNIZE_SPEECH`
   - `ime list -s`
   - 렌더러, `getStatus`의 hypervisorType·hardwareConfig(패널 크기)
7. **관찰값 검증:** `persist.sys.locale` = `ko-KR`, `persist.sys.timezone` = `Asia/Seoul`인지 본다. 다르면 단계 이름과 함께 실패로 끝낸다. 관찰값은 `provision.json`과 `device_caps.json`에 기록한다.
   - **COLD는 여기서 끝난다.** `provision.json`(mode: COLD)을 쓰고, 이 부팅이 그대로 SESSION_INIT → READY로 이어진다.
7a. **(GOLDEN) 준비 세션을 끄고, 깨끗한 세션에서 저장한다.** `setVmState(SHUTDOWN)` → `-change-locale` 없이 COLD 플래그로 다시 부팅 → 10 s 안정 → 4단계 설정과 로캘·시간대가 남아 있는지 읽어 확인 → `setprop debug.wm.disable_deprecated_target_sdk_dialog true`(RAM 속성) → 8·9단계.
   - **4단계 명령이 하나라도 실패했거나 이 확인에서 하나라도 빠지면 저장하지 않고 실패로 끝낸다.** 기존 골든도 지우지 않는다. Phase 2에서 준비 세션이 부팅 7 s 뒤 사라졌는데 스크립트가 다음 세션에서 저장해, 로드 뒤 adb가 붙지 않는 골든이 생긴 적이 있다.
   - **이유(S4 실측):** 준비 세션(로캘 변경 → 프레임워크 재시작 직후)에서 저장한 골든은 로드할 때 게스트 시계가 저장 시각에 멈춰 있고, **로드 약 21 s 뒤 게스트 전체가 멈췄다**(adb·화면·logcat 모두. 3회 재현). 일반 세션에서 저장한 스냅샷은 4분 뒤 로드해도 시계가 맞고(−0.1 s) 90 s 넘게 멀쩡했다. 저장 방식(gRPC·콘솔)과 GPU 모드(host·host_hidden·software)는 원인이 아니었다.
   - 비용: 콜드 부팅 1회(약 25 s). 1회성 준비 전체는 약 90 s.
   - **저장 전 대기와 확인 [3a 추가]:** RAM 속성·표식을 넣은 뒤 `/proc/stat`으로 게스트 CPU 사용률(2 s 창)을 재서 0.35 미만이 **2회 연속**이 될 때까지 기다린다(최대 180 s, 넘으면 로그에 남기고 진행). 그다음 `provision.framework_problem`(system_server·package·power 서비스)이 문제를 찾으면 저장하지 않고 실패로 끝낸다.
     - **이유(Phase 3a 실측):** `-wipe-data` 뒤 첫 부팅의 게스트는 몇 분 동안 앱 컴파일로 바쁘다(load average 약 12). 그때 저장한 골든은 표식·설정 확인을 모두 통과했지만, 로드한 뒤 system_server가 Watchdog으로 죽어 `package`가 Broken pipe, `power`가 DEAD_OBJECT를 냈고 `svc power stayon`이 멈췄다. 그 상태에서 초기화하면 표식이 빈 값으로 보였다. 대기를 넣은 뒤: 68 s에 조용해짐(CPU 0.03), 재구축 전체 294 s, 초기화 2회 정상.
8. **표식:**
   - `setprop debug.orcus.session ''`로 세션 nonce를 비운다(§6.7). `DeviceShell`이 argv 요소마다 따옴표 처리를 하므로 빈 값이 그대로 전달된다. 종료 코드도 확인한다.
   - `setprop debug.orcus.golden <provision_id>`를 설정한다.
   - 둘 다 RAM에만 있는 값이라, 골든에서 부팅하면 그대로 남아 있고 콜드 부팅하면 없다.
9. **저장:**
   1. 재구축이면 `DeleteSnapshot("orcus_golden")` 후 `ListSnapshots(All)`로 없어졌는지 확인한다. 37.2.12의 gRPC 저장은 이미 있는 이름을 **덮어쓰고 성공**을 돌려주었다(S4. 36.1 소스로 예상한 거부는 일어나지 않음). 그래도 먼저 지워 이전 골든의 파일이 섞이지 않게 한다.
   2. `SnapshotService.SaveSnapshot("orcus_golden")`을 부르고 **응답의 `success`와 `err`를 확인한다.** 실패도 정상 응답으로 오기 때문이다(P).
   3. 실패하면 `adb -H 127.0.0.1 -P 15037 -s emulator-5640 emu avd snapshot save orcus_golden`로 저장한다.
   4. `ListSnapshots`에서 Compatible 또는 Loaded인지 확인한다.
10. **검증 부팅:** `setVmState(SHUTDOWN)` → GOLDEN 모드로 부팅 → SESSION_INIT에서 표식·프레임워크와 4단계 설정 확인 → **로드 60 s 뒤에도 adb가 답하고 `framework_problem`이 없는지 확인**(S4의 "로드 20–25 s 뒤 멈춤"과 system_server Watchdog의 60 s를 지나서. 3a에서 30 s → 60 s) → `provision.json` 기록 → READY. 실패하면 골든을 지우고 `ENGINE_PROVISION_FAILED`로 끝낸다.
    - 실측(Phase 2, `-wipe-data`부터): 첫 부팅 92 s, 재시작 대기 9 s, 설정 약 10 s, 콜드 부팅 22 s, 저장 2 s, 검증 부팅 6 s → 약 3분 30초. 3a에서 7a 대기(약 70 s)와 검증 60 s가 더해져 **약 5분**(실측 294 s).
    - UI 문구: "처음 준비하는 중 (약 5분) — 한 번만 합니다".
    - 재구축이면 이유를 보여준다. 예: "그래픽 드라이버가 바뀌어 휴대폰을 다시 준비합니다 (약 5분)".

### 6.6 `session_init()` (매 부팅과 초기화 뒤, 멱등, 약 1 s)

- `setprop debug.wm.disable_deprecated_target_sdk_dialog true`
  - debug 속성은 RAM에만 있다. 골든 부팅은 이 값을 복원하지만, COLD는 매번 다시 설정해야 한다.
  - shell 사용자는 user·userdebug 빌드 모두에서 `debug.*`를 설정할 수 있다(P, AOSP sepolicy).
- `svc power stayon true`
- `setMicrophoneState(realAudioEnabled=false)`. 앱별 동의가 있으면 **[P3b]** ApkService가 실행할 때 다시 켠다.
- `setprop debug.orcus.session <새 uuid>`: 세션 nonce(§6.7). engine이 이 값을 기억한다.
- **위치 테스트 제공자 준비 + 서울시청(37.5665, 126.9780):** `appops set com.android.shell android:mock_location allow` → `cmd location providers add-test-provider gps`·`fused` → `set-test-provider-enabled <p> true` → `set-test-provider-location <p> --location 37.5665,126.9780 --accuracy 5`. gRPC `setGps`는 App Inventor 앱에 닿지 않는다(S7: LocationSensor가 fused 제공자에 BALANCED로 요청해 GNSS가 켜지지 않음). 약 0.5 s.
- **게스트 시계 확인(보험):** 깨끗한 세션에서 저장한 골든은 로드할 때 에뮬레이터가 시계를 맞춘다(S4: 1–4분 뒤 로드에서 오차 −0.1…−1.2 s). 준비 세션에서 저장한 골든만 시계가 저장 시각에 멈췄다(§6.5 7a로 막음). 그래도 오차가 5 s를 넘으면 `su 0 date -u MMDDhhmmYYYY.ss`로 맞춘다(userdebug 이미지, S4에서 동작 확인). TLS를 쓰는 Web 컴포넌트와 Clock 수업 때문에 필요하다.
- 렌더러 확인, `DeviceCaps` 캐시 읽기.

### 6.7 초기화 검증

- 표식 `debug.orcus.golden`은 초기화 **전에도** 있다. 그래서 표식만으로는 `LoadSnapshot`이 성공했다고 증명할 수 없다.
- 초기화 뒤에는 먼저 adb가 다시 `device`가 될 때까지 기다린다(10 s, 재등록 §5.5. 시간 초과면 `ENGINE_ADB_LOST`). 그다음 아래를 모두 확인한다.
  - 응답의 `success == true`
  - `debug.orcus.session`이 **이번 세션에서 설정한 값과 다르다.** 골든은 준비 8단계에서 nonce를 비운 상태로 저장했으므로, 성공했다면 보통 빈 값이 보인다. 판정 기준은 '빈 값'이 아니라 '다름'이다.
  - 골든 표식이 provision id와 같다.
- 하나라도 맞지 않으면 `ENGINE_RESET_FAILED`를 경고하고, 엔진을 GOLDEN으로 재시작한다(원인 RESET_RECOVERY).
- 이 검사가 있어야 수업·시험 모드에서 초기화가 조용히 실패해 이전 앱과 데이터가 남는 일을 막을 수 있다.

### 6.8 디스크

| 항목 | 크기 |
|---|---|
| 에뮬레이터 | 1.07 GiB |
| 이미지 | 4.37 GiB |
| userdata | 최대 6 GiB(희소 파일인지는 S4에서 확인) |
| 골든 | RAM 크기 파일 약 2.5 GiB + 디스크 변경분 |
| 합계 | **약 13–14 GiB** |
| [P4] keep-state | +2 GiB(전환 중에는 +4 GiB) |
| 설치 중 임시 파일 | +2.4 GB |

- **여유 공간 확인:**
  - 설치 전: **24 GiB**(합계 + 임시 파일 + 실행 여유)
  - 실행 전: **6 GiB**(userdata 증가분 + 로그 여유). 준비·재구축으로 가는 시작이면 **10 GiB**(userdata를 새로 만들 때 필요한 1.2 × 6 GiB(P) + 골든 RAM 약 2 GiB).
  - S4에서 userdata가 희소 파일인지 확인한 뒤 두 값을 조정한다.
- **실행 중:** 3 GiB 미만이면 경고한다(`ENGINE_DISK_LOW`). 1 GiB 미만이면 빠른 정지 뒤 FAILED(`PRE_DISK`)로 간다.

## 7. APK 수명주기 (`apk`)

### 7.1 판정 (드롭한 **뒤**, 확장자가 아니라 내용으로)

- **드래그 중에는 판정하지 않는다.** `dragEnterEvent`에서는 I/O 없이 "로컬 파일 1개인가"와 확장자 힌트만 본다.
  - Drive '온라인 전용' 파일을 드래그 중에 열면 다운로드가 시작되어 창이 멈춘다.
  - 내용 판정은 드롭한 뒤 STAGING/INSPECTING 단계에서 한다.

| 판정 | 근거 | 학생에게 보여줄 문구 |
|---|---|---|
| APK | zip 안에 `AndroidManifest.xml` | (진행) |
| AAB | `BundleConfig.pb` 또는 `base/manifest/` | "App Inventor의 .aab는 Google Play용입니다. [빌드 ▸ Android App (.apk)]로 내보낸 .apk를 끌어다 놓으세요." |
| AIA | `youngandroidproject/project.properties` | "App Inventor 프로젝트 파일(.aia)입니다. ai2.appinventor.mit.edu에서 [프로젝트 ▸ 내 컴퓨터에서 프로젝트 (.aia) 가져오기]로 연 뒤 [빌드 ▸ Android App (.apk)]로 받은 .apk를 끌어다 놓으세요." |
| AIX | 확장 기능 설명 파일(`components.json` 등) | "App Inventor 확장기능(.aix) 파일이라 실행할 수 없습니다. 프로젝트에서 [확장기능 추가하기]로 넣은 뒤 APK를 빌드하세요." |
| 그 외 / 깨진 zip | — | "Android 앱 파일(.apk)이 아닙니다." |

### 7.2 스테이징

- **복사:** 해시를 계산하면서 `D:\OrcusAPK\staging\<sha256>.apk`로 복사한다(목표 ≤ 0.4 s, 3.5 MB 기준). 실패하면 `STAGE_COPY_FAILED`다.
- **한글·Drive 경로**는 Python 유니코드 API로만 읽는다. 이후 도구는 ASCII 사본만 본다.
- **Drive '온라인 전용' 파일**은 읽는 순간 다운로드가 시작된다. "Google Drive에서 파일을 가져오는 중…"을 보여주고, 120 s가 지나면 `STAGE_DRIVE_TIMEOUT`으로 "'오프라인 사용 가능'으로 설정한 뒤 다시 끌어다 놓으세요"를 안내한다.
- **보관:** LRU로 최근 20개만 둔다. 같은 파일 빠른 경로는 §6.2를 따른다(epoch 기준이며, 건너뛰는 것은 설치뿐).

### 7.3 매니페스트 분석 (apkInspector 1.3.7, `parse_apk_for_manifest`)

- **읽는 항목:**
  - package, launcher(상대 이름 `.Screen1`을 package 기준으로 풀어 씀), **launcher의 `android:screenOrientation`**, label(한글 가능)
    - apkInspector는 방향을 **숫자**로 준다(픽스처 실측: unspecified `-1`, landscape `0`). 숫자 표로 바꾼다: 0 landscape, 1 portrait, 6 sensorLandscape, 7 sensorPortrait, 8 reverseLandscape, 9 reversePortrait, 11 userLandscape, 12 userPortrait
  - versionCode/Name, min/targetSdk, 권한, `<queries>`, 기능(feature)
  - 네이티브 ABI(`lib/<abi>/`, 그리고 `assets/` 아래 ELF 매직)
  - 아이콘(`res/mipmap-xxxhdpi-v4/ic_launcher.png`)
- **App Inventor 판정:**
  - application이 `…runtime.multidex.MultiDexApplication`이거나 package가 `appinventor.ai_`로 시작하면 App Inventor 앱이다.
  - `ReplApplication`이면 Companion이다.
- **빌드 시기**(targetSdk 기준): 26 = nb172–177, 28 = nb178–183, 29 = nb184–186, 30 = nb187–189, 31 = nb190–193, 33 = nb194–197, 34 = nb198–201, 35 = nb202–205, 36 = nb206+. 그보다 낮으면 "2018년 12월 이전 빌드"다.
- **파서 함정(P):** UTF-16 문자열 풀, `<application>` 앞에 오는 `<queries>`, 중복 컴포넌트. 회귀 픽스처는 MITAI2Companion 2.81u다.
- **파싱에 실패해도 막지 않는다.** 그대로 설치하고 다음 방법으로 찾는다.
  - package: `pm list packages -3`를 설치 전후로 비교한다.
  - launcher: `cmd package resolve-activity --brief -c android.intent.category.LAUNCHER <pkg>`

### 7.4 사전 점검 (`compat.preflight(info, caps) -> list[Issue]`)

- **BLOCK:**
  - minSdk > 36 → `INSTALL_OLDER_SDK`: "이 앱은 PC 휴대폰보다 새 Android가 필요합니다".
  - 기기 ABI 목록에 없는 ABI의 네이티브 코드만 있음 → `INSTALL_NO_ABI`: 32비트 ARM 전용 확장 기능이다. google_apis 36은 x86_64와 arm64-v8a 번역만 된다(P).
- **WARN:**
  - NFC / Bluetooth / USB Serial → "PC 휴대폰에서는 동작하지 않습니다".
  - RECORD_AUDIO → 마이크 토글 안내.
  - `<queries>`에 zxing SCAN → `SCANNER`: "PC 휴대폰 카메라는 가짜 영상이라 실제 바코드를 비출 수 없습니다. UseExternalScanner를 끄면 오류 1501 없이 스캐너 화면은 열리지만 인식은 되지 않습니다."
  - targetSdk < 24 → 레거시 모드(우회 플래그를 자동으로 붙임). targetSdk ≤ 22이면 같은 칩에 §7.6의 권한 검토 화면 안내를 넣는다.
  - ARM 번역 → "느릴 수 있음".
  - 가로 고정 launcher → `ORIENTATION_LOCKED`(실행 전에 기기를 돌림, §7.6).
- **INFO:**
  - VIBRATE → "진동은 PC에서 느낄 수 없습니다(진동 표시는 나중에 추가)". Sound·Player 컴포넌트는 항상 이 권한을 넣는다.
  - 위치 권한 → `LOCATION` 힌트(§9.7).
  - TTS·음성 인식 상태 칩. **TTS는 매니페스트로 알 수 없다.** 모든 AI2 APK에 모든 컴포넌트 클래스가 들어 있기 때문이다(P). 그래서 `DeviceCaps`와 런타임 로그(§7.8)로만 판단한다. 음성 인식 칩은 `<queries>`에 `android.speech.RecognitionService`가 있는 앱(SpeechRecognizer를 쓰는 앱, 픽스처 확인)에만 붙인다.

### 7.5 설치

`adb -H 127.0.0.1 -P 15037 -s emulator-5640 install -r [-g: 자동 허용 설정 시] [--bypass-low-target-sdk-block: targetSdk < 24] <staged>`. 타임아웃은 180 s이고 출력은 UTF-8로 디코딩한다.

| 실패 | 대응 |
|---|---|
| `INSTALL_FAILED_DEPRECATED_SDK_VERSION` | 우회 플래그로 1회 재시도 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | `-d`로 재시도(userdebug 이미지). Play 이미지면 제거 여부를 묻는다 |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | `Prompt`로 묻는다: "다른 App Inventor 계정·키로 빌드된 같은 앱이 있습니다. 바꿀까요? 저장된 데이터가 지워집니다." → `answer()` → 제거 후 설치 |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | 32비트 ARM 전용 확장 기능이라고 설명 |
| `INSTALL_FAILED_OLDER_SDK` | minSdk 설명 |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | [휴대폰 초기화] 제안 |
| `INSTALL_PARSE_FAILED_*` / `INVALID_APK` | "파일이 손상되었습니다. App Inventor에서 다시 받으세요." |
| 시간 초과 / 기기 offline | `get_state()` 확인 → 1회 재시도 → `ENGINE_ADB_LOST` |
| 그 외 | 쉬운 말 설명 + [자세히]에 원문 |

### 7.6 실행

1. 이전 앱에 `am force-stop`을 보낸다.
2. **방향 맞추기:** launcher의 `screenOrientation`이 landscape / sensorLandscape / reverseLandscape / userLandscape이면 실행 **전에** `DeviceControls.rotate()`로 가상 기기를 가로로 돌린다(방향 부호는 S7 표). portrait 고정인데 기기가 가로면 세로로 돌린다.
   - "landscape"는 표시 `ROTATION_90`으로 그려지므로 자세 LANDSCAPE(z=90)로, "reverseLandscape"는 REVERSE_LANDSCAPE로 돌린다(S7). sensor·user 변형은 기기가 이미 그쪽 축이면 그대로 둔다.
   - ApkService는 자기가 돌린 자세를 epoch마다 기억하고, 새 epoch는 골든의 세로 자세로 본다. **[P3b]** 회전 버튼이 생기면 자세를 `DeviceControls`와 공유하는 방법을 정한다.
3. `am start -W -n <pkg>/<launcher>`를 실행하고 `Status: ok`를 확인한다.
   - Activity를 찾지 못하면 `resolve-activity`로 찾아 다시 시도한다. 그래도 없으면 `APP_LAUNCH_NOT_FOUND`다.
   - targetSdk ≤ 22이면 힌트를 보여준다: "옛 앱은 먼저 권한 검토 화면이 나옵니다. [계속]을 누르세요."
4. `pidof <pkg>`로 PID를 얻어 감시기를 붙이고, `RunEvent.pid`에 담는다. 5 s 안에 PID가 없으면 crash 버퍼를 읽어 크래시 카드를 만든다.
5. **[P3b]** 앱별 마이크 동의가 있으면 마이크를 켠다.

### 7.7 권한 (사용자 결정 D5)

- **기본:** 실제 휴대폰처럼 대화상자를 띄우고 사용자가 [허용]을 누른다. 학생이 AskForPermission/PermissionDenied 흐름을 그대로 겪어 보고, "Orcus에서는 되는데 내 폰에서는 안 되는" 앱이 생기지 않게 한다.
- **[이 앱 권한 모두 허용]:** 요청된 런타임 권한마다 `pm grant <pkg> <perm>`을 실행한다. 바꿀 수 없는 권한은 무시한다. 오류 908 카드의 해결 버튼으로도 쓴다.
- **위치 권한 대화상자:** '대략적인 위치'를 고르면 coarse 권한만 받는다. 그러면 갱신이 10분에 한 번으로 제한된다(P). 위치 힌트에 이 점을 적는다.
- **설정 "권한 자동 허용(-g)":** 옵트인이다.

### 7.8 감시 (`adb logcat`, 패턴은 S5에서 실제 출력으로 고정)

- **원천은 `adb logcat -v threadtime -T 1`이다(Phase 3a 기기 시험에서 변경).** gRPC `streamLogcat`은 `LoadSnapshot`([휴대폰 초기화]) 뒤 **아무것도 보내지 않았다**(5 s·20 s 모두 0개, 같은 시각의 표식 줄은 adb logcat에 있음). 게스트의 `goldfish-logcat`(`logcat -f /dev/hvc1`)을 다시 시작해도 돌아오지 않아 호스트 쪽 경로가 끊긴 것으로 본다. 앱 시작 때의 골든 부팅 뒤에는 동작한다.
  - engine이 epoch에 묶인 `adb logcat` 자식 프로세스를 만들고(`EngineManager.logcat()`), app이 그것을 DeviceSession의 logcat 원천으로 주입한다. 원천을 만들지 못하면 gRPC로 대체한다. DeviceSession을 닫으면 자식 프로세스도 끝난다.
  - `-T 1`은 "지금부터"라서 백로그(골든 부팅 이후 약 3.4만 줄)와 게스트 시계 비교가 필요 없다.
  - gRPC Text 모드의 `contents`는 메시지 하나에 항목 하나이고 **줄바꿈이 없다**(9,667개 메시지에 `\n` 0개). 메시지를 이어 붙이지 않고, 한 메시지에 여러 항목이 있으면 다음 타임스탬프에서 나눈다(`common/logtext.py`).

App Inventor는 오류를 **두 경로**로 알린다(P, appinventor-sources).
- **번호 있는 컴포넌트 오류:** `Form.ErrorOccurred`가 처리한다. 항상 `E/Form: Form <name> ErrorOccurred, errorNumber = N, …`를 기록한다. 학생이 처리기를 두지 않았으면 토스트(또는 대화상자 변형)를 띄운다.
- **블록 실행 중 잡히지 않은 예외:** `W/System.err` 스택과 `E/RuntimeErrorAlert: alert(<msg>, <title>, End Application`을 남긴다. 이 대화상자의 버튼은 `finish()`만 부르므로 **프로세스가 살아 있다.**

판정 우선순위는 표의 위에서 아래로다.

| 분류 | 신호 | 표시 |
|---|---|---|
| `APP_SPEECH_MISSING` | 앱 PID의 어떤 줄이든(`AndroidRuntime` FATAL 스택, `W/System.err`, RuntimeErrorAlert 메시지) `ActivityNotFoundException` 또는 `No Activity found to handle Intent`와 `RECOGNIZE_SPEECH`가 함께 있음(S5 실측: `W System.err` + RuntimeErrorAlert "Runtime Error"). 또는(UseLegacy 끔) `E SpeechRecognizer: Bind to system recognition service failed with error 10` 줄(`DeviceCaps`와 무관, 아래 구현 메모), 또는 `DeviceCaps.speech_recognizer`가 거짓일 때 `E/Form … errorNumber = 0, componentType = SpeechRecognizer, functionName = GetText, messages = Try Again.` | "이 PC 휴대폰에는 음성 인식 앱이 없어 오류가 났습니다. 음성 인식은 실제 휴대폰이나 Companion으로 시험하세요. (SpeechRecognizer의 UseLegacy를 끄면 오류 창 대신 ErrorOccurred로 받습니다.)" |
| `APP_CRASH` | `FATAL EXCEPTION` + `Process: <pkg>` (AndroidRuntime) | 빨간 배너 "○○가 예기치 않게 종료되었습니다" + 예외 첫 줄, [자세히] [다시 실행] [보고서 복사] |
| `APP_AI_RUNTIME_ERROR` | `E/RuntimeErrorAlert` `alert(` | 카드: 블록 실행 오류 메시지를 한국어 설명과 함께 보여준다. [다시 실행] 포함. `System.err` 스택만으로는 크래시로 보지 않는다 |
| `APP_AI_COMPONENT_ERROR` | `E/Form … ErrorOccurred, errorNumber = N` | 정보 카드(앱이 처리했을 수 있음). 번호별 한국어 설명은 아래 목록 |
| `APP_TTS_MISSING` | 앱 PID의 `InternalTTS` 로그: `max number of speak retries exceeded (20): speak will fail`(Speak 후 약 10 s) 또는 `speak called and tts.speak result was an error`. `DeviceCaps.tts_engine`이 None이면 첫 `speak called when TTS not initialized`에서 바로 표시 | "이 PC 휴대폰에 한국어 음성 엔진이 없어 말하기가 실패했습니다…" + 이유 보기 |
| `APP_ANR` | `WindowManager: ANR in Window{… <pkg>/…}. Reason:Input dispatching timed out`(S5 실측: 입력 뒤 약 6 s, 앞에 `InputDispatcher: Window … is unresponsive`) 또는 ActivityManager `ANR in <pkg>` | 배너 + 설명 |
| `APP_DIED` | PID가 사라짐(크래시 흔적 없음) | "앱이 종료됨" + [다시 실행] |

`APP_AI_COMPONENT_ERROR`의 번호별 설명:
- 1501 → BarcodeScanner. §7.4 SCANNER 문구로 설명한다.
- 2701 → `AvailableLanguages`/`AvailableCountries`에서만 발생하는 TTS 준비 오류다.
- 908 → "권한이 거부되었습니다" + [이 앱 권한 모두 허용].
- 601 → ActivityStarter.
- 3806 → SpeechRecognizer 'No Match'. 마이크가 꺼져 있으면(기본) 인식 서비스가 있어도 항상 이 번호다 → "마이크를 켜면 말소리를 인식할 수 있습니다" + [마이크 켜기]([P3b]). 걸리는 시간은 일정하지 않다: S5 5.5 s, Phase 3a 단독 6 s, 다른 오류 경로 직후 26 s(온라인 인식기 대기로 추정).
- 0 → SpeechRecognizer 'Try Again.'(서비스 연결 실패). `APP_SPEECH_MISSING` 규칙으로 처리한다.
- 3802 → SpeechRecognizer 'Client Side Error'(Phase 2에서는 보지 못함).
- 그 외 번호는 원문을 보여준다.

- **처리하지 않은 컴포넌트 오류는 토스트다**(`Notifier.ShowAlert`). uiautomator dump에는 나오지 않고, logcat에 `E Toast: getXOffset()/getYOffset()/setGravity() shouldn't be called on text toasts…` 3줄이 따라붙는다 → 감시에서 무시한다.
- **S5 결과:** `streamLogcat`은 D 수준 줄도 전달한다(`InternalTTS` 26줄). 2701은 AvailableLanguages 뒤 12.4 s에 온다. 이 이미지는 한국어 음성이 내장되어 "ko-KR 음성만 없는 경우"는 생기지 않는다(엔진을 끈 경우만 `APP_TTS_MISSING`).
- **App Inventor 자체 오류 대화상자는 크래시로 취급하지 않는다.**
- **구현 메모(`apk/watcher.py`):**
  - `FATAL EXCEPTION`은 같은 PID의 AndroidRuntime 줄이 끝날 때까지(0.5 s, 다른 태그 줄, `has died`, 스트림 끝) 모은 뒤 판정한다. 그래야 첫 줄만 보고 크래시로 정하지 않고 RECOGNIZE_SPEECH를 찾을 수 있다.
  - 같은 오류(코드 + 내용)는 3 s 안에 한 카드로 묶는다(Clock 타이머가 같은 오류를 반복하는 경우). ANR은 10 s, TTS·음성 인식 없음은 실행마다 한 번이다.
  - 음성 인식 없음 카드 뒤 3 s 안의 RuntimeErrorAlert는 같은 원인이므로 버린다.
  - `Bind to system recognition service failed with error 10`은 `DeviceCaps`와 상관없이 음성 인식 없음이다. 캐시된 기능 탐지는 골든 기준이라 실행 중에 꺼진 제공자를 모르고, error 10(바인드 불가)은 그 자체가 제공자가 없다는 증거다. `DeviceCaps.speech_recognizer`는 이 줄 없이 errorNumber 0만 올 때만 본다.
  - `am start`와 PID 사이에 온 줄은 링 버퍼에서 다시 넣는다. 5 s 안에 PID가 없고 crash 버퍼에도 흔적이 없으면 `APP_DIED`다.
  - **네이티브 크래시도 `APP_CRASH`다**: 앱 PID의 `F libc: Fatal signal N (SIG…)`. WebViewer가 있는 앱은 렌더러 프로세스가 죽으면 `chromium: … Render process … crash wasn't handled by all associated webviews, triggering application crash` 뒤 SIGTRAP으로 끝난다(Phase 3a: OrcusProbe에 `am crash`를 보내면 렌더러가 먼저 죽어 이 경로가 된다. ANR 반복 중에도 한 번 일어났다).
  - Java 크래시 뒤에는 App Inventor Form이 `E Form: Uncaught Exception` + 스택 + `errorNumber = 909`를 남긴다(Phase 3a, HelloPurr). 크래시 카드가 실행을 끝내므로 909 카드는 따로 만들지 않는다.

### 7.9 세션 동작

- [다시 실행] (F5): `force-stop` 후 `am start`
- [앱 중지]
- [데이터 지우고 실행]: `pm clear` 후 재실행. 패키지는 남으므로 빠른 경로 기록은 유지된다.
- [제거]: 빠른 경로 기록을 지운다.
- [권한 모두 허용]
- **[P3b]** [화면 방향 맞추기]: §9.7
- [보고서 복사]: APK 정보, `DeviceCaps`, Orcus·에뮬레이터 build, 관련 로그를 텍스트로 모은다. 로그는 앱의 경고·오류(W/E/F) 최근 30줄과 I 이상 최근 50줄이다. App Inventor의 Form은 `onGlobalLayout()`·`keyboard hidden!` D 줄을 초당 여러 번 남겨, D를 넣으면 50줄이 그것으로 채워진다(Phase 3a 기기 시험).

---

## 8. 드래그앤드롭 → 상호작용까지 (E2E)

0. **앱 시작.** 창은 2 s 안에 그려진다(I). 엔진은 PREFLIGHT → … → READY 순으로 바로 시작한다(골든 2–8 s, U). 화면에 "휴대폰 켜는 중… (약 5초)"를 보여주고, 다 켜지면 "여기에 .apk를 끌어다 놓거나 [APK 열기]"를 보여준다. 실패하면 폰 영역에 엔진 오류 패널을 띄운다.
1. **드롭 (t = 0).** `dragEnterEvent`는 I/O 없이 검사한다(§7.1). 베젤이 강조되고 "놓으면 설치"가 표시된다. [APK 열기](Ctrl+O), 명령줄 인자, 단일 인스턴스 전달도 같은 경로(`SessionApi.open_paths`)로 들어온다.
2. **판정** (≤ 0.1 s, §7.1).
3. **스테이징** (≤ 0.4 s, §7.2).
4. **분석** (≤ 0.5 s, §7.3) → **드롭 후 1 s 안에 앱 카드**(아이콘, 이름, 빌드 시기 배지)를 보여준다. 사용자가 받는 첫 피드백이다.
5. **사전 점검** (즉시, §7.4). BLOCK이면 여기서 멈추고 [보고서 복사]를 보여준다.
6. **READY 대기.** 예열되었으면 0 s다. 아니면 남은 부팅 시간을 보여준다("부팅 중… 이어서 설치합니다"). 재구축 중이면 남은 시간(약 3분)을 솔직하게 보여준다. 대기 중에 새 드롭이 오면 취소된다. 엔진이 FAILED가 되면 이 실행은 `ENGINE_UNAVAILABLE`로 멈추고(원인은 엔진 실패 패널에 표시), 엔진이 다시 READY가 되면 [다시 실행]을 제안한다.
7. **정책 적용 → 이전 앱 정지 → 설치** (2–5 s, U). `FRESH_PHONE`이면 초기화하고 새 epoch를 기다린다. `KEEP_DATA` 빠른 경로에서는 설치만 건너뛴다(§6.2).
8. **방향 맞추기 → 실행** (0.5–2 s, U, §7.6).
9. **확인.** PID를 얻고 감시를 붙인다. 앱의 첫 프레임은 스트림 폴링 한 번(≤ 125 ms, P) 안에 도착한다.
10. **상호작용.** RUNNING이 되면 토스트 "실행 중 · 4.8초"를 보여준다. 드롭부터 잰 시간이며 회귀 추적을 위해 로그에도 남긴다. 포커스는 폰 화면으로 간다.
11. **실행 중 이벤트**는 §7.8을 따른다. 엔진이 크래시하면 자동으로 재시작한다(`epoch_cause = CRASH_RESTART`). 모드에 맞는 안내(§6.1)를 보여주고, ApkService가 마지막 APK를 재설치·재실행한다(약 15 s).

**시간 예산** (S4·S5에서 측정해 확정)

| 상황 | 추정 | 목표 |
|---|---|---|
| 드롭 → 앱 카드 | ≤ 1 s | ≤ 1 s |
| 엔진 예열됨, 드롭 → Screen1 | 3–9 s | **≤ 10 s (p95)** |
| 앱 시작 직후 바로 드롭 | 6–17 s | **≤ 20 s** |
| 골든 재구축 필요 | 3–4분 | 진행률과 이유 표시 |
| 최초 설치(2.3 GB 다운로드, WHPX 재부팅, 첫 준비) | 1회 10–20분 | 단계별 진행 표시 |

---

## 9. 화면·입력·센서·오디오

### 9.1 9:16 화면과 프레임 파이프라인

- **9:16 뷰포트(가로 모드는 16:9)를 고정한다.** 화면 위젯의 비율을 고정하고, 서버에 **정확히 그 크기**를 요청한다. 그래야 레터박스가 생기지 않는다.
  - **크기 계산:** 창이 놓인 화면의 `QScreen.availableGeometry()`를 쓴다.
    - 높이: 창 테두리·툴바·상태바·베젤·◁○▢ 줄(배율 100%에서 약 256 px)을 빼고 16의 배수로 내린다.
    - 폭: 높이 × 9/16.
    - 폭을 기준으로도 계산한다(가용 폭 − 측면 버튼 열 − 베젤 − 펼친 오른쪽 패널). 둘 중 작은 쪽을 쓴다. 폰이 들어가지 않으면 오른쪽 패널을 자동으로 접는다.
    - 창이 다른 화면으로 옮겨 가면 다시 계산한다.
  - **이 PC의 예**(모니터 3대):
    - 주 모니터 1920×1080(작업 영역 높이 1032 px): **약 432×768**
    - 세로로 돌린 1200×1920 모니터: 오른쪽 패널을 접으면 720×1280이 들어간다(I).
  - 배율 125/150%에서는 창 장식도 함께 커지므로 폰 화면은 그만큼 작아진다.
- **배율 메뉴:** 맞춤(기본), 50%(360×640), 75%(540×960), 100%(720×1280). 각 항목은 창이 놓인 화면에 들어갈 때만 켜진다.
1. **요청:** `streamScreenshot(ImageFormat{format=RGB888, width=W, height=H, display=0})` + `authorization: Bearer <token>`.
   - W×H는 위젯의 **물리 픽셀** 크기이고 720×1280을 넘지 않는다.
   - 서버가 비율을 유지하며 줄여 준다(P). 432×768이면 프레임당 약 1 MB다.
2. **채널 옵션:** `grpc.max_receive_message_length = 16 MiB`, `grpc.enable_http_proxy = 0`.
3. **`frames` 스레드:**
   - **크기는 `Image.format.width/height`에서 읽는다.** 37.2.12는 `Image.width/height`를 모든 프레임에서 0으로 보낸다(S2 실측: `image` 995,328 B, `format` 432×768). `Image.width==0`을 "빈 프레임"으로 보고 버리면 화면이 영영 나오지 않는다(첫 스파이크가 이 판정 때문에 멈췄다).
   - 길이가 `format.width*format.height*3`과 다른 프레임만 버린다. 회전은 `format.rotation`, 순번은 `seq`, 시각은 `timestampUs`(벽시계 µs)다.
   - 스트림은 시작하자마자 현재 화면 1장을 보내고, 그 뒤로는 화면이 바뀔 때만 보낸다(S2).
   - `QImage(data, w, h, w*3, Format_RGB888)`로 감싼다(zero-copy, GIL 안전). S2 결과에 따라 `Format_RGB32`로 한 번 변환한다.
     - 이 작업은 `gui.frame_pump`가 넘겨준 프레임 콜백 안에서 한다. 콜백 규칙의 유일한 예외다(§3).
   - 1칸 우편함에 넣는다. 그리기가 대기 중이면 신호를 보내지 않고, 오래된 프레임은 버린다.
4. **그리기:** `ScreenView.paintEvent`가 `drawImage`로 1:1 출력한다(스케일·스무딩 없음, `setDevicePixelRatio(dpr)`). 베젤은 캐시한 `QPixmap`이다.
5. **행 순서:** 위→아래로 다룬다(I, Android Studio 방식). S2에서 확인하고, 틀리면 `frame.flip_rows`를 켠다.
6. **재요청:** 위젯 크기(150 ms 디바운스), `format.rotation`, DPI, 화면이 바뀌면 스트림을 다시 연다. 스트림이 끝나면(`on_end`) `EngineStatus.state`가 READY일 때만 간격을 늘려 가며 다시 연결하고 `release_all`을 보낸다. READY가 아니면 다음 epoch를 기다린다.
7. **일시 정지:** 창이 최소화되면 프레임 스트림만 멈춘다. 입력 스트림과 keepalive는 유지한다. 화면이 정지해 있으면 서버가 프레임을 보내지 않으므로, 마지막 프레임을 계속 그린다.
8. **진단:** `seq` 간격(누락 프레임)과 `now − timestampUs`(프레임 나이)를 숨김 오버레이(Ctrl+Shift+F12)와 S2 측정에 쓴다.

**예산**(잠정 목표, U — S2 기준선으로 보정한다. 크기는 주 모니터 기본 크기와 720×1280 두 가지):
- 애니메이션 중 30 fps 이상
- 프레임 나이 p95 ≤ 50 ms, 마우스 입력 → 화면 반영 ≤ 100 ms
- GUI 프로세스 CPU: 애니메이션 중 한 코어의 15% 이하, 정지 화면에서는 1% 미만

**예산을 넘으면** 다음 순서로 대응한다: RGB32 변환 위치 조정 → 30 fps 상한 → MMAP 스파이크. 지연 예산도 이 판단 기준에 포함된다(수용 기준 B2·B9).

### 9.2 좌표 변환 (`device/rotation.py`)

- GUI는 표시 중인 프레임 안의 정규 좌표 (u, v) ∈ [0, 1]과 그 프레임의 `seq`만 보낸다(`PointerAt`).
- device는 그 `seq` 프레임의 회전(최근 프레임 몇 개를 기억)과 `getStatus.hardwareConfig`의 패널 크기를 써서 **회전 전 패널 픽셀**로 바꾼다. 그래서 회전하는 순간에도 새 프레임 기준으로 잘못 변환하지 않는다.
- 아래 표는 **S7에서 확정**했다(`tools/spikes/fx_s7b.py`): `pointer_location`을 켜고 패널 좌표로 누른 점을 프레임에서 찾아 표로 되돌리니, 네 프레임 회전 모두 **오차 0–1 px**였다(B5 기준 ±4 px). 게스트 표시 회전이 자세와 같을 때(앱이 따라 돎)와 다를 때(세로 고정 홈 화면, 가로 고정 Screen2, 180°) 모두 맞다. MouseEvent는 회전 전 패널 좌표이고, 표시 회전은 게스트가 스스로 적용하기 때문이다. device의 유닛 테스트로 고정한다.

| rotation | panel x | panel y |
|---|---|---|
| PORTRAIT | u·W | v·H |
| LANDSCAPE | (1−v)·W | u·H |
| REVERSE_PORTRAIT | (1−u)·W | (1−v)·H |
| REVERSE_LANDSCAPE | v·W | (1−u)·H |

(W×H = 720×1280, `DeviceCaps.panel`에서 가져온다.)

### 9.3 마우스

- **왼쪽 버튼:** 누름·드래그는 `MouseEvent{x, y, buttons=1}`, 뗌은 `buttons=0`이다. 이동 이벤트는 묶지 않고 받는 즉시 보낸다. 그래야 Canvas가 실제 타이밍의 촘촘한 DOWN/MOVE/UP을 받아 TouchDown·Dragged·Flung 속도가 맞는다.
- **이동 이벤트가 성기면**(S7): Windows에서 마우스 이동 밀도는 OS가 `WM_MOUSEMOVE`를 합치는 데서 정해진다. `AA_CompressHighFrequencyEvents`는 Windows에서 터치에만 적용되므로(P) 해결책이 아니다.
  - `GetMouseMovePointsEx`나 Raw Input / WM_POINTER 이력(네이티브 이벤트 필터)으로 중간 점을 되살린다.
  - GUI 스레드의 5 ms 규칙이 가장 큰 지렛대다.
- **떼기 보장:** 클래식 엔진은 접촉을 스스로 풀지 않는다(P). 다음 경우에 반드시 `buttons=0`을 보낸다:
  - 누른 채 화면 밖으로 나감(가장자리 고정 좌표로)
  - 포커스를 잃거나 창이 비활성화됨
  - 회전이 바뀜
  - 입력 스트림이 재연결됨(id 0–9 해제)
  - epoch가 바뀜
  - 앱이 종료됨
- 한 제스처 안에서 MouseEvent와 TouchEvent를 섞지 않는다.
- **오른쪽 클릭**은 Back(설정, 기본 켬), **가운데 클릭**은 Home이다.
- **이동 밀도(S7):** 게스트는 화면 프레임마다(약 60 Hz) 한 번 묶어 Dragged를 보낸다(10 ms 간격 50번 → 30번). 그보다 촘촘히 보내도 얻는 것이 없고, 20 ms 간격이면 거의 다 전달된다.
- **휠: 게스트에 휠을 받을 장치가 없다**(S7: `getevent -lp`에 REL_WHEEL 없음, `injectWheel`로 스크롤되지 않음). **[P3b]** 대안: 커서가 Canvas 밖에 있을 때만 휠 한 칸을 짧은 세로 드래그(약 120 패널 px)로 흉내 낸다. Canvas 위에서는 드래그 이벤트가 되므로 보내지 않는다.
- **[P4]** 두 손가락 핀치(Ctrl + 드래그, TouchEvent id 0/1)를 넣는다.

### 9.4 키

- 모든 키는 헬퍼 하나를 거치며 `eventType=keypress`를 **항상** 설정한다. 기본값 keydown은 해제되지 않는다(P). 길게 누르기는 keydown → 700 ms → keyup이다.

| 컨트롤 | key 값 | 비고 |
|---|---|---|
| ◁ Back | `GoBack` | KEYCODE_BACK으로 도착해 `Screen.BackPressed`를 일으킨다 |
| ○ Home | `GoHome` | `Home`은 텍스트 커서 키라서 다르다 |
| ▢ Recents | `AppSwitch` | |
| 전원 | `Power` | 길게 누르면 전원 메뉴 |
| 볼륨 +/− | `AudioVolumeUp` / `AudioVolumeDown` | |

- **폰 화면에 포커스가 있을 때:**
  - 출력 가능한 ASCII는 `KeyboardEvent{text=ch}`로 보낸다.
  - 이름 있는 키는 W3C 이름(Backspace, Enter, Tab, Delete, Arrow\*, Home/End)으로 보낸다. S2에서 확인한다.
  - Ctrl+A/C/X/Z는 chord로 보낸다.
  - **호스트 Ctrl+V**는 Windows 클립보드의 텍스트를 `paste_text()`로 붙여넣는다(한글 포함).

### 9.5 단축키 (`gui/keymap.py` 한 곳. 중복과 폰 편집 키 충돌은 유닛 테스트로 검사)

| 동작 | 단축키 |
|---|---|
| APK 열기 | Ctrl+O |
| 다시 실행 | F5 |
| 뒤로 | Esc(설정, 기본 켬) · 오른쪽 클릭 |
| 홈 | Ctrl+Shift+H · 가운데 클릭 |
| 최근 앱 | Ctrl+Shift+R |
| 흔들기 | F6 |
| 왼쪽 / 오른쪽 회전 | F7 / F8 |
| 한글 입력 바 | Ctrl+T · 한/영 키(S8에서 확인) · [가] 버튼 |
| 휴대폰 초기화 | 메뉴에서만(확인 대화상자) |
| FPS·지연 오버레이(개발용) | Ctrl+Shift+F12 |

Ctrl+←/→와 Ctrl+Shift+←/→는 TextBox의 단어 이동·선택과 겹치므로 쓰지 않는다.

### 9.6 한글 입력

키 주입으로는 한글을 보낼 수 없다(P). `KeyboardEvent.text`, `adb shell input text`, scrcpy는 모두 ASCII만 받는다.

- **[P3b] 한글 입력 바:**
  - 폰 아래의 `QPlainTextEdit`에서 Windows IME로 조합한다.
  - Enter는 `paste_text(s)`를 부른다: `setClipboard(ClipData{text})` → 100 ms 대기 → `KeyboardEvent{key:'Paste'}`.
  - Shift+Enter는 줄바꿈이다. 바는 열린 채로 있고 Esc로 닫는다.
  - **방식은 Paste 키로 확정(S8).** App Inventor TextBox·MultiLine(줄바꿈 유지)·PasswordTextBox·WebViewer 안 `<input>`에 그대로 들어간다. NumbersOnly는 숫자만 받는다(한글은 조용히 버려짐). Ctrl+V chord는 `v`만 들어가고 Gboard가 `ㅍ`으로 바꾼다.
  - ASCII도 붙여넣기로 보낸다: `KeyboardEvent{text:"abc"}`는 ko-KR Gboard에서 "뮻"이 된다(S8).
  - 준비 단계에서 `clipboard_show_access_notifications 0`을 설정한다. 켜져 있으면 붙여넣을 때마다 "○○에서 클립보드 데이터를 붙여넣음" 토스트가 앱 아래쪽을 가린다(S8, 끄면 사라짐 확인).
  - 툴팁: "휴대폰 클립보드를 덮어씁니다. 비밀번호에는 쓰지 마세요."
- **[P4] 폰 화면에 직접 한글 입력:** `WA_InputMethodEnabled` + `QInputMethodEvent`를 쓴다. 조합 중인 글자는 폰 아래 말풍선에 보이고, 확정된 문자열은 키 입력과 같은 큐 순서로 `paste_text`한다. S8을 통과해야 넣는다.
- **쓰지 않는 것:** ADBKeyBoard(GPL)는 쓰지 않는다. 게스트 한국어 IME(Gboard)는 S1 결과를 보고 나중에 선택한다.

### 9.7 센서와 방향 (`device-controls` 스레드)

**[P3b] 흔들기 (Shake)** — `device/shake.py`, 순수 파형 생성기
1. `getSensor(ACCELERATION)`으로 기준값 b를 읽는다. 중력 축 a = argmax|b|.
2. 기준값을 250 ms 유지해 10개 표본 창을 채운다.
3. **b[a] += sign(b[a])·Δ**(중력과 같은 방향, 0을 넘지 않게)를 60 ms 유지한 뒤 기준값으로 돌아간다.
   - 역방향 자세(REVERSE_PORTRAIT/LANDSCAPE)에서는 b[a] ≈ −9.81이다. 여기에 +Δ를 더하면 0을 넘어 부호가 바뀐다.
   - 스트리밍 프레임의 회전은 가속도로 정해진다(아래). 부호가 바뀌면 펄스 동안 창이 180° 뒤집힌다.
   - App Inventor는 |현재 − 평균|만 보므로 감도 대역은 그대로다(P).
4. `setPhysicalModel(ROTATION, 현재 회전·기울기 상태, STEP)`으로 override를 해제한다. `setPhysicalModel`을 부르면 모든 `setSensor` override가 지워진다(P).

- **세기(Δ):** Moderate(기본) 16, Weak 25, Strong 9 m/s².
- **클릭 한 번에 펄스 한 번**만 보낸다. 버튼을 누르고 있으면 450 ms마다 반복한다(MinimumInterval 400 ms보다 길게).
- **감도 선택:** 흔들기 옆에 감도 선택기를 두고, 패키지마다 기억한다. 라벨은 "→ AccelerometerSensor.Shaking"이다.
- **끊지 않고 실행:** 흔들기는 `device-controls` 스레드에서 처음부터 끝까지 한 번에 실행된다. 기울기·회전 명령과 섞이면 펄스가 잘리거나 옛 기울기가 복원되기 때문이다.
- **근거**(P, appinventor-sources로 재확인):
  - 현재 표본을 10개 캐시에 **넣은 뒤** |현재 − 평균|이 감도 대역에 드는지 본다. 대역: Strong 5–13, Moderate 13–20, Weak > 20.
  - 이벤트 사이 최소 간격은 400 ms다. 센서 속도는 SENSOR_DELAY_GAME이다.
  - 60 ms 펄스는 표본 약 3개다(I).
  - 감도가 맞으면 첫 표본의 편차 0.9Δ(14.4 / 22.5 / 8.1)가 대역 안이라 1번 울리고, 나머지 표본은 400 ms 간격에 막힌다.
  - **다른 감도도 울릴 수 있다.** 예: Δ16의 둘째 표본 12.8은 Strong 대역이다. 그래서 "다른 감도에서는 안 울림"은 약속하지 않는다.
- **검증:** App Inventor 감지기를 유닛 테스트에 이식해 "맞는 감도에서 정확히 1번"을 확인한다.
- **S7 실기 결과(OrcusProbe, 세로):** Weak+Δ25, Moderate+Δ16, Strong+Δ9 → **각각 정확히 1번**. Moderate+Δ25, Strong+Δ16 → 1번(큰 Δ는 더 민감한 감도도 울림), Moderate+Δ9, Weak+Δ16 → 0번. TalkToMe(Moderate 기본)도 Δ16으로 "Stop shaking me!"를 말했다. 거꾸로·가로·반대 가로 자세에서도 Moderate+Δ16이 감지됐다(중력 방향 부호).
- **회전도 흔들기로 잡힌다(S7):** 회전은 가속도를 한 번에 바꾼다. 180° 회전(19.6 m/s²)은 Moderate·Strong에서, 90° 회전은 Strong에서 Shaking을 일으킨다. STEP과 SMOOTH 모두 그렇다. 실제 폰을 빠르게 돌려도 같은 일이 생기므로 막지는 않되, **[P3b]** 회전 버튼은 15°씩 0.6 s 이상에 걸쳐 돌리는 방식을 시험한다(10개 표본 평균과의 차이를 5 m/s² 아래로 유지). 안 되면 회전 직후 흔들기 감지가 생길 수 있다고 툴팁에 적는다.

**방향(회전)**
- **프레임 회전은 기기 자세를 따른다.** 스트리밍 프레임의 `format.rotation`(과 픽셀 방향)은 가상 기기의 **자세(가속도)**로 정해진다(P, `ScreenshotUtils.deriveRotation`).
  - 축과의 오차가 약 26° 안일 때만 그 방향으로 보고, 아니면 PORTRAIT로 본다. S7 실측: 가로 자세에서 z를 ±24°까지 기울이면 LANDSCAPE, ±26°부터 PORTRAIT. 세로에서는 ±30°까지 PORTRAIT(아니면 PORTRAIT로 보기 때문).
  - 게스트의 표시 회전은 반영하지 않는다. 그래서 가로 고정 Screen이나 BarcodeScanner 캡처 화면(`screenOrientation="landscape"`)은 세로 자세에서 옆으로 누운 채 보인다(S7: Screen2를 세로 자세로 열면 프레임 PORTRAIT, 표시 `ROTATION_90`).
  - **180°는 표시가 따라 돌지 않는다**(S7): z=180이면 프레임은 REVERSE_PORTRAIT인데 표시는 직전 회전 그대로다(Android가 180°를 허용하지 않음). 탭 좌표는 §9.2 표대로 맞는다.
- **대응:**
  - **[P3a]** launcher의 방향을 읽어 실행 전에 기기를 돌린다(§7.6).
  - **[P3b]** 표시 회전이 기기 자세와 다르면 배너와 [화면 방향 맞추기]를 보여준다.
    - 표시 회전은 `dumpsys window displays`의 `mCurrentRotation=ROTATION_n`으로 읽는다(S7: 0.06 s). API 36의 `dumpsys input`에는 SurfaceOrientation 줄이 없다.
    - Screen2, 스캐너 화면, 실행 중 ScreenOrientation 블록도 이 방법으로 해결한다.
- **[P3b] 회전 버튼(F7/F8):** `setPhysicalModel(ROTATION, [0,0,0] | [0,0,90] | [0,0,180] | [0,0,270], STEP)` → 프레임 PORTRAIT / LANDSCAPE / REVERSE_PORTRAIT / REVERSE_LANDSCAPE(S7 확정). 앱 표시는 0·90·270°만 따라 돈다.
  - 골든에서 자동 회전이 켜져 있어, App Inventor의 "unspecified" 방향은 기기 회전을 따른다.
  - 프레임이 회전하면 창과 베젤도 함께 돈다.

**[P3b] 기울기 패드(최소형)**
- 점을 끌면 `setPhysicalModel(ROTATION, [x, 0, z₀ + dz], STEP)`을 보낸다(z₀ = 현재 회전 버튼 각도). 같은 스레드에서 갱신을 합쳐 최대 20 Hz로 보낸다.
  - **S7: 회전 벡터는 [x = 앞뒤로 기울임, y = 세로축 회전, z = 화면 회전]이다. y는 가속도를 바꾸지 않는다**(중력과 같은 축) → 패드의 위아래는 x, 좌우는 z의 작은 변화로 보낸다.
- 더블클릭하면 가운데로 돌아간다. AccelerometerSensor·OrientationSensor 게임용이다.
- **현재 회전 축에서 최대 20°까지만** 기울인다(S7: 가로 자세는 26°부터 프레임이 PORTRAIT로 뒤집힘, 24°까지 유지). 20°에서 가속도 성분은 3.36 m/s²(중력의 34%)다.

**[P3b] 위치**
- **프레임워크 테스트 제공자로 넣는다**(S7 결과로 변경): `cmd location providers set-test-provider-location gps|fused --location <lat>,<lon> --accuracy 5`를 두 제공자에 보낸다. 제공자 준비는 `session_init`(§6.6)에서 한다. 기본값은 서울시청이고 프리셋을 둔다.
  - gRPC `setGps`·콘솔 `geo fix`는 쓰지 않는다: App Inventor LocationSensor는 Google Play 서비스 fused 제공자에 BALANCED로 요청하고, 그 요청은 GNSS를 켜지 않아 에뮬레이터 GPS 값이 앱까지 오지 않는다(S7, GOLDEN·COLD 모두).
  - 앱은 1 s 간격으로 보낸 값을 수 초 안에 받는다(S7: 세 번 보낸 뒤 "loc 37.5665,126.9780"). 위치를 바꾸면 값을 1 s 간격으로 2–3번 보낸다.
  - 테스트 제공자 위치는 `Location.isMock()`이 참이다. App Inventor는 이 값을 보지 않는다.
- **위치 힌트**(위치 컨트롤 옆과 `LOCATION` 칩):
  - "App Inventor LocationSensor 기본값은 TimeInterval 60000 ms·DistanceInterval 5 m라서, 첫 위치 뒤에는 약 1분마다·5 m 이상 움직였을 때만 LocationChanged가 옵니다. 빨리 보려면 TimeInterval을 1000으로 바꾸세요."(P)
  - "권한 대화상자에서 '대략적인 위치'를 고르면 10분에 한 번만 갱신됩니다 — [이 앱 권한 모두 허용]으로 정확한 위치를 허용하세요."

**[P4]** 조도·근접(`setPhysicalModel`), 배터리(`setBattery`), 문자·전화 수신(`sendSms`/`sendPhone`).
**진동:** gRPC 신호가 없다(I). [P3a]에서는 VIBRATE INFO 칩(§7.4)만 보여준다. **[P4]** 표시는 `dumpsys vibrator_manager`로 한다: HelloPurr의 Vibrate(500)가 `effect | finished | duration: 531ms | … usage: TOUCH | <pkg>` 줄로 남았다(S5).

### 9.8 오디오·마이크·카메라

- **소리:** 에뮬레이터가 DirectSound로 직접 내며 앱이 할 일은 없다(U).
  - S5: 게스트는 소리를 낸다. gRPC `streamAudio`는 소리가 날 때만 패킷을 보낸다(고양이 소리 213패킷, TTS, 16비트 최대의 3–5% 레벨). 무음 대체 경로가 필요해지면 이 스트림을 쓴다.
  - S3에서 **Orcus 창이 앞에 있을 때와 최소화했을 때** 모두 확인한다.
  - 무음이면 GPU `host_hidden`(L2)을 시도한다. 그래도 안 되면 `streamAudio` → `QAudioSink`(PySide6-Addons, +40 MB)로 가는데, S3 결과가 그것을 요구할 때만 한다.
- **[P3b] 마이크:**
  - 기본은 꺼짐이다(에뮬레이터가 0으로 채움, P).
  - 토글하면 `setMicrophoneState(realAudioEnabled=true)`를 런타임에 보낸다. 재실행이 필요 없고 골든에도 영향이 없다.
  - RECORD_AUDIO 앱에는 켜기를 제안한다. 동의는 패키지마다 기억하고, 실행할 때 다시 적용한다.
  - 켜져 있는 동안 빨간 "마이크 켜짐"을 표시한다. 녹음이 무음이면 Windows '데스크톱 앱의 마이크 액세스' 설정을 안내한다.
  - S3: SoundRecorder 녹음·재생 흐름은 마이크 꺼짐·켜짐 모두 동작한다(3gp 약 13 KB, AfterSoundRecorded). 이 PC에는 녹음 장치가 없어(`dsound: Could not initialize DirectSoundCapture`) 실제 소리는 확인하지 못했다 → 녹음 장치가 없으면 [마이크 켜기]를 끄고 "이 PC에 마이크가 없습니다"를 보여준다. 장치 유무는 에뮬레이터 로그의 이 줄로 안다.
- **카메라:** `hw.camera.back=emulated`(**소프트웨어 가상 카메라**, P).
  - Camera·Camcorder는 가짜 영상을 받는다. BarcodeScanner는 실제 바코드를 비출 수 없으므로 인식할 수 없다(§7.4 문구).
  - **확인 항목(Phase 2에서 미실시 → [P4] 착수 전):** `-camera-back imagefile:D:\OrcusAPK\camera\current.png`(고정 ASCII 경로)를 확인한다. OrcusProbe의 BarcodeScanner는 외부 스캐너를 쓰므로(1501) 내장 스캐너용 픽스처가 따로 필요하다.
    - 헤드리스 37.2.12에서 동작하는가
    - 스캔하는 사이에 파일을 바꾸면 반영되는가
    - 골든 유효성에 영향이 있는가(카메라 설정은 스냅샷 호환성에 들어갈 수 있음)
    - 모두 되면 **[P4]** [카메라에 이미지 보여주기](QR PNG 드롭)로 BarcodeScanner·Camera 수업을 시험할 수 있게 한다. 안 되면 웹캠 옵션(**[P4]**, AVD 변경이라 재구축)으로 대신한다.

---

## 10. GUI (`gui`)

### 10.1 창 구성

```
+-- Orcus APK Emulator ------------------------------------------------------------------+
| [Open APK] [Run again F5] [Stop app] [Clear data+run] [Reset phone] [Shot] [Settings] [?] |
+----------------------------------------+------+----------------------------------------+
|      .------ bezel (QPainter) ------.  | Pwr  | [App] [Sensors] [Log]        (collapse) |
|      |                              |  | Vol+ | App: icon, label, package, version     |
|      |  screen 9:16, 1:1 blit       |  | Vol- |   AI2 build-era badge, info/warn chips  |
|      |  (~432x768 on 1920x1080)     |  | RotL |   [Allow all perms] [Clear] [Uninstall] |
|      |  overlays: boot / drop /     |  | RotR |   [Match orientation] [Copy report]    |
|      |  toast / banner / IME bubble |  | Shake| Sensors: Shake(+sensitivity), Tilt,    |
|      '------------------------------'  | (ga) |   Location(+hint), Mic (-> AI2 event)   |
|            [<]    [O]    [#]           |      | Log: Problems cards / Raw log toggle   |
+----------------------------------------+------+----------------------------------------+
| * Ready . Android 16 . GPU host . clean-phone mode | "TalkToMe running . 4.8 s"  mic off |
+----------------------------------------------------------------------------------------+
```

- **실제 문구는 한국어다:** [APK 열기] [다시 실행] [앱 중지] [데이터 지우고 실행] [휴대폰 초기화] [스크린샷] [설정] [도움말]. 탭은 [앱] [센서] [로그]. 상태 표시는 "준비됨 · Android 16 · GPU host · 깨끗한 휴대폰 모드".
- **폰 영역:** §9.1의 9:16 규칙을 따른다.
- **베젤:** QPainter로 그린 일반적인 둥근 프레임이고 제조사 표식은 없다. 가로 모드에서는 베젤과 화면이 제자리에서 돈다.
- **오른쪽 패널:** 접을 수 있다(폰만 보이는 작은 모드).
- **[P3a]와 [P3b] 범위:** [P3a]에서는 툴바, 폰 화면, 베젤 키, [앱]·[로그] 패널(앱 카드, 문제 카드, 원본 로그)이 동작한다. [P3b]에서 [센서] 패널, 한글 입력 바, 설정 대화상자가 추가된다. [P3a]의 툴바 [설정]은 GPU 모드 대화상자를 연다.

### 10.2 피드백 채널 (심각도마다 하나, 코드 → 채널 표로 연결)

| 종류 | 위젯 | 예 |
|---|---|---|
| 진행 | 폰 화면 위 반투명 오버레이 + 단계 문구 + 솔직한 예상 시간 | "휴대폰 켜는 중 (약 5초)", "설치 중" |
| 성공·정보 | 폰 상단 토스트(3 s) | "실행 중 · 4.8초", "휴대폰 초기화 완료" |
| 지속 경고 | 화면 상단 배너(닫을 때까지) | 소프트웨어 렌더링, 마이크 켜짐, TTS 없음, 레거시 앱, 방향 불일치 |
| 결정 필요 | 모달: 쉬운 한국어 + [자세히](원문) + 주 버튼 하나 | 서명 불일치 교체, AAB/AIA/AIX 안내, 닫을 때 데이터 안내 |
| 엔진 실패 | 폰 영역 전체 패널: 원인, 해결 버튼, 로그 마지막 20줄 | WHPX 꺼짐 → [Windows 하이퍼바이저 플랫폼 켜기], 다른 Orcus가 사용 중, 부팅 시간 초과 → [다시 시도] [로그] |
| 앱 문제 | 폰 위 빨간 배너(크래시) 또는 [로그] 탭의 '문제' 카드 | 크래시, ANR, 블록 실행 오류, 컴포넌트 오류(1501·2701·908…), TTS·음성 인식 없음 |

- 모든 문장은 학생이 읽는다고 보고 쓴다. 내부 용어("S1", "이미지")는 쓰지 않는다. 문장마다 **다음에 할 일**을 함께 주고, 기술 코드는 작게 보여준다.
- **화면 꺼짐:** `EngineStatus.display_on=false`이면 "클릭하면 화면이 켜집니다" 오버레이를 띄운다. 클릭하면 Power 키를 보낸다.

**코드 → 채널 표** (`common/messages_ko.py`. 모든 코드가 정확히 한 채널에 속하는지 유닛 테스트로 검사한다. 단, FAILED 상태의 `error`는 코드와 상관없이 엔진 실패 패널에 보여준다)

| 코드 | 채널 |
|---|---|
| `SETUP_*` | 설치 진행 화면(CLI 터미널 / [P4] 마법사)의 실패 줄 + IT 요청문 |
| `PRE_*`, `ENGINE_*`(아래 넷 제외) | 엔진 실패 패널 |
| `ENGINE_GPU_SOFTWARE`, `ENGINE_ADB_LOST`, `ENGINE_RESET_FAILED`, `ENGINE_DISK_LOW` | 지속 경고 배너 |
| `FILE_*`, `STAGE_*`, `APK_PARSE`, `INSTALL_*` | 결정 필요 모달 |
| `APP_CRASH` | 폰 위 빨간 배너 + 문제 카드 |
| 그 밖의 `APP_*` | [로그] 탭의 문제 카드(필요하면 배너도) |

### 10.3 패널

- **앱 [P3a]:**
  - 아이콘, 이름, 패키지, 버전, min/target SDK, App Inventor 빌드 시기를 보여준다.
  - 칩을 누르면 설명이 나온다: NFC·Bluetooth·Serial 미지원, 마이크, 음성 인식, 스캐너, 레거시, ARM 번역, 진동, 위치, 가로 고정. TTS 칩은 기기 기능 탐지와 런타임 로그에서만 온다.
- **센서 [P3b]:** 흔들기(크게, 감도 포함), 기울기 패드, 회전, 위치(위도·경도, 프리셋, 힌트), 마이크. 각 컨트롤 옆에 그것이 일으키는 App Inventor 이벤트를 적는다.
- **로그 [P3a]:**
  - 기본은 '문제' 카드 보기다(§7.8의 분류, 설치 실패). 카드마다 시간, 한 줄 원인, "해 볼 것", [자세히]가 있다.
  - '원본 로그' 토글은 `ApkService.subscribe_log`로 앱 PID의 logcat을 고정폭 글꼴로 보여준다. 수준별 색, 최근 2,000줄, [저장].

### 10.4 설정 대화상자 [P3b] (최소형)

- 한글 붙여넣기 방식, 흔들기 기본 감도
- 권한 자동 허용(-g, 기본 끔), 실행할 때마다 휴대폰 초기화(끔)
- GPU 모드(`host` / `host_hidden` / `software`, 바꾸면 재구축 확인), **[P4]** 자동 사다리, **[P4]** 세션 간 휴대폰 유지
- Esc = 뒤로, 오른쪽 클릭 = 뒤로
- 런타임 폴더(읽기 전용), [로그 폴더 열기]
- [P3a]에는 GPU 모드만 고르는 작은 대화상자가 먼저 들어간다(툴바 [설정]과 배너에서 연다, §5.6).
- `update_settings`의 `ApplyResult`에 따라 "다시 시작 필요" / "휴대폰 다시 준비 필요(약 3분)" 확인을 받는다.

### 10.5 설치 UI

- **[P2] `orcus-cli setup`:**
  - `SetupEvent`를 터미널에 진행률로 그린다.
  - **라이선스 단계는 사람이 직접 입력해야 한다.** 대화형 콘솔에서 전문을 보여주고, 사용자가 "동의"를 직접 쳐야 넘어간다.
  - `--yes` 같은 우회 옵션이나 환경변수는 없다.
  - 기록에 `accepted_by`(Windows 사용자)를 남긴다.
  - 사용자가 자기 터미널에서 이 단계를 마친 뒤에만 나머지 단계를 자동으로 진행한다.
- **[P4] GUI 설치 마법사:** 같은 `SetupApi` 단계 모델을 그린다. 순서는 §11.6과 **같다**: 환영 + PC 점검 → 라이선스 → WHPX 켜기 → 다운로드 → 재부팅·자동 재개 → 가속 확인 → AVD → 휴대폰 준비 → 완료.
  - 라이선스는 끝까지 스크롤해야 [동의]가 켜진다. 구속력 없는 한국어 요약을 함께 보여준다.
- **이 PC에서 Phase 3 GUI가 처음 뜰 때:** 설치가 안 되어 있으면(`PRE_NOT_INSTALLED`) "설치가 필요합니다: `orcus-cli setup`"을 안내한다.

### 10.6 문자열

모든 문자열은 `common/messages_ko.py` 한 파일에 둔다(Qt 없음. CLI도 같이 쓴다). 영어는 **[P4]**다. 이모지 글리프는 쓰지 않고 아이콘은 SVG로 한다.

---

## 11. 런타임 레이아웃·설치 (`setup`)

### 11.1 디렉터리

```
D:\OrcusAPK\                       ORCUS_HOME: ASCII, short, NTFS, outside Drive
  sdk\emulator\                    37.2.12 (Pkg.BuildId=16428233, lib\emulator_access.json, *.proto,
                                   netsimd.exe?, crashpad_handler.exe?)
  sdk\platform-tools\              37.0.1 (adb.exe)
  sdk\platforms\                   empty, required by SDK-root validation (P)
  sdk\system-images\android-36\google_apis\x86_64\     (the zip's top-level x86_64\ moved here)
  avd\orcus36.ini
  avd\orcus36.avd\                 config.ini, userdata*, snapshots\orcus_golden\ (+ [P4] orcus_user_a|b\)
  home\                            ANDROID_EMULATOR_HOME = ANDROID_USER_HOME, shared adbkey(.pub) (§6.4)
  tmp\                             TMP/TEMP of children
  downloads\                       *.part (resumable); zips deleted after verified extraction
  staging\                         <sha256>.apk (keep the last 20)
  camera\                          [P4 if S5 passes] current.png for -camera-back imagefile
  logs\                            orcus-*.log, emulator-*.log, adb-server.log, setup.log (14-day rotation)
  state\                           settings.json, setup.json, licenses.json, provision.json, device_caps.json,
                                   ports.json, engine.lock, engine_owner.json, ([P4] gpu.json, keep_state.json)
  app\                             installed program ([P4] installer, per-user, no admin)
D:\OrcusDev\                       developer only: venv\, pip-cache\, pycache\, pytest\, build\, dist\,
                                   pyinstaller\, fixtures\ (APKs), src-copy\ (packaging)
D:\Project_Lab\09_프로그램_개발\Orcus_APK_Emulator\   source, docs, vendored protos, small fixtures only
```

### 11.2 AVD 파일 (`common/avd.py`가 만든다)

`avd\orcus36.ini`
```
avd.ini.encoding=UTF-8
path=D:\OrcusAPK\avd\orcus36.avd
target=android-36
```

`avd\orcus36.avd\config.ini`
```
hw.cpu.arch=x86_64
abi.type=x86_64
image.sysdir.1=system-images\android-36\google_apis\x86_64\
tag.id=google_apis
hw.lcd.width=720
hw.lcd.height=1280
hw.lcd.density=320
hw.ramSize=2560
hw.cpu.ncore=4
disk.dataPartition.size=6G
hw.keyboard=yes
hw.mainKeys=yes
hw.gpu.enabled=yes
hw.gpu.mode=host
hw.audioInput=yes
hw.audioOutput=yes
hw.camera.back=emulated
hw.camera.front=none
PlayStore.enabled=false
fastboot.forceColdBoot=no
firstboot.saveToLocalSnapshot=no
firstboot.bootFromLocalSnapshot=no
```
- `hw.cpu.arch=x86_64`는 필수다. 기본값이 arm이다(P).
- `hw.ramSize=2560`: 37.2.12는 이 이미지(API 36)를 2560 MB 아래로 띄우지 않는다. 2048을 주면 "Increasing RAM size to 2560MB"를 남기고 `hardware-qemu.ini`만 2560으로 쓴다(S6 실측). 숨은 보정이 없도록 처음부터 2560으로 둔다. 에뮬레이터는 `config.ini`를 고치지 않았다(첫 부팅 전후 해시 같음).
- AVD 이름에는 `[A-Za-z0-9_.-]`만 쓸 수 있다(P).
- `hw.mainKeys=yes`가 이 키의 기본값이다(P). **그러나 API 36에서는 게스트 내비게이션이 숨지 않는다**(S7 실측: 제스처 내비 `navigation_mode=2`).
  - 문제: 마우스로는 제스처 내비를 쓸 수 없고, 가장자리 제스처가 PaintPot 같은 Canvas 드래그를 가로챌 수 있다.
  - **대응(S7에서 확인):** 준비 단계에서 `cmd overlay enable-exclusive --category com.android.internal.systemui.navbar.threebutton` → `navigation_mode=0`(3버튼 내비). 가장자리 제스처가 없어지고, 게스트 화면 아래에 ◁ ○ ▢ 바가 보인다(베젤 버튼과 중복이지만 해롭지 않다). 설정은 userdata에 남아 골든에 들어간다.
  - Back/Home/Recents는 베젤 버튼이 `GoBack`/`GoHome`/`AppSwitch` keypress로 보낸다(S7: 셋 다 동작).
- 키가 하나라도 바뀌면 `config.ini` 해시가 달라져 골든을 재구축한다.

### 11.3 설정 파일

- **우선순위:** 명령줄 > `ORCUS_*` 환경변수 > `state\settings.json`(GUI가 원자적으로 기록) > 프로그램에 포함된 `defaults.toml`(`tomllib`으로 읽음).
- **기본값:**
  - **엔진:** `engine.ram_mb=2560`(이미지 최소값, §11.2), `engine.cores=4`, `engine.gpu_mode="host"`(S2 결과에 따라 `"host_hidden"`, **`"auto"`라는 값은 없음**), `engine.idle_grpc_timeout_s=120`, `engine.keepalive_s=30`, `engine.ports={console 5640, grpc 8640, adb_server 15037}`, `engine.locale="ko-KR"`, `engine.timezone="Asia/Seoul"`
  - **실행:** `apk.grant_all=false`, `apk.reset_before_each_run=false`, `apk.staging_keep=20`, `session.keep_state=false`([P4]), `ui.confirm_close_data=true`
  - **입력:** `input.paste_method="paste_key"`, `input.esc_is_back=true`, `input.right_click_back=true`
  - **화면:** `frame.flip_rows=false`, `frame.max_fps=60`
  - **센서:** `sensors.shake_default="moderate"`, `sensors.shake_sensitivity={}`(패키지별), `sensors.location_presets=[[37.5665, 126.9780, "서울시청"]]`
  - **개인정보:** `privacy.mic_allowed_packages=[]`
- **고정값** `common/pins.py`(앱 안에 포함, 사용자가 편집하지 않음): 에뮬레이터·platform-tools·이미지의 URL, 크기, SHA-1, build id.

### 11.4 루트 결정과 가드

- **결정 순서:** `--home` > `ORCUS_HOME` > `<exe 폴더>\orcus_home.txt`(설치 프로그램이 기록) > `D:\OrcusAPK`.
- **실행을 거부하는 경우:**
  - 경로에 비 ASCII 문자가 있음(37.2.12에는 비 ASCII AVD 경로에서 멈추는 버그가 있음, P)
  - 경로가 40자를 넘음
  - NTFS가 아님
  - 조상 폴더에 `.tmp.driveupload` / `.tmp.drivedownload`가 있음(Drive 동기화 트리, 오늘 `D:\Project_Lab`이 해당)
  - `G:\`(Drive 가상 드라이브)
  - `%LOCALAPPDATA%\Packages` 또는 `WindowsApps` 아래
  - `sys.executable`이 `WindowsApps` 아래(Store Python)이거나 3.14가 아님

### 11.5 C:에 남는 것 (A6: 비교 범위와 허용 목록)

- **비교 범위:** 설치·실행 전후로 아래 폴더와 레지스트리만 비교한다. C:의 나머지는 비교하지 않는다.
  - `%USERPROFILE%` 최상위와 그 아래 `.android`·`.cache`
  - `%LOCALAPPDATA%`(Temp\avd 포함)
  - `%APPDATA%`, `%ProgramData%`
  - `HKCU\Software`(RunOnce 포함)
- **허용 목록:**

| 위치 | 내용 |
|---|---|
| `%USERPROFILE%\.emulator_console_auth_token` | 에뮬레이터가 항상 기록(P) |
| `%USERPROFILE%\.android\` | 사용자 adb 키 `adbkey`, `adbkey.pub`. adb는 `ANDROID_USER_HOME`을 무시하므로(P), 공유 키를 써도 adb가 만들 수 있다 |
| `%LOCALAPPDATA%\Temp\avd\running\` | discovery 파일(위치 고정, P) |
| HKCU `...\RunOnce` | 설치 중 재부팅 뒤 재개용 1개. 실행 후 자동 삭제 |
| `%APPDATA%`, `%ProgramData%` | 아무것도 없어야 함 |

- **판정 방법:**
  - 전후 비교에서 OS가 남기는 기록은 뺀다: `NTUSER.DAT*`, Explorer 기록(UserAssist, MuiCache, RecentDocs, ComDlg32, `%APPDATA%\Microsoft\Windows\Recent`), 다른 앱의 Temp·캐시.
  - 남는 변화는 경로와 시각으로 우리 프로세스(python/OrcusAPK, emulator, qemu, adb, dism, emulator-check, powershell)가 만든 것인지 판단해 기록한다.
  - 사용자가 허용하면 Process Monitor로 우리 프로세스의 쓰기만 걸러 확인한다.
- 이미 설치된 Python 3.14 런타임(개발용)은 예외다. QSettings는 쓰지 않는다.
- **개발 캐시는 모두 `D:\OrcusDev`로 돌린다**(§11.7). pip, pyright(npm), npm 캐시를 기본값으로 두면 C:에 수십~수백 MB가 생긴다.

### 11.6 설치 순서 [P2]

`setup.steps`로 구현한다. CLI와 [P4] 마법사가 같은 단계 모델을 쓴다. 각 단계는 멱등이며, `state\setup.json`으로 중단한 곳부터 이어서 실행한다.

1. **PC 점검**(아무것도 바꾸지 않는다):
   - 인터프리터(Store가 아닌 3.14), 루트 가드, D: 여유 공간 ≥ 24 GiB(§6.8), CPU 가상화를 확인한다.
   - **WHPX 상태**(`common/accel.py`):
     - 조회 명령: `powershell.exe -NoProfile -NonInteractive -Command "(Get-CimInstance Win32_OptionalFeature -Filter \"Name='HypervisorPlatform'\").InstallState"`. 관리자 권한이 필요 없고 약 0.7 s 걸린다(이 PC에서 확인).
     - 값: 1 = 켜짐, 2 = 꺼짐, 3 = 없음, 4 = 알 수 없음.
     - **`WinHvPlatform.dll`이 있는지나 `WHvGetCapability(HypervisorPresent)` 결과로 판단하지 않는다.** 이 PC는 기능이 꺼져 있는데도 DLL이 있고, Hyper-V가 돌고 있어서 HypervisorPresent=1을 돌려준다.
     - 값이 1이면 3단계를 건너뛰고, 7단계의 `emulator-check accel`이 판단하게 한다.
     - `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending` 키(일반 권한으로 읽을 수 있음)는 **재부팅을 기다리는 서비스 작업이 있다**는 뜻일 뿐이다. Windows 업데이트일 수 있고, WHPX와 무관할 수 있다. 그래서 7단계에서 가속 확인이 실패했을 때 원인을 설명하는 데만 쓴다.
   - **관리자 토큰이 아니면** IT 요청문을 **경고**로만 보여준다. 일반 사용자도 UAC 창에 관리자 자격 증명을 넣을 수 있으므로 여기서 멈추지 않는다.
   - **AhnLab V3:** EPP로 중앙 관리되는지 기록한다(S0a).
2. **라이선스**(사람만 할 수 있음, §10.5):
   - `https://dl.google.com/android/repository/repository2-4.xml`과 `…/sys-img/google_apis/sys-img2-5.xml`을 받는다. 없으면 -3 버전을 쓴다.
   - 세 패키지의 `<uses-license ref>`가 모두 stable 채널의 `android-sdk-license`인지 확인한다. 아니면 `SETUP_LICENSE_NOT_STABLE`이다.
   - 전문을 보여주고, 동의를 `licenses.json`에 기록한다: `{license_id, sha256(text), accepted_at, accepted_by, packages}`. 전문의 해시가 바뀌면 `SETUP_LICENSE_CHANGED`로 다시 묻는다.
3. **WHPX 켜기**(InstallState가 1이 아닐 때만):
   - **실행:** `ShellExecuteExW`를 다음 값으로 부른다.
     - `lpVerb="runas"`
     - `lpFile`: `GetSystemDirectoryW()`로 얻은 경로 + `\dism.exe`. 환경변수는 쓰지 않는다(쓰려면 `SEE_MASK_DOENVSUBST`가 필요하다).
     - `lpParameters="/online /enable-feature /featurename:HypervisorPlatform /all /norestart"`
     - `fMask=SEE_MASK_NOCLOSEPROCESS|SEE_MASK_NOASYNC`, `nShow=SW_HIDE`
   - **결과 처리:**
     - `ShellExecuteExW`가 FALSE이고 `GetLastError()`가 1223(`ERROR_CANCELLED`)이면, 사용자가 UAC를 거절한 것이다. `SETUP_UAC_DENIED` + IT 요청문을 보여준다. 이때 dism은 시작되지 않았고 `hProcess`는 NULL이다.
     - 그 밖의 FALSE(정책이 권한 상승을 자동으로 거부한 경우 등)는 `SETUP_DISM_FAILED` + IT 요청문.
     - TRUE면 `WaitForSingleObject(hProcess)` → `GetExitCodeProcess`로 종료 코드를 읽는다.
       - 0 또는 3010: 성공. 3010이면 `reboot_pending`을 기록한다.
       - 3017(`ERROR_FAIL_REBOOT_REQUIRED`: 실패했고 되돌리려면 재부팅 필요)과 그 밖의 코드: `SETUP_DISM_FAILED` + IT 요청문.
     - InstallState가 3(없음)이면 `/Source`가 필요할 수 있어서, 대개 `SETUP_DISM_FAILED`로 끝난다.
     - 실패하면 기본적으로 **다운로드 전에 멈춘다.** 사용자가 [그래도 다운로드]를 누르면 계속한다.
   - 재부팅 관문(6단계)은 이번 실행의 dism이 3010을 돌려준 경우에만 연다. InstallState는 재부팅 전에도 1을 보일 수 있기 때문이다.
   - .ps1 파일은 쓰지 않는다. 관리되는 Education PC의 실행 정책이 막을 수 있다.
   - dism은 호출자의 Job에 들어간다(S0a 실측). CLI setup은 이 단계에서 아직 우리 Job이 없다(§1.2). 이 PC에서 dism은 25 s 걸렸고 종료 코드는 3010이었다.
4. **다운로드**(재부팅을 기다리는 중에도 계속한다):
   - 표준 라이브러리 urllib, HTTP Range로 이어받기, `.part` 파일, 크기와 SHA-1 검증.
   - 네트워크 오류는 `SETUP_NETWORK`, 해시 불일치는 `SETUP_CHECKSUM`이다.
   - 대상:
     - `emulator-windows_x64-16428233.zip`: 455,342,868 B, SHA-1 `488ed747e82de7e9bb5247becd1ac043c7e5e85d`
     - `platform-tools_r37.0.1-win.zip`: 8,044,989 B
     - google_apis 36 r7 x86_64: 1,895,447,397 B
   - platform-tools와 이미지의 SHA-1은 Phase 2에서 인덱스로 읽어 `common/pins.py`에 고정한다.
   - 고정한 build가 인덱스에서 빠지면 고정 URL과 고정 SHA-1로 받는다. 값이 다르면 오류로 끝내고, 조용히 업그레이드하지 않는다.
5. **압축 해제:**
   - **최종 위치(`sdk\<패키지>`)에 바로 푼다.** 모든 항목을 먼저 검사한다(zip-slip, Windows 금지 문자).
   - **풀고 나서 폴더를 옮기지 않는다.** 이 PC에서는 Windows Defender(실시간 검사 켜짐)와 V3가 새 실행 파일을 잠깐씩 열어 둔다. 처음 설계(`tmp\extract_*`에 풀고 이름 바꾸기)는 첫 실행에서 "액세스 거부"(WinError 5)로 실패했다(S0b, 2026-10-30).
   - 시작할 때 `<패키지>.partial` 표시 파일을 만들고, 검증이 끝나면 지운다. 표시가 남아 있으면 설치되지 않은 것으로 본다. 다음 실행에서 그 폴더를 지우고 다시 푼다.
   - 폴더·파일 삭제는 잠금이 풀릴 때까지 최대 2분 재시도한다. 그래도 잠겨 있으면 `SETUP_QUARANTINE`을 "검사 중이라 잠김" 문구로 보여준다.
   - 핵심 파일이 있는지 확인한다: `Pkg.BuildId`, `emulator.exe`, `qemu-system-x86_64-headless.exe`, `qemu-system-x86_64.exe`, `adb.exe`, `emulator_access.json`, `*.proto`.
     - 없으면 격리(`SETUP_QUARANTINE`, "파일이 사라짐")로 보고, **복사 가능한 IT 요청문**(경로, build, 실행 파일, 이유. V3와 Defender 모두)을 보여준다.
   - `netsimd.exe`·`crashpad_handler.exe`가 없으면 경고만 한다.
   - 빈 `sdk\platforms\`를 만들고 zip을 지운다.
6. **재부팅 관문:** `reboot_pending`이면 "Windows를 다시 시작하면 설치가 이어집니다"를 보여주고 HKCU RunOnce를 등록한다.
7. **가속 최종 확인:** setup은 이 단계 직전에 Job을 만든다(§1.2).
   - `emulator-check.exe accel`을 **단독으로** 실행한다. 여러 검사를 함께 돌리면 검사마다 결과 블록이 나오고, Hyper-V가 켜진 이 PC에서는 hyper-v 블록이 코드 2를 낸다.
   - accel 블록에 `WHPX(`와 `is installed and usable`이 있어야 한다. Windows 빌드 번호는 고정하지 않는다.
   - **이 검사는 필요조건일 뿐이다.** 이 PC에서는 dism(3010) 직후 재부팅 전에도 'usable'을 보고했다(S0b). 그래서 6단계 재부팅 관문을 이 단계보다 먼저 두고, 가속이 실제로 되는지는 에뮬레이터를 부팅하는 `--boot-check`·S4에서 확인한다.
   - 실패했는데 RebootPending 키가 있으면 `SETUP_REBOOT_PENDING`: "Windows를 다시 시작해야 합니다."
   - 재부팅했는데도 꺼져 있으면(RebootPending 없음) `SETUP_WHPX_REVERTED`: "재부팅 뒤에도 기능이 꺼져 있습니다. PC 복원 프로그램이나 정책이 되돌렸을 수 있습니다." + IT 요청문.
   - 그 밖의 실패는 `SETUP_ACCEL_FAILED`. TCG로 대체하는 경로는 없다.
   - 결과는 Windows 부팅 시각(마지막 부팅 시각)을 키로 캐시한다. PREFLIGHT가 이 캐시를 쓴다.
8. **AVD 작성과 공유 adb 키:**
   - AVD를 쓴다(§11.2). userdata를 만들기 전에 1.2 × 6 GiB + RAM만큼 비어 있는지 확인한다.
   - `adb keygen D:\OrcusAPK\home\adbkey`로 공유 adb 키를 만든다. 이미 있으면 그대로 둔다(§6.4).
   - **여기까지 끝나면 NOT_INSTALLED에서 벗어난다.**
9. **첫 준비:**
   - **[P3a]** 주입된 `EngineControl.start()`를 부른다(PREFLIGHT → PROVISIONING → READY). 그 `EngineStatus`를 설치 진행률로 그대로 보여준다. setup은 엔진을 직접 만들지 않는다.
   - **[P2]**에서는 스파이크 하네스가 이 단계를 맡는다.
10. **AhnLab V3 예외(선택):**
    - 사용자나 IT 담당자가 `D:\OrcusAPK\sdk`, `\avd`를 예외로 추가한다.
    - V3 설정은 스크립트로 바꾸지 않는다. EPP로 관리되는 PC면 IT 요청문을 쓴다.

### 11.7 개발 환경 (Drive 위생) [P2]

- **venv:** `%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe -m venv D:\OrcusDev\venv`(절대 경로).
- **도구는 Python으로만 만든다:** `tools/bootstrap.py`, `tools/dev.py`, `tools/gen_protos.py`, `tools/fetch_fixtures.py`, `tools/spikes/`(버리는 하네스, 패키징하지 않음).
  - `.bat`·`.ps1`은 없다. cmd는 한글 경로로 cd하지 못하고, PowerShell 5.1은 BOM이 필요하다(사용자 환경 기록).
- **`tools/bootstrap.py`:** pip를 부르기 **전에** `PIP_CACHE_DIR=D:\OrcusDev\pip-cache`를 설정한다. 첫 설치 때부터 C:에 휠 캐시가 생기지 않게 하기 위해서다.
- **`tools/dev.py`가 설정하는 것:**
  - `PYTHONPYCACHEPREFIX=D:\OrcusDev\pycache`, `PIP_CACHE_DIR`
  - `PYRIGHT_PYTHON_CACHE_DIR=D:\OrcusDev\pyright`, `npm_config_cache=D:\OrcusDev\npm-cache`. pyright PyPI 래퍼는 npm으로 pyright를 받는다(P). 이 PC에는 node가 있다.
  - pytest `cache_dir`과 `--basetemp`(`D:\OrcusDev\pytest`), `PYINSTALLER_CONFIG_DIR`
  - 그다음 test / typecheck / run / build를 실행한다.
- **패키징:**
  - 소스를 `D:\OrcusDev\src-copy`로 복사한 뒤 PyInstaller·ISCC를 실행한다. Drive가 새로 쓴 파일을 잠깐 잠그기 때문이다.
  - ISCC는 subprocess 인자 목록으로 호출한다. Git Bash가 `/D` 인자를 경로로 바꾸는 문제를 피하기 위해서다.
- **proto:**
  - Phase 2에서 37.2.12 `emulator\lib\*.proto`의 Apache-2.0 헤더를 확인한다(라이선스 §3.5). 확인되면 `src/orcus/emugrpc/protos/16428233/`에 NOTICE와 함께 넣는다.
  - 스텁은 개발 시 grpcio-tools로 생성해 저장소에 넣고, import를 패키지 상대 경로로 고친다. 런타임에는 grpcio-tools가 없다.
  - protobuf는 gencode 버전 이상, 8 미만으로 고정한다.

## 12. 테스트 전략

### 12.1 유닛 테스트 (pytest + pyright, 기기 없이 20 s 이내, 변경할 때마다)

| 대상 | 테스트 |
|---|---|
| `common.paths` | ASCII·길이·Drive 조상·WindowsApps·Store Python·NTFS 가드, 루트 결정 순서 |
| `common.childenv` | 설정·제거 키, PATH 정리, `QT_*`·`ANDROID_EMULATOR_*`·`QEMU_*` 차단, adb 서버 전용 변수(`ADB_USB=0`, `MAX_PORT=5553`, `ADB_VENDOR_KEYS`), 프록시 규칙(레지스트리 가짜) |
| `common.avd`·`common.pins` | `orcus36.ini`·`config.ini`가 기준 파일과 바이트 단위로 같음. proto 폴더 이름 = pin build |
| `common.accel` | InstallState 1/2/3/4 판정. **DLL 있음 + HypervisorPresent=1 + InstallState=2 → 꺼짐.** InstallState=1 → dism 없이 7단계로. accel 실패 + RebootPending → `SETUP_REBOOT_PENDING`. accel 실패 + 키 없음 + 이전 dism 3010 → `SETUP_WHPX_REVERTED`. emulator-check 출력(코드 0/2/6) 파싱 |
| `common.proc` | 타임아웃이 나면 실제로 종료하고 반환함(손자 프로세스가 출력 핸들을 쥔 경우 포함) |
| `common.messages_ko` | 모든 `ErrorCode`에 한국어 메시지, 채널(§10.2 표), 해결 동작이 있음. 모든 `RemedyAction`에 처리기가 있음 |
| `engine.args` | 모드 × GPU 조합의 명령줄이 기대값과 정확히 같음. 불변식(§5.2): `-gpu auto` 금지, `-snapshot`이 있으면 `-no-snapshot-save`도 있음 |
| `engine.discovery` | `port.serial` 일치, 죽은 PID·남의 avd.dir 무시, CRLF·BOM·한글 avd.dir·쓰는 중인 파일 |
| `engine.manager` | 가짜 seam(§12.2)으로 모든 전이·명령·실패 코드 시나리오 |
| `device.shake` | 파형(시간·Δ·**부호**·마지막 setPhysicalModel). 이식한 App Inventor 감지기로 '맞는 감도에서 정확히 1번'(역방향 자세 포함) |
| `device.rotation`·`device.input` | 4방향 모서리 왕복, 패널 크기 반영, 프레임 seq 기준 변환, 키 헬퍼는 항상 keypress, 붙여넣기 순서, 재연결·epoch 변화 뒤 release_all |
| `device.controls` | FIFO 순서, 흔들기 중 기울기 합치기, 회전 순서, 기울기 20° 제한 |
| `gui.keymap` | 단축키 중복 없음, 폰 편집 키와 충돌 없음 |
| `apk.intake` | APK/AAB/AIA/AIX/비 zip/깨진 zip 판정, 스테이징 이름 = SHA-256, 한글 원본 경로 |
| `apk.inspect` | package, launcher, launcher 방향, 한글 label, SDK, 권한, `<queries>`, ABI, AI 판정·빌드 시기. MITAI2Companion 2.81u 회귀. androguard 4.1.4 교차 확인(개발용) |
| `apk.compat`·`apk.install` | 사전 점검 행렬(VIBRATE·LOCATION·SCANNER·방향 포함), 설치 플래그, 실패 코드 → 대응 |
| `apk.service` | 빠른 경로는 KEEP_DATA + 같은 epoch일 때만. FRESH_PHONE은 새 epoch까지 기다림(COLD에서는 FRESH_APP). **CRASH_RESTART에서만 재설치(RESET에서는 안 함).** 대기 중 새 드롭 → 취소 |
| `apk.watcher` | logcat 녹취 분류(우선순위 포함): 크래시, ANR, RuntimeErrorAlert, E/Form 1501/2701/908/601, InternalTTS, RECOGNIZE_SPEECH(두 경로), 종료 |
| `setup` | 인덱스 파싱(라이선스 참조, stable 채널), 이어받기, 크기·SHA-1 불일치(로컬 http.server), zip-slip, 라이선스 기록(대화형 수락기 주입), dism 실행 결과: **ShellExecuteExW FALSE + 1223 → UAC 거절**, 그 밖의 FALSE → DISM 실패, TRUE + 종료 코드 0/3010/3017/기타 |
| 레이어·타입 | `test_layering.py`(허용 목록), pyright strict(contracts·경계), Protocol 대입 검사, §3에 이름이 나오는 모든 타입이 contracts에 정의됨 |

### 12.2 가짜 객체 (`src/orcus/emugrpc/fake.py` + `tests/fakes/`)

- **`FakeEmulatorServer`**: src 안에 둔다(테스트와 `--selftest`가 같이 씀). 생성된 servicer로 만든 in-process grpcio 서버이며 Bearer 토큰도 검사한다.
  - 구현하는 RPC: `getStatus`, `get/setVmState`, `streamScreenshot`(합성 프레임), `streamInputEvent`(기록), `get/setSensor`, `setPhysicalModel`, `setClipboard`, `setGps`, `setMicrophoneState`, `streamNotification`, `streamLogcat`.
  - SnapshotService도 구현한다. **`success=false` 응답과 "already exists" 거절**을 각본으로 넣을 수 있다.
- **engine seam 가짜:** `FakeProcessHost`(실행·종료를 각본대로), `FakeJob`, `FakeDiscoveryDir`, `FakeAdbRunner`, `FakePortProber`, `FakeClock`(tick 구동), `FakeChannelFactory`.
- **시나리오 예:**
  - **부팅·골든**
    - 골든 정상 부팅. epoch는 1, `epoch_cause`는 BOOT다.
    - 골든 로드 실패(강제 로드가 스냅샷을 지움) → 재시도 없이 재구축.
    - 골든 부팅 중 `Not enough space`로 조기 종료 → 재구축이 아니라 FAILED(`PRE_DISK`). 분류되지 않은 종료인데 골든 스냅샷 폴더가 그대로 있음 → FAILED(`ENGINE_EXIT`).
    - 게스트 전원 끄기 → STOPPING(stop) → STOPPED. 잠금이 해제된다.
    - 표식이 없음 → STOPPING(rebuild) → PROVISIONING.
    - 재구축 한도를 넘으면 FAILED. [다시 시도]를 누르면 한도가 초기화된다.
  - **COLD(L3)**
    - 시작하면 PROVISIONING 없이 READY가 되고, 배너가 뜬다.
    - COLD 기록이 없으면 PROVISIONING(COLD)을 한 번 거친다.
    - COLD에서 `reset_device()`를 부르면 재구축 요청이 된다.
  - **준비 직후 초기화**
    - 준비가 끝난 바로 뒤에 초기화하면 통과한다. 골든에 비운 nonce가 저장돼 있기 때문이다.
    - epoch가 정확히 1 오르고 `epoch_cause`는 RESET이다.
  - **초기화 실패**
    - `LoadSnapshot`이 `success=false`를 돌려주면 `ENGINE_RESET_FAILED`가 되고, 재시작 원인은 RESET_RECOVERY다.
    - 초기화 뒤 이번 세션의 nonce가 그대로 남아 있으면 실패로 처리한다.
  - **골든 재구축**
    - `SaveSnapshot`이 "already exists"로 거절되는 경우는 미리 Delete하고 List로 확인해 피한다.
    - 그래도 실패하면 콘솔 저장으로 넘어간다.
  - **각 활성 상태에서 `stop`** → STOPPING(stop) → STOPPED. 준비 중이었다면 `provision.json`을 쓰지 않는다.
  - **크래시**
    - READY에서 크래시가 나면 STOPPING(restart)을 거쳐 재시작한다. 이때 STOPPED는 보이지 않는다.
    - ApkService가 재설치하고, 원인은 CRASH_RESTART다. 두 번째 크래시는 FAILED.
  - **FAILED 진입(잠금을 쥔 경우):** DeviceSession이 닫히고, VM이 정리되고, 잠금이 해제된다. 그 뒤 다른 프로세스가 잠금을 얻을 수 있다.
  - **FAILED 진입(잠금이 없는 경우):** 다른 소유자가 잠금을 쥔 상태에서 `ENGINE_IN_USE`로 들어오면 `TerminateProcess`를 한 번도 부르지 않고, 소유자 기록도 바뀌지 않는다.
  - **실행·잠금**
    - 포트가 사용 중이면 다음 포트로 간다.
    - AVD 잠금(`ENGINE_AVD_LOCKED`) 처리.
    - 소유권 잠금을 얻지 못하면 `ENGINE_IN_USE`.
  - **adb**
    - 서버가 죽으면 재시작하고 재등록 메시지를 보낸다(재시작은 3/5분까지).
    - 재시작 중 `DeviceShell` 호출 → `ENGINE_ADB_LOST`.
  - **정지:** 시간 초과 → PID 강제 종료. **`TerminateJobObject`를 호출하지 않았음을 단언한다.**
  - **경합:** 초기화·크래시가 INSTALLING/LAUNCHING 도중에 일어나면 결정적으로 취소·재시작된다.
  - **대기:** `start()` 직후 `wait_ready()`가 바로 실패하지 않는다. NOT_INSTALLED면 `PRE_NOT_INSTALLED`.

### 12.3 기기 통합 테스트 (`pytest -m device`, 실제 AVD, 단계 승인 전과 엔진·이미지 변경 후)

1. **부팅:** 골든에서 READY까지 S4 예산 안. `debug.orcus.golden` = provision id, 렌더러 host, hypervisor WHPX.
2. **정리:** 앱(또는 CLI)을 종료한 뒤, 그리고 테스트 프로세스를 TerminateProcess로 죽인 뒤 N s(S4) 안에 **이미지가 `D:\OrcusAPK\sdk` 아래인 프로세스가 하나도 남지 않는다**(private adb 서버 포함).
3. **프레임:** 첫 프레임이 비어 있지 않고 행 순서가 맞다. 요청 크기와 같고 9:16이다. 회전하면 재요청이 동작한다.
4. **설치·실행:** 픽스처가 설치되고 실행된다. 두 번째는 빠른 경로를 탄다. **초기화 뒤 같은 파일을 드롭하면 다시 설치된다.**
5. **OrcusProbe**(§12.4)의 라벨을 `uiautomator dump`로 읽어 확인한다. Phase 2 스파이크에서 배운 것:
   - dump는 센서 라벨이 계속 바뀌어도 2–4 s에 된다. **IME 창(키보드·Gboard 시트)과 토스트는 dump에 없다** → 화면 픽셀이나 logcat으로 본다. WebView 안 입력란은 dump에서 찾기 어렵다 → 화면 비교.
   - 가로에서는 Canvas가 화면을 채워 아래 라벨이 화면 밖이다 → 좌표 검사는 `pointer_location` 십자선을 프레임에서 찾는 방식(`tools/spikes/fx_s7b.py`)으로 한다.
   - 탭 오차 ±4 패널 px(4방향 회전) — S7: 0–1 px
   - 300 px 드래그에 Dragged 20회 이상. Flung 속도가 드래그 속도에 비례한다
   - 흔들기 클릭 1번 = Shaking 1번(맞는 감도, 역방향 자세 포함)
   - Back → BackPressed
   - "안녕하세요 Orcus"가 TextBox·MultiLine에 정확히 들어간다(NumbersOnly에서는 걸러짐)
   - **ACCESS_FINE_LOCATION을 허용한 상태에서** 테스트 제공자 위치가 10 s 안에 반영된다(Probe는 TimeInterval=1000). 기본 속성 앱은 첫 위치만 확인한다
   - 기울기 패드로 OrientationSensor 값이 바뀐다
   - 가로 고정 Screen2가 [화면 방향 맞추기] 뒤 바르게 보인다
6. **손가락 고착:** 드래그 중 입력 스트림을 끊고 다시 연결한 뒤 탭한다 → 탭이 등록되고 드래그는 이어지지 않는다.
7. **레거시·SDK·ABI:** 레거시(target < 24, 구할 수 있을 때), minSdk 36, ARM 전용(실제 AI2 APK에 `lib/armeabi-v7a/*.so`를 넣은 것 → BLOCK)이 각각 맞는 결과를 낸다.
8. **초기화:** 초기화 뒤 `pm list packages`에 픽스처가 없고, epoch가 정확히 1 오르며, `epoch_cause`가 RESET이다.
9. **재구축:** 유효성 키를 바꾸면 다음 시작이 재구축 → READY.
10. **포트:** 8640을 점유하면 8641로 대체한다.
11. **adb 격리:** 외부 adb를 5037에 띄워도 `adb -P 5037 devices`에 `emulator-5640`이 없다. 우리 서버 목록에 USB 기기와 다른 에뮬레이터가 없다. 우리 서버를 죽였다 살리면 재등록 메시지로 바로 `emulator-5640`이 돌아온다.
12. **소유권:** GUI(또는 `orcus-cli run`)가 VM을 쥔 동안 다른 `orcus-cli run`은 `ENGINE_IN_USE`로 멈추고, 첫 VM은 살아 있다.
13. **App Inventor 오류 경로:**
    - 크래시: `adb shell am crash <pkg>`로 일으킨다. AI2 블록 오류는 대개 RuntimeErrorAlert로 잡히기 때문이다.
    - 블록 실행 오류 버튼, 601, 908(권한 거부), 1501(BarcodeScanner), ANR(Probe의 ANR 버튼)
    - 2701과 음성 인식 없음은 **해당 패키지를 끈 상태에서** 확인한다: `adb shell pm disable-user --user 0 <S1에서 확인한 TTS·음성 인식 패키지>`로 끄고, 끝나면 [휴대폰 초기화]. 2701은 버튼을 누르고 약 12 s 뒤에 온다(App Inventor가 120 × 100 ms 기다림).
    - SpeechRecognizer 경로는 모두 `APP_SPEECH_MISSING` 카드가 떠야 한다(S5 실측, TTS·ASI 패키지를 끈 상태):
      - UseLegacy 켬: 권한 대화상자를 거치는 첫 실행은 FATAL, 이미 허용된 상태는 RuntimeErrorAlert(`No Activity found to handle Intent { act=android.speech.action.RECOGNIZE_SPEECH … }`)
      - UseLegacy 끔: `Bind to system recognition service failed with error 10` + errorNumber 0 "Try Again."
    - 제공자가 있고 마이크가 꺼져 있으면 UseLegacy 끔은 3806 "No Match"다(카드는 마이크 안내).
    - 각각 맞는 카드가 떠야 한다.
14. **단일 인스턴스:** 실행 중에 `.apk`를 인자로 두 번째 실행을 하면 3 s 안에 첫 창에서 열린다.
15. **가로 고정 launcher:** Screen1이 가로 고정인 픽스처를 드롭하면, 실행 전에 기기가 가로로 돌아 있어 화면이 바르게 보인다.

### 12.4 픽스처 (APK는 크기·라이선스 때문에 저장소에 넣지 않고 `D:\OrcusDev\fixtures`에 둔다)

- **OrcusProbe**: `tools/ai2/projects.py`가 1쪽 명세(docs/fixtures.md)대로 `.aia`를 만들고, 사용자가 App Inventor 웹 IDE에서 빌드한다(Phase 2에서 그렇게 만듦). `.aia`는 저장소에 둔다.
  - Canvas: TouchDown/Dragged/Flung(x, y, 속도)
  - 센서: Shaking 횟수(감도 전환 버튼 포함), AccelerometerSensor·OrientationSensor 값 라벨, 방향 라벨, LocationSensor(**TimeInterval=1000, DistanceInterval=0**)
  - 입력: BackPressed 횟수, TextBox / MultiLine / PasswordTextBox / NumbersOnly, **WebViewer**(입력란이 있는 페이지, S8용)
  - 소리·말: TTS 버튼("안녕하세요"), **TTS AvailableLanguages 버튼**(2701), **SoundRecorder 녹음 + Player 재생**(S3 마이크)
  - 오류 경로:
    - SpeechRecognizer GetText 버튼 **두 개**(UseLegacy 켬·끔)
    - **BarcodeScanner 버튼**(UseExternalScanner 기본값 true → 1501)
    - 블록 실행 오류 버튼, ActivityStarter 잘못된 action 버튼(601), 권한 요청 버튼(908 확인용), **ANR 버튼**(10 s 넘게 도는 블록 반복 뒤 화면을 탭)
  - **가로 고정 Screen2**
- **App Inventor 예제**(`.aia`는 `tools/ai2`가 만들거나 공식 튜토리얼대로, 빌드는 사용자가 웹 IDE에서):
  - 스파이크 전: HelloPurr, TalkToMe(Phase 2에서 생성·빌드 완료)
  - **3a 수용 시험 전:** PaintPot, BallBounce, MoleMash, 한글 이름 앱(HelloPurr 이름만 바꿔도 됨), AndroidMinSdk = 36 빌드, 가로 고정 앱(HelloPurr의 ScreenOrientation만 landscape로)
  - **레거시 APK:** 2018년 이전 빌드는 구할 수 있을 때만 쓴다. 없으면 레거시 항목은 '해당 없음'으로 기록한다.
- **MITAI2Companion 2.81u**(18,365,393 B): `tools/fetch_fixtures.py`가 받고 SHA-256을 고정한다. 파서 회귀용이다.
- **ARM 전용:** 실제 AI2 APK에 `lib/armeabi-v7a/x.so`를 넣어 만든다. 서명은 깨지지만, 사전 점검 BLOCK이 설치 전에 걸리므로 상관없다.
- **합성 zip:** `assets/` 아래 ELF, 이름만 바꾼 .aab/.aia/.aix, 잘린 zip, zip이 아닌 파일.

### 12.5 스모크·자체 시험

- **`orcus-cli smoke`(약 1분):** PASS/FAIL 표와 시간을 `logs\smoke-*.txt`에 남긴다. **에뮬레이터·이미지·platform-tools 업그레이드의 관문**이다.
  - **[P3a]** 시작 → 프레임 → 탭 → 키 → HelloPurr 설치·실행 → 붙여넣기 → 초기화 → 정지.
  - **[P3b]** 위 순서에 흔들기·회전·위치를 더한다.
- **`OrcusAPK.exe --selftest`(패키징본) [P2부터]:** 내장 검사만 한다(테스트 코드나 pytest를 쓰지 않음).
  - import, 의존성 버전 대 lock, proto·스텁 해시, `FakeEmulatorServer` 왕복을 확인한다(`engine/selftest.py`).
  - 과거의 개발용·배포용 런타임 불일치(OpenCV 4.14 대 5.0)를 막는 장치다.
- **`OrcusAPK.exe --boot-check`(패키징본) [P2부터]:** `emulator-check accel`을 돌리고, `host`·`host_hidden`으로 각각 짧게 콜드 부팅한다(discovery + `getStatus` 확인 후 정지, `engine/bootcheck.py`). 패키징본의 로더 위생(§5.4)을 실제로 검증한다.

### 12.6 수동 점검 (단계 승인마다)

- **초보 예제 6개:** 소리, 한국어 TTS, PaintPot 필기감, BallBounce 플링, MoleMash, TalkToMe 흔들기.
- **방향·스캐너:** 가로 고정 앱, BarcodeScanner(안내 문구가 정직한지).
- **화면 환경:** 배율 100/125/150%. 창을 다른 모니터(세로 모니터 포함)로 옮겼을 때 크기 재계산. 원격제어(Mirage)를 켠 상태. V3 실시간 감시를 켠 상태.
- **드롭:** Drive 한글 경로, 온라인 전용 파일, 탐색기, 바탕화면에서.
- **로그오프 빠른 정지:** 앱 실행 중 로그오프 → 다시 로그인했을 때 `D:\OrcusAPK\sdk` 프로세스가 없고 스냅샷이 저장되지 않았다.
- **자원:** 작업 관리자로 유휴 RAM·CPU를 확인한다(모든 Orcus·SDK 프로세스 합계).
- **흔적:** C:·Drive 폴더 비교(§11.5의 범위와 허용 목록).

## 13. 위험과 대응

| # | 위험 | 대응 | 확인 |
|---|---|---|---|
| 1 | **WHPX를 켤 수 없음**(관리자 권한, 학교 정책, 복원 프로그램) — 차단 요인 | 1일차 S0a. 상태는 Win32_OptionalFeature + RebootPending으로 판정한다(DLL로 판단하지 않음). ShellExecuteEx로 dism을 실행하고 실행 실패와 종료 코드를 구분한다. IT 요청문을 준다. 재부팅 뒤 되돌려졌는지 감지한다. TCG·AEHD 대체 없음 | S0a·S0b |
| 2 | google_apis 36에 **TTS·ko-KR 음성·음성 인식이 없음**, 또는 Google 계정 로그인이 필요함 | 이미지를 확정하기 전에 S1. 대안: playstore 36(root와 `-d` 다운그레이드를 잃고 Play Protect 프롬프트가 날 수 있음. debug 속성은 그대로 쓸 수 있음) 또는 TTS 사이드로드. 개인 계정은 골든에 넣지 않는다. 런타임 TTS 카드 | S1·S5 |
| 3 | 헤드리스 `-gpu host`가 소프트웨어로 떨어짐(Mirage, 원격 세션) | `-gpu host`를 명시하고 렌더러를 검증한다. 배너 + 수동 GPU 모드, COLD 모드 정의. [P4] 자동 사다리(재확인 부팅, 원격 세션 예외) | S2 |
| 4 | 골든 named 스냅샷 가정이 틀림: 로드 속도, 디스크 복원, no-save 상태의 저장, 강제 종료 뒤 무결성, 시계, 포트·프록시 변경, `-snapshot` 저장 의미, **강제 로드 실패 시 골든 삭제**, **gRPC 저장의 같은 이름 거부** | S4에서 항목별로 확인한다(실험은 임시 이름으로). 골든 로드가 실패하면 재시도 없이 재구축하고, 저장 전에는 Delete + List로 확인한다. 대비책(quickboot 모델)은 D6의 '깨끗한 휴대폰' 약속, 정지 시간, 초기화 비용까지 바꾸므로 사용자 결정으로 올린다 | S4 |
| 5 | 입력 고착(클래식 엔진은 접촉을 풀지 않음, keydown이 기본값) | 입력 헬퍼 하나로 통일, 모든 종료 경로와 epoch 변화에서 해제, keypress 강제, 기기 테스트 6 | S7 |
| 6 | private adb 문제: nodaemon 동작, 재등록, 클라이언트가 서버를 몰래 띄움, 스캔이 느림(약 86 s), 공유 adb 키, USB·외부 서버 간섭 | 재등록 메시지(`host:emulator:5641`)를 직접 보낸다. `-H 127.0.0.1` 호출, 재시작 한도, `ADB_USB=0`, `MAX_PORT=5553`, `ADB_VENDOR_KEYS` 공유 키(안 되면 계정이 바뀔 때마다 재구축), 양방향 격리 테스트 | S9 |
| 7 | Python 프레임 비용과 지연 | 창 크기 프레임, 최신 프레임만 유지, 최소화하면 정지, fps·CPU·**지연** 예산. 넘으면 RGB32 → 30 fps → MMAP | S2 |
| 8 | 헤드리스 qemu에서 소리가 안 남 | `host_hidden` → `streamAudio` + QtMultimedia(+40 MB) | S3 |
| 9 | 한글 붙여넣기가 일부 필드에서 실패, 클립보드 토스트 | S8에서 Paste 키와 Ctrl+V 중 고른다. 입력 바 유지. 게스트 IME는 나중에 | S8 |
| 10 | 경로·인코딩(한글 + Drive, 비 ASCII AVD 경로 멈춤, cp949, MAX_PATH) | ASCII 짧은 루트와 가드, 스테이징 복사, UTF-8, 캐시는 Drive 밖 | 유닛·수동 |
| 11 | 디스크(약 13–14 GiB, keep-state +2–4, 설치 중 +2.4 GB). BlueStacks 최소 사양보다 많이 씀 | 설치 24 GiB·실행 6 GiB 사전 확인, zip 삭제, C:에는 KB 파일만. 줄이는 방법(AOSP 이미지 −2.4 GiB, 희소 userdata)은 D0(d) | S4·A6 |
| 12 | **AhnLab V3가 EPP로 중앙 관리됨** → 격리·지연이 생겨도 사용자가 예외를 못 넣을 수 있음 | 압축 해제 후 파일 확인, 첫 discovery 60 s, 복사 가능한 IT 요청문, onedir, 오탐 신고. 예외 전후 부팅 시간 비교(D13) | S0b·S4·A8 |
| 13 | 에뮬레이터 부가 프로세스(netsimd, crashpad_handler)가 Job 밖에 남거나 오래 머묾 | 프로세스 점검(Job 목록 대 SDK 이미지), 정지 후 정리. 필요하면 netsim 기능을 끈다(골든을 만들기 전에) | S4·S6·S9 |
| 14 | 버전 변화(37.x 소스 미확인, 차세대 엔진 예고) | build·proto·스텁 고정, 자동 업데이트 없음, 스모크 관문, UNIMPLEMENTED면 그 기능만 끔 | 스모크 |
| 15 | 라이선스(§3.4 재배포 금지, ARM 번역은 개발·디버그용) | PC마다 사람이 동의한 뒤 다운로드, SDK를 묶지 않음, 공개 배포 전 법률 검토 | §14 |
| 16 | 학교 프록시 → 게스트의 Web·Map·CloudDB 실패 | 환경변수가 없으면 Windows 시스템 프록시를 `-http-proxy`로 넘긴다. PAC는 경고. gRPC 채널만 프록시를 끈다 | S4·S6 |
| 17 | 가로 고정 화면이 옆으로 보임(프레임 회전 = 기기 자세). 흔들기·기울기가 프레임을 뒤집음 | 실행 전 방향 맞추기, 불일치 배너 + [화면 방향 맞추기], 흔들기 부호 규칙, 기울기 20° 제한, Probe Screen2 | S7 |
| 18 | GUI와 CLI·테스트가 같은 VM을 서로 죽임 | 엔진 소유권 잠금(§1.3), `ENGINE_IN_USE` | 기기 테스트 12 |
| 19 | 모니터마다 크기·배율이 다름(이 PC는 3대, 주 모니터 1920×1080) | 창이 놓인 화면 기준으로 크기를 계산하고, 화면이 바뀌면 다시 계산 | 수동 |

## 14. 라이선스·배포 메모

- **Google SDK 구성요소**(에뮬레이터, platform-tools, 시스템 이미지)는 android-sdk-license §3.4에 따라 **재배포 금지**다. 사용자 PC에서 **사람이 직접 동의**를 기록한 뒤 다운로드한다. stable 채널만 쓰고, 패키지를 줄이지 않는다.
- **ARM 번역**(libndk_translation)은 개발·디버그용이다. 상용 호스팅 서비스에는 쓸 수 없다.
- **우리 배포물:**
  - Qt(LGPL): DLL을 교체할 수 있게 별도 파일로 둔다(onedir).
  - grpcio(Apache-2.0), protobuf(BSD-3), apkInspector(Apache-2.0).
  - 포함한 proto: Apache-2.0 헤더를 확인한 뒤 NOTICE에 적는다.
  - PyInstaller 부트로더 예외.
- **공개 배포 전**에 법률 검토와 코드 서명(OV 인증서, 오픈소스라면 SignPath)이 필요하다(사용자 결정 ★D3).

---

## 15. 설계 결정 기록

### 15.1 주요 결정과 기각한 대안

| 결정 | 기각 | 이유 |
|---|---|---|
| Google 에뮬레이터 37.2.12 + WHPX | QEMU + Android-x86/BlissOS, Genymotion, WSA, Waydroid, Cuttlefish, 차세대 Preview 37.1.6 | Hyper-V/VBS가 켜진 PC에서 쓸 수 있고 gRPC 제어면이 있는 유일한 유지보수 엔진이다. 나머지는 낡았거나(virgl 없음), Hyper-V와 충돌하거나, 종료되었거나, Linux 전용이거나, API 37 전용이다 |
| API 36 google_apis x86_64 | API 35, AOSP default, playstore, API 37, API 30 | API 36만 모든 AI2 APK를 설치할 수 있다(AndroidMinSdk 최대 36). AOSP에는 TTS가 없고, playstore는 user 빌드이며, API 37은 RAM 하한이 4 GB다 |
| emulator gRPC 인밴드 RGB888 | scrcpy 5.0.1, MMAP 공유 메모리 | 게스트 인코딩이 없고, 센서가 같은 채널에 있다. 인밴드는 MMAP과 같은 에뮬레이터 프레임버퍼를 다른 전송으로 받는 방식이다. MMAP은 S2 예산을 넘을 때만 쓰고, scrcpy는 실기기용(이후, 별도 승인)이다 |
| Python 3.14 + PySide6-Essentials | C#/.NET 10 + WPF, C++/Rust, Electron, Flutter | 컴파일러나 큰 SDK가 C:에 오지 않고, 최선의 AXML 파서가 있으며, 한 언어로 끝난다. 무거운 일은 에뮬레이터가 하므로 네이티브 언어의 이득이 작다 |
| 스레드 + 큐, core는 `subprocess` 기반 자체 실행기 | asyncio/qasync, QtAsyncio, core에서 QProcess | 블로킹 gRPC 스트림과 잘 맞고, 같은 코드가 CLI·테스트·GUI에서 돈다. QtAsyncio는 subprocess·socket을 지원하지 않는다(P) |
| apkInspector | androguard(개발용 검증 기준으로만), aapt2(build-tools 추가 다운로드) | 순수 Python 30 KB, 의존성 없음 |
| adb.exe 직접 + private 서버 | adbutils | requests·Pillow에 자체 adb까지 묶어 와 버전 충돌을 부른다 |
| 골든 named 스냅샷 + `-force-snapshot-load` + `-no-snapshot-save` | 표준 quickboot 지속 모델 | 결정적이고, 크래시에 안전하며, 종료가 빠르다. 실패하면 조용히 콜드 부팅하지 않고 바로 드러난다 |
| 상태 12개 + 단순 복구(자동 재시작 1회, 실패 재구축 1회) | 다단 복구 사다리, DEGRADED 상태, 4중 감시, AVD 복제 | 혼자 유지보수하기에 맞는 크기다. 경고는 배너로 충분하다 |
| Phase 2 = 골격·설치·버리는 스파이크 하네스 | Phase 2에서 엔진 본체까지 | 사용자가 정한 단계 정의를 지키고, 검증 전에 만든 코드를 다시 짜는 일을 피한다 |

### 15.2 설계 패널에서 가져온 것

Phase 1에서 설계안 3개를 따로 만들었다: A 경량, B 견고, C 학습자 UX. 심사위원 3명(시스템, 제품, 기술 리드)이 채점했고, 결과는 C > A > B 두 표, A > C > B 한 표였다. 이 문서는 A를 뼈대로 다음을 접목했다.

- **A:** 의존성 4개, 표준 라이브러리 설치, Python 전용 도구, 루트 가드, `PYTHONPYCACHEPREFIX`, adb 하이재킹 변수 제거, platform-tools 재배포 금지 정정, 스파이크 표, 예열, 심각도별 피드백 채널.
- **B:**
  - `contracts` 계층과 `ApkService.answer`
  - Job 규율(TerminateJobObject 금지, PID 강제 종료)
  - IPv4·IPv6 포트 검사, 실행 정책 3종
  - FakeEmulatorServer, 패키징본 `--selftest`
- **C:**
  - 골든 수명주기(`-force-snapshot-load`, `-wipe-data` 재구축, 검증 부팅, `session_init`)
  - 단일 인스턴스, `localhost` + 프록시 끄기, V3 격리 확인, zip-slip, RunOnce
  - 학생용 문구, AI 오류 카드, 보고서 복사, 실제 권한 대화상자, 감도별 단일 펄스 흔들기, 위치 켜기

### 15.3 검토에서 새로 정한 것

**v2 (적대적 검토 66건)**
- WHPX 상태는 Win32_OptionalFeature로 판정한다. dism은 `ShellExecuteExW`로 실행하고 결과를 확인한다.
- 엔진 소유권 잠금을 넣고, 고아의 정의를 다시 세웠다.
- DeviceSession은 주입된 팩토리로 만들고, gRPC 채널을 2개로 나눴다.
- epoch를 도입하고 같은 파일 빠른 경로의 규칙을 고쳤다.
- COLD 모드를 정식으로 정의했다. GPU 자동 사다리는 [P4]로 옮기고 `-gpu auto` 값은 금지했다.
- keep-state는 [P4]로 옮기고 A/B 슬롯으로 바꿨다. 빠른 정지에서는 저장하지 않는다.
- `-snapshot`이 있으면 `-no-snapshot-save`도 반드시 있어야 한다는 불변식을 넣었다. 초기화는 nonce로 검증하고, SnapshotService 응답의 `success`를 확인한다.
- 자식 프로세스의 로더 위생을 넣었다. 시스템 프록시는 `-http-proxy`로 넘기고, adb는 USB를 끈다.
- App Inventor 동작 반영: LocationSensor 주기, 두 오류 경로, 908, TTS 런타임 감지, 화면 방향, 정직한 스캐너 안내.
- 화면 9:16, 지연 예산, 정규 좌표를 넣었다. 드래그 중에는 파일 I/O를 하지 않는다.
- contracts를 Phase 2에서 모두 정의한다. 레이어 규칙은 허용 목록으로 하고 pyright를 쓴다.
- Phase 2 범위를 사용자 정의로 되돌리고, Phase 3을 3a와 3b로 나눴다.

**v3 (수정 점검 52건)**
- **상태 기계:** epoch는 SESSION_INIT에서만 올리고 `epoch_cause`를 붙였다(재설치와 크래시 안내는 CRASH_RESTART에서만). STOPPING은 의도별로 나간다(stop/rebuild/restart). FAILED에 들어가면 VM을 정리하고 잠금을 푼다. 활성 상태에서 생긴 예기치 않은 종료와 SESSION_INIT의 모든 검사에 결과를 정했다. `wait_ready`의 STOPPED 규칙을 고쳤다.
- **골든:** 강제 로드에 실패하면 에뮬레이터가 스냅샷을 지우므로, 재시도 없이 재구축한다. gRPC 저장은 같은 이름을 거부하므로 저장 전에 Delete + List로 확인한다. KEEP_STATE는 강제 로드 없이 부팅하고 RAM 표식으로 감지한다.
- **nonce:** 준비 8단계에서 nonce를 비운다. 검증은 "이번 세션 값과 다름"으로 한다.
- **adb:** 재등록 메시지 `host:emulator:5641`을 직접 보낸다. `MAX_PORT=5553`으로 스캔을 끈다(이 PC에서 스캔은 약 86 s 걸린다). `ADB_EMU=0`은 쓰지 않는다. 계정 간 재구축을 막으려고 공유 adb 키(`ADB_VENDOR_KEYS`)를 쓴다.
- **설치:**
  - UAC 거절은 실행 실패(1223)로 처리한다. dism 종료 코드 3017을 처리한다. `lpFile`은 `GetSystemDirectoryW`로 만든다.
  - InstallState 4를 처리하고, 값이 1이어도 RebootPending을 확인한다.
  - 설치 전 여유 공간은 24 GiB, 실행 전은 6 GiB로 했다.
  - dism이 Job에 들어가는지는 U이며 S0a에서 확인한다.
- **화면:** 이 PC는 모니터가 3대이고 주 모니터는 1920×1080이다. 크기는 창이 놓인 화면을 기준으로 계산한다(주 모니터에서 약 432×768).
- **센서:** 흔들기는 중력 방향으로 펄스를 준다(역방향 자세에서 화면이 뒤집히지 않게). 기울기는 20°로 제한한다.
- **계약:**
  - `Settings` 타입, `ReadOnlyEngine` 읽기 전용 접속, `screenshot_png`를 추가하고 SetupApi를 비차단으로 바꿨다.
  - 문자열은 `common/messages_ko.py`로, WHPX 확인은 `common/accel.py`로 옮겼다.
  - Phase 2 최소 조립은 `app/compose.py`와 `engine/bootcheck.py`·`selftest.py`다.
- **모드별 문구:** COLD와 KEEP_STATE에서는 '데이터가 지워진다'는 안내를 하지 않는다.
- **단계:** [앱]·[로그] 패널은 3a다. 3a 스모크에서 흔들기를 뺐다. 단계 표기의 기본 규칙을 고쳤다. S0을 S0a(다운로드 전)와 S0b(다운로드 후)로 나눴다.
- **픽스처:** Probe는 S3·S5 전에 만든다. Probe에 WebViewer, OrientationSensor, BarcodeScanner, AvailableLanguages, UseLegacy 두 버튼, SoundRecorder를 넣었다. 크래시는 `am crash`로 일으킨다.
- **개발 캐시:** pip, pyright, npm 캐시를 모두 D:로 옮겼다. A6 비교 범위를 명시했다.

**v3 최종 점검(29건)**
- **FAILED:** 잠금 없이 들어오면(`ENGINE_IN_USE`, `PRE_NOT_INSTALLED`) 아무것도 정리하지 않는다. 잠금을 쥔 FAILED는 DeviceSession도 닫는다.
- **조기 종료와 전원 끄기:**
  - 조기 종료는 먼저 분류 표를 따른다.
  - 골든 로드 실패로 보는 것은 스냅샷 행이거나 골든 폴더가 사라진 경우뿐이다.
  - 게스트 전원 끄기는 STOPPING(stop)으로 간다.
- **adb:** PROVISIONING도 LAUNCHING과 같은 adb 준비와 소유자 기록을 한다. private adb 서버는 앱 수명 동안 유지한다.
- **초기화와 검증 부팅:** 초기화 뒤에는 adb `device`를 기다린 다음 검증한다. 검증 부팅에서 `provision.json`을 쓰고 원인(BOOT/REBUILD)을 정한다.
- **디스크:**
  - 준비·재구축 시작에는 10 GiB가 필요하다.
  - 디스크 경고는 `ENGINE_DISK_LOW`다. 1 GiB 미만이면 빠른 정지 뒤 FAILED(`PRE_DISK`).
- **WHPX 확인:** RebootPending은 원인 설명에만 쓴다(Windows 업데이트일 수 있다). InstallState가 1이면 바로 가속 확인으로 간다.
- **설정:**
  - `preview_settings`로 재구축이 필요한지 먼저 묻는다.
  - 3a의 툴바 [설정]은 GPU 모드 대화상자를 연다.
  - `sensors.shake_default` 키를 추가했다.
- **nonce:** 비울 때 argv에 따옴표 처리를 해서 빈 값을 전달하고, 종료 코드를 확인한다.
- **음성·오류 시험:**
  - SpeechRecognizer의 UseLegacy 끔 경로(3802)를 `APP_SPEECH_MISSING`으로 분류한다.
  - 2701과 음성 인식 없음은 해당 패키지를 끈 상태에서 시험한다.
  - Probe에 ANR 버튼을, 픽스처에 가로 고정 앱을 추가했다.
- **화면:** 크기는 높이 기준과 폭 기준 중 작은 쪽으로 정한다. 폰이 안 들어가면 오른쪽 패널을 접는다. 프레임 재연결은 READY일 때만 한다.
- **기타:**
  - A6는 OS 기록을 빼고 우리 프로세스가 만든 변화만 본다.
  - 공유 adb 키가 안 되면 계정이 바뀔 때마다 재구축한다.

### 15.4 미룬 항목과 다시 검토할 조건

| 항목 | 다시 검토할 조건 |
|---|---|
| scrcpy·PyAV | 실기기 지원을 요청할 때(별도 승인) |
| MMAP 프레임 전송 | S2, 또는 수용 기준 B2(지연)·B9(fps·CPU)에서 예산을 넘을 때 |
| QtMultimedia(`streamAudio`) | S3에서 헤드리스와 `host_hidden` 모두 소리가 나지 않을 때 |
| numpy | 프레임 변환·센서 계산이 S2 예산을 넘고, numpy가 그 문제를 해결할 때 |
| adbutils | 쓰지 않는다(자체 adb를 함께 묶어 옴). 다시 보지 않는다 |
| qasync / QtAsyncio | asyncio 기반 라이브러리가 꼭 필요해질 때 |
| playstore 36 이미지 | S1에서 TTS·음성 인식이 playstore에만 있을 때(D8) |
| API 30 이미지 | 32비트 ARM 전용 확장 기능을 꼭 돌려야 할 때 |
| 다중 AVD | 동시에 여러 휴대폰이 필요하다는 요구가 있을 때 |
| Companion 실시간 테스트 | 사용자가 요청할 때(Phase 4 이후 별도 승인) |
| 차세대 엔진 어댑터 | 차세대 엔진이 API 36 대상과 proto를 제공할 때 |
| GPU 자동 사다리, keep-state, GUI 마법사, detached 모드 | Phase 4 |

### 15.5 조사 노트 정정 (research brief 대비, brief 머리의 정정 목록과 같다)

1. §7 "Ship pinned platform-tools"는 틀렸다. 라이선스 §3.4에 따라 동의한 뒤 다운로드한다.
2. §2 `emulator-check.exe accel hyper-v`의 종료 코드에 기대지 않는다. `accel`을 단독으로 실행한다.
3. §2 `-port N`만 줄 때 gRPC가 꺼지는지는 해석이 갈린다. 우리는 항상 `-grpc`와 토큰을 함께 주므로 영향이 없다(S6에서 확인).
4. §4 흔들기는 클릭당 펄스 1회이고, 펄스는 중력 방향으로 준다.
5. §7 QProcess 대신 core에서는 `subprocess` 기반 실행기를 쓴다.
6. §7 adb는 `ADB_LOCAL_TRANSPORT_MAX_PORT=5553`으로 시작 스캔을 끄고, 서버를 띄운 뒤 `host:emulator:5641`을 직접 보내 재등록한다. `ADB_EMU=0`은 쓰지 않는다. `ADB_USB=0`으로 USB를 끈다.
7. gRPC 대상은 `localhost`다. 에뮬레이터 프로세스의 프록시 변수는 그대로 둔다.
8. adb 서버는 nodaemon을 먼저 시도한다. start-server를 PIPE로 실행하면 멈춘다는 전제는 낡았을 수 있다(I).
9. §2 "`-snapshot <name>`은 덮어쓰지 않는다"는 공식 문서와 어긋난다. 항상 `-no-snapshot-save`를 함께 준다. 같은 이름으로 다시 저장할 때의 동작은 경로마다 다르다(gRPC는 거부, 콘솔은 지우고 씀, Delete 응답은 늘 성공).
10. WHPX 상태는 DLL이 있는지로 판단하지 않는다. UAC 거절(1223)은 dism 종료 코드가 아니라 실행 실패로 나타난다.
11. §4 "GPS를 약 1 Hz로 읽는다"는 HAL 기준이다. AI2 LocationSensor는 기본으로 60 s/5 m 간격이다.
12. §2 에뮬레이터는 Windows 시스템 프록시를 읽지 않는다. `-http-proxy`로 넘긴다.
13. §1 AhnLab V3는 EPP로 중앙 관리된다.
14. §2 에뮬레이터 패키지에는 `netsimd.exe`와 `crashpad_handler.exe`가 들어 있다(프로세스가 더 늘 수 있음).
15. §6 "setprop … (userdebug images)": shell은 user 빌드에서도 `debug.*`를 설정할 수 있다. 그래서 playstore 이미지에서도 대화상자 억제가 된다.
16. §1 "1920x1200 display": 이 PC는 모니터 3대다. 주 모니터는 1920×1080, 둘째도 1920×1080, 1920×1200 모니터는 세로로 돌려 1200×1920으로 쓴다.
17. §2 `-force-snapshot-load`는 로드 검증에 실패하면 스냅샷을 **지운 뒤** 종료한다(36.1·master 소스).

---

## 16. 검토 반영 추적

검토 ID 접두어: WE = Windows·에뮬레이터, AU = App Inventor UX, MS = 유지보수·범위, CC = 일관성·완전성. 원문은 Phase 1 세션 기록에 있다.

| 주제 | 반영 위치 | 검토 ID |
|---|---|---|
| WHPX 상태 판정·dism 결과 처리·재부팅 뒤 되돌림 | §11.6 1·3·7단계, §12.1 accel·setup | WE-1(차단), WE-5, AU-8 |
| keep-state A/B 슬롯·빠른 정지·크래시 재시작 | §6.3, §5.1 STOPPING, §1.3 | WE-2, MS-4, CC-2 |
| COLD(L3) 모드·재구축 한도·사다리 | §5.1, §5.6, §6.1, §6.5 | WE-3, MS-5, CC-1, CC-14 |
| 자식 환경 로더 위생 | §5.4, §12.5 `--boot-check` | WE-4 |
| SnapshotService 응답 검사·초기화 nonce | §6.5 8·9단계, §6.7 | WE-6 |
| 엔진 소유권 잠금 | §1.3, §5.7, §12.3-12 | WE-7, MS-6, CC-10 |
| 시스템 프록시 | §5.4 | WE-8 |
| 마우스 이동 밀도 해결책 | §9.3 | WE-9 |
| 단일 인스턴스·로그오프 처리 위치 | §1.3, §2 | WE-10, MS-10, CC-11 |
| 보조 프로그램 실행·BREAKAWAY | §1.2 | WE-11 |
| adb 재시작·서버 몰래 띄우기 방지·실행기 | §5.5 | WE-12 |
| `-snapshot` 저장 의미 | §5.2 불변식, §6.1 | WE-13 |
| 준비 대기 폴링·관찰값 검증 | §6.5 2·7단계 | WE-14 |
| adb 격리(USB, 스캔) | §5.4, §5.5, §12.3-11 | WE-15, CC-12 |
| LocationSensor 주기·대략적 위치·기본 위치 | §6.6, §7.7, §9.7, §12.3-5 | AU-1 |
| App Inventor 오류 두 경로·908·음성 인식 | §7.8 | AU-2, AU-11 |
| 방향(기기 자세 대 표시 회전) | §7.6, §9.7 | AU-3 |
| 스캐너 문구·카메라 imagefile | §7.4, §9.8 | AU-4 |
| 런타임 TTS 감지 | §7.8 | AU-5 |
| 닫을 때 데이터 안내·크래시 메시지 | §6.1, §8 | AU-6 |
| 설치 순서 통일 | §10.5, §11.6 | AU-7, CC-9 |
| Play Protect·계정 로그인·진동 칩 | §6.5 4·5단계, §7.4 | AU-9, AU-10, AU-12 |
| DeviceSession 팩토리·채널 | §1.1, §3, §4 | MS-1, CC-11 |
| epoch·빠른 경로 | §6.2, §8 | MS-2, CC-3 |
| NOT_INSTALLED·setup→engine·pins | §2, §5.1, §11.6 9단계 | MS-3, CC-8 |
| 비차단 API·device-controls 스레드·종료 순서 | §3, §4, §9.7 | MS-7 |
| contracts 정의 범위 | §3 | MS-8 |
| Phase 2 범위·스파이크 순서 | plan.md §3 | MS-9, CC-4 |
| 허용 목록 레이어·pyright·HeadlessSession | §2, §3 | MS-11 |
| 오류 코드 체계 | §3 errors, §10.2 | MS-12 |
| 픽스처·사용자 작업·A6 범위 | §11.5, §12.4, plan.md | MS-13, CC-7 |
| Phase 3 분할·수용 기준 | plan.md §3 | MS-14 |
| 정규 좌표·기능 플래그 | §3, §9.2 | MS-15 |
| 드래그 중 I/O 금지 | §7.1, §8 | MS-16 |
| engine seam·가짜 객체 위치·selftest | §2, §12.2, §12.5 | MS-17 |
| 버전 관리 권장 | plan.md ★D4 | MS-18 |
| 아키텍처 승인 항목(★D0) | plan.md §2 | CC-4 |
| BlueStacks 대비 풋프린트 | plan.md §1.7 | CC-5 |
| 라이선스는 사람이 직접 동의 | §10.5, §11.6 2단계 | CC-6 |
| netsimd·crashpad·프로세스 점검 | §1.2, §5.7, §11.6 5단계 | CC-13 |
| 9:16·크기 | §9.1, §10.1 | CC-15 |
| 지연 예산 | §9.1, plan.md S2·B2 | CC-16 |
| D3 참조 | §14 | CC-17, WE-16 |
| 미룬 항목의 재검토 조건 | §15.4 | CC-18 |
| 준비 명령을 확정할 스파이크·WHPX 캐시 키 | §6.5 4단계, §11.6 7단계 | CC-19 |
| 앱 카드 1 s | §7.2, §8 | CC-20 |

**v3 수정 점검(52건) 반영 위치**

| 주제 | 반영 위치 |
|---|---|
| epoch 한 번만 증가·원인 표시·이전 DeviceSession 닫기 | §3 EngineStatus, §5.1, §6.2 |
| nonce가 골든에 저장되는 문제 | §6.5 8단계, §6.7, §12.2 |
| STOPPING 의도별 출구, FAILED 정리, 활성 상태의 예기치 않은 종료, SESSION_INIT 검증 결과 | §5.1 |
| 강제 로드 실패 시 골든 삭제, gRPC 저장의 같은 이름 거부 | §5.1, §6.3, §6.4, §6.5 9단계, §13-4 |
| COLD·KEEP_STATE 안내 문구 | §6.1 |
| adb 재등록 메시지, 스캔 끄기, 공유 adb 키 | §5.4, §5.5, §6.4, §11.6 8단계 |
| UAC 1223·3017·lpFile·InstallState 4·RebootPending·dism과 Job | §1.2, §11.6, §12.1 |
| 디스크 기준 24/6 GiB | §6.8, §11.6 |
| 이 PC 모니터 구성과 크기 계산 | §9.1, §10.1, §13-19, plan.md D11·S2 |
| 흔들기 부호·기울기 제한 | §9.7, §12.1 |
| user 빌드의 debug 속성 | §6.6, §13-2 |
| Settings·ReadOnlyEngine·screenshot·SetupApi 비차단 | §3 |
| 문자열·WHPX 확인을 common으로, Phase 2 최소 조립 | §2, §10.6 |
| 단계 표기 규칙·3a/3b 패널·스모크 | 머리말, §5, §10.1, §12.5, plan.md |
| Probe 사양·마감, `am crash` | §12.3, §12.4, plan.md |
| 개발 캐시·A6 범위 | §11.5, §11.7, plan.md A6 |
