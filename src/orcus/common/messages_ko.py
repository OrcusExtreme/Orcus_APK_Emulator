"""Every user-visible string, plus the ErrorCode -> message / remedy / feedback channel table.

No Qt here: the CLI and the GUI share this file (ARCHITECTURE.md §10.2, §10.6). Sentences are
written for students: plain words, and each one says what to do next.
"""

import getpass
import os
from dataclasses import dataclass
from enum import StrEnum, auto

from orcus.contracts import ErrorCode, OrcusError, Remedy, RemedyAction
from orcus.contracts import RemedyAction as R


class Channel(StrEnum):
    SETUP_LINE = auto()     # setup progress screen (CLI / [P4] wizard): failure line + IT request
    ENGINE_PANEL = auto()   # full panel over the phone area
    BANNER = auto()         # persistent warning banner
    MODAL = auto()          # decision dialog
    RED_BANNER = auto()     # red banner on the phone + problem card
    PROBLEM_CARD = auto()   # [Log] tab problem card


@dataclass(frozen=True)
class MessageSpec:
    text: str
    channel: Channel
    remedy: RemedyAction | None


E = ErrorCode
C = Channel

ERRORS: dict[ErrorCode, MessageSpec] = {
    # 설치
    E.SETUP_PERMISSIONS: MessageSpec("설치 폴더를 이 계정 전용으로 잠그지 못했습니다. 다른 계정이 만든 폴더라면 다른 폴더(예: D:\\OrcusAPK2)를 쓰세요.", C.SETUP_LINE, R.RETRY),
    E.SETUP_PATH: MessageSpec("이 폴더에는 설치할 수 없습니다. 영문 짧은 경로(예: D:\\OrcusAPK)를 쓰세요.", C.SETUP_LINE, None),
    E.SETUP_DISK: MessageSpec("D: 드라이브 공간이 부족합니다. 24 GB 이상 비운 뒤 다시 실행하세요.", C.SETUP_LINE, R.FREE_DISK),
    E.SETUP_NETWORK: MessageSpec("내려받기가 끊겼습니다. 인터넷 연결을 확인하고 다시 실행하면 이어서 받습니다.", C.SETUP_LINE, R.RETRY),
    E.SETUP_CHECKSUM: MessageSpec("내려받은 파일이 손상되었습니다. 다시 실행하면 처음부터 다시 받습니다.", C.SETUP_LINE, R.RETRY),
    E.SETUP_LICENSE_CHANGED: MessageSpec("Google SDK 라이선스 내용이 바뀌었습니다. 새 내용을 읽고 다시 동의해 주세요.", C.SETUP_LINE, None),
    E.SETUP_LICENSE_NOT_STABLE: MessageSpec("필요한 구성요소가 정식(stable) 라이선스가 아닙니다. 개발자에게 알려 주세요.", C.SETUP_LINE, None),
    E.SETUP_UAC_DENIED: MessageSpec("권한 요청(UAC)이 거절되어 Windows 하이퍼바이저 플랫폼을 켜지 못했습니다. 다시 실행해 [예]를 누르거나, 관리자에게 IT 요청문을 보내세요.", C.SETUP_LINE, R.COPY_IT_REQUEST),
    E.SETUP_DISM_FAILED: MessageSpec("Windows 하이퍼바이저 플랫폼을 켜지 못했습니다. 학교 정책일 수 있으니 IT 요청문을 관리자에게 보내세요.", C.SETUP_LINE, R.COPY_IT_REQUEST),
    E.SETUP_REBOOT_PENDING: MessageSpec("Windows를 다시 시작해야 합니다. 다시 시작하면 설치가 이어집니다.", C.SETUP_LINE, None),
    E.SETUP_WHPX_REVERTED: MessageSpec("재부팅 뒤에도 Windows 하이퍼바이저 플랫폼이 꺼져 있습니다. PC 복원 프로그램이나 정책이 되돌렸을 수 있으니 IT 요청문을 보내세요.", C.SETUP_LINE, R.COPY_IT_REQUEST),
    E.SETUP_QUARANTINE: MessageSpec("압축을 푼 파일 일부가 사라졌습니다. 백신(V3)이 격리했을 수 있으니 IT 요청문으로 예외를 요청하세요.", C.SETUP_LINE, R.COPY_IT_REQUEST),
    E.SETUP_ACCEL_FAILED: MessageSpec("가상화 가속(WHPX)을 사용할 수 없습니다. 로그를 확인하세요.", C.SETUP_LINE, R.OPEN_LOG),
    # 실행 전 점검
    E.PRE_NOT_INSTALLED: MessageSpec("설치가 필요합니다. 터미널에서 orcus-cli setup을 실행하세요.", C.ENGINE_PANEL, R.REPAIR_SETUP),
    E.PRE_WHPX_OFF: MessageSpec("Windows 하이퍼바이저 플랫폼이 꺼져 있어 휴대폰을 켤 수 없습니다.", C.ENGINE_PANEL, R.ENABLE_WHPX),
    E.PRE_SDK_MISSING: MessageSpec("휴대폰 실행에 필요한 파일이 없습니다. 설치를 복구하세요.", C.ENGINE_PANEL, R.REPAIR_SETUP),
    E.PRE_DISK: MessageSpec("D: 드라이브 공간이 부족해 휴대폰을 켤 수 없습니다. 공간을 비운 뒤 다시 시도하세요.", C.ENGINE_PANEL, R.FREE_DISK),
    E.PRE_PATH: MessageSpec("설치 폴더 경로에 문제가 있습니다. 설치를 복구하세요.", C.ENGINE_PANEL, R.REPAIR_SETUP),
    E.PRE_PORTS: MessageSpec("필요한 통신 포트를 다른 프로그램이 쓰고 있습니다. 잠시 뒤 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.PRE_NO_DISPLAY: MessageSpec("화면 장치가 보이지 않아(원격 데스크톱 연결이 끊긴 경우 등) 휴대폰을 준비하지 않았습니다. 다시 연결한 뒤 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    # 엔진
    E.ENGINE_IN_USE: MessageSpec("다른 Orcus가 휴대폰을 사용 중입니다. 그 창을 닫은 뒤 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_EXIT: MessageSpec("휴대폰이 예기치 않게 꺼졌습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_AVD_LOCKED: MessageSpec("다른 프로그램이 가상 휴대폰을 쓰고 있습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_CONNECT_TIMEOUT: MessageSpec("휴대폰에 연결하지 못했습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_BOOT_TIMEOUT: MessageSpec("휴대폰이 제시간에 켜지지 않았습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_GOLDEN_LOAD_FAILED: MessageSpec("저장해 둔 휴대폰 상태를 불러오지 못했습니다. 다시 시도하면 휴대폰을 새로 준비합니다(약 5분).", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_RESET_FAILED: MessageSpec("휴대폰 초기화가 확인되지 않아 휴대폰을 다시 켰습니다.", C.BANNER, None),
    E.ENGINE_PROVISION_FAILED: MessageSpec("휴대폰을 처음 준비하다 멈췄습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_GPU_SOFTWARE: MessageSpec("느린 그래픽 모드로 실행 중입니다. 화면이 느릴 수 있습니다.", C.BANNER, R.CHANGE_GPU_MODE),
    E.ENGINE_ADB_LOST: MessageSpec("휴대폰과의 연결이 잠시 끊겼습니다. 다시 연결하는 중입니다.", C.BANNER, None),
    E.ENGINE_CRASHED: MessageSpec("휴대폰이 반복해서 멈췄습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_UNAVAILABLE: MessageSpec("휴대폰 엔진을 사용할 수 없습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_NO_FRAMES: MessageSpec("휴대폰 화면을 받지 못했습니다. 다시 시도하세요.", C.ENGINE_PANEL, R.RETRY),
    E.ENGINE_DISK_LOW: MessageSpec("D: 드라이브 공간이 3 GB보다 적습니다. 공간을 비워 주세요.", C.BANNER, R.FREE_DISK),
    E.ENGINE_NO_DISPLAY: MessageSpec("화면 장치가 보이지 않을 때 켜져서(원격 데스크톱 연결이 끊긴 경우 등) 이번만 느린 시작으로 켰습니다. 휴대폰 초기화를 하면 저장된 깨끗한 휴대폰으로 돌아옵니다.", C.BANNER, None),
    # 드롭한 파일
    E.STAGE_UNEXPECTED: MessageSpec("앱을 준비하다 예기치 않은 문제가 생겼습니다. 다시 넣어 보고, 계속되면 [로그 보기]로 로그를 확인하세요.", C.MODAL, None),
    E.FILE_NOT_ANDROID: MessageSpec("Android 앱(APK) 파일이 아닙니다. App Inventor에서 [빌드] → [Android 앱 (.apk)]로 받은 파일을 넣으세요.", C.MODAL, None),
    E.FILE_IS_AAB: MessageSpec("AAB 파일은 바로 실행할 수 없습니다. App Inventor에서 APK로 다시 빌드하세요.", C.MODAL, None),
    E.FILE_IS_AIA: MessageSpec("AIA는 App Inventor 프로젝트 파일입니다. App Inventor에서 APK로 빌드한 뒤 넣으세요.", C.MODAL, None),
    E.FILE_IS_AIX: MessageSpec("AIX는 확장 기능 파일입니다. App Inventor 프로젝트에 넣고 APK로 빌드하세요.", C.MODAL, None),
    E.STAGE_DRIVE_TIMEOUT: MessageSpec("Google Drive에서 파일을 가져오지 못했습니다. 파일을 PC에 내려받은 뒤 다시 넣으세요.", C.MODAL, R.RETRY),
    E.STAGE_COPY_FAILED: MessageSpec("파일을 복사하지 못했습니다. 다시 시도하세요.", C.MODAL, R.RETRY),
    E.APK_PARSE: MessageSpec("APK 파일을 읽을 수 없습니다. 파일이 손상되었을 수 있으니 다시 빌드하세요.", C.MODAL, None),
    # 설치(앱)
    E.INSTALL_OLDER_SDK: MessageSpec("이 앱은 이 가상 휴대폰보다 새 Android가 필요합니다.", C.MODAL, None),
    E.INSTALL_NO_ABI: MessageSpec("이 앱은 이 가상 휴대폰에서 돌지 않는 부품(ARM 전용)을 포함합니다.", C.MODAL, None),
    E.INSTALL_INCOMPATIBLE: MessageSpec("다른 App Inventor 계정·키로 빌드된 같은 앱이 있습니다. 바꿀까요? 저장된 데이터가 지워집니다.", C.MODAL, R.CONTINUE_ANYWAY),
    E.INSTALL_STORAGE: MessageSpec("가상 휴대폰 저장 공간이 부족합니다. 휴대폰을 초기화하세요.", C.MODAL, R.RESET_PHONE),
    E.INSTALL_INVALID: MessageSpec("APK가 올바르지 않아 설치할 수 없습니다. 다시 빌드하세요.", C.MODAL, None),
    E.INSTALL_OTHER: MessageSpec("앱을 설치하지 못했습니다. 로그를 확인하세요.", C.MODAL, R.OPEN_LOG),
    # 실행 중
    E.APP_LAUNCH_NOT_FOUND: MessageSpec("앱의 시작 화면을 찾지 못했습니다.", C.PROBLEM_CARD, None),
    E.APP_CRASH: MessageSpec("앱이 멈췄습니다(크래시). 블록을 확인한 뒤 다시 실행하세요.", C.RED_BANNER, R.RUN_AGAIN),
    E.APP_ANR: MessageSpec("앱이 응답하지 않습니다. 오래 도는 반복 블록이 있는지 확인하세요.", C.PROBLEM_CARD, R.RUN_AGAIN),
    E.APP_DIED: MessageSpec("앱이 종료되었습니다.", C.PROBLEM_CARD, R.RUN_AGAIN),
    E.APP_AI_COMPONENT_ERROR: MessageSpec("App Inventor 컴포넌트 오류가 났습니다. 오류 번호와 설명을 확인하세요.", C.PROBLEM_CARD, R.OPEN_LOG),
    E.APP_AI_RUNTIME_ERROR: MessageSpec("블록을 실행하다 오류가 났습니다. 오류가 난 블록을 확인하세요.", C.PROBLEM_CARD, R.OPEN_LOG),
    E.APP_TTS_MISSING: MessageSpec("이 가상 휴대폰에는 말하기(TTS) 기능이 없습니다.", C.PROBLEM_CARD, None),
    E.APP_SPEECH_MISSING: MessageSpec("이 가상 휴대폰에는 음성 인식 기능이 없습니다.", C.PROBLEM_CARD, None),
    E.APP_ORIENTATION_MISMATCH: MessageSpec("앱 화면이 휴대폰과 다른 방향으로 그려져 돌아가 보입니다(가로 고정 화면 등). [화면 방향 맞추기]를 누르면 휴대폰을 앱 방향으로 돌립니다.", C.BANNER, R.MATCH_ORIENTATION),
}

REMEDY_LABELS: dict[RemedyAction, str] = {
    R.RETRY: "다시 시도",
    R.ENABLE_WHPX: "Windows 하이퍼바이저 플랫폼 켜기",
    R.COPY_IT_REQUEST: "IT 요청문 복사",
    R.REPAIR_SETUP: "설치 복구",
    R.OPEN_LOG: "로그 보기",
    R.RESET_PHONE: "휴대폰 초기화",
    R.RUN_AGAIN: "다시 실행",
    R.GRANT_ALL: "이 앱 권한 모두 허용",
    R.CHANGE_GPU_MODE: "GPU 모드 바꾸기",
    R.FREE_DISK: "공간 확보 방법",
    R.CONTINUE_ANYWAY: "그래도 계속",
    R.MATCH_ORIENTATION: "화면 방향 맞추기",
}

TEXT: dict[str, str] = {
    # 경로·인터프리터 가드 (paths.home_problems / interpreter_problems)
    "path.not_absolute": "전체 경로(예: D:\\OrcusAPK)가 아닙니다.",
    "path.non_ascii": "경로에 한글 같은 비영문 문자가 있습니다.",
    "path.too_long": "경로가 40자보다 깁니다.",
    "path.no_drive": "드라이브를 찾을 수 없습니다.",
    "path.not_ntfs": "NTFS 드라이브가 아닙니다.",
    "path.drive_virtual": "Google Drive 가상 드라이브입니다.",
    "path.drive_sync": "Google Drive가 동기화하는 폴더 안입니다.",
    "path.store_sandbox": "Microsoft Store 앱 폴더 안입니다.",
    "python.store": "Microsoft Store판 Python은 쓸 수 없습니다(python.org 설치 관리자의 3.14 필요).",
    "python.version": "Python 3.14가 필요합니다.",
    "proxy.pac_unsupported": "자동 프록시 설정(PAC)은 지원하지 않습니다. 앱의 인터넷 연결이 안 될 수 있습니다.",
    # 설치 단계 이름
    "setup.step.check": "PC 점검",
    "setup.step.license": "라이선스 동의",
    "setup.step.whpx": "Windows 하이퍼바이저 플랫폼 켜기",
    "setup.step.download": "내려받기",
    "setup.step.extract": "압축 풀기",
    "setup.step.reboot": "재부팅 확인",
    "setup.step.accel": "가상화 가속 확인",
    "setup.step.avd": "가상 휴대폰 만들기",
    "setup.step.provision": "휴대폰 처음 준비",
    "setup.step.done": "완료",
    # 설치 안내
    "setup.title": "Orcus APK Emulator 설치",
    "setup.check.admin_limited": "UAC 창이 뜨면 [예]를 누르면 됩니다.",
    "setup.check.admin_default": "이 계정은 관리자가 아닙니다. UAC 창에 관리자 계정 암호가 필요합니다. 없으면 IT 요청문을 관리자에게 보내세요.",
    "setup.check.v3_epp": "AhnLab V3가 중앙 관리(EPP)되고 있습니다. 예외 설정이 필요하면 IT 담당자에게 요청해야 합니다.",
    "setup.check.disk_ok": "D: 여유 공간 {free_gib:.0f} GiB (필요 {need_gib} GiB)",
    "setup.check.whpx_state": "Windows 하이퍼바이저 플랫폼: {state}",
    "setup.license.intro": "아래는 Google Android SDK 라이선스 전문입니다. 끝까지 읽어 주세요.",
    "setup.license.packages": "적용 대상: {packages}",
    "setup.license.prompt": "동의하면 '동의'라고 직접 입력하세요. 동의하지 않으면 빈 줄로 Enter: ",
    "setup.license.declined": "동의하지 않아 설치를 멈췄습니다. 언제든 다시 실행할 수 있습니다.",
    "setup.license.console_only": "라이선스 동의는 사용자가 직접 터미널(콘솔)에서 입력해야 합니다. 터미널을 열고 다시 실행하세요.",
    "setup.whpx.prompt": "Windows 하이퍼바이저 플랫폼을 켭니다. Enter를 누르면 UAC 창이 뜹니다. 건너뛰려면 n: ",
    "setup.whpx.running": "켜는 중입니다(1–3분). 창을 닫지 마세요.",
    "setup.whpx.continue_prompt": "WHPX 없이 내려받기만 계속할까요? (예/아니오): ",
    "setup.reboot.needed": "Windows를 다시 시작하면 설치가 자동으로 이어집니다. 지금 바로 다시 시작하지 않아도 됩니다.",
    "setup.done": "설치가 끝났습니다. 휴대폰 첫 준비는 다음 단계에서 진행합니다.",
    "setup.provision.later": "휴대폰 첫 준비는 Phase 2에서는 검증 스크립트가, Phase 3부터는 프로그램이 자동으로 합니다.",
    "setup.quarantine.missing": "사라진 파일: {files}",
    "setup.extract.locked": "백신(Windows Defender·V3)이 방금 푼 파일을 검사하느라 2분 넘게 붙잡고 있어 설치를 마치지 못했습니다. 잠시 뒤 다시 실행하세요. 계속되면 IT 요청문으로 검사 예외를 요청하세요.",
    "setup.unexpected": "예상하지 못한 오류로 이 단계가 멈췄습니다. 다시 실행해 보고, 계속되면 로그 파일을 알려 주세요.",
    # 엔진 상태 (EngineStatus.text_key)
    "engine.state.not_installed": "설치가 필요합니다",
    "engine.state.stopped": "휴대폰이 꺼져 있습니다",
    "engine.state.preflight": "휴대폰 켤 준비 중…",
    "engine.state.launching": "휴대폰 켜는 중… (약 5초)",
    "engine.state.connecting": "휴대폰에 연결하는 중…",
    "engine.state.booting": "휴대폰 켜는 중…",
    "engine.state.session_init": "휴대폰 마무리 준비 중…",
    "engine.state.ready": "준비됨",
    "engine.state.resetting": "휴대폰을 처음 상태로 되돌리는 중…",
    "engine.state.provisioning": "휴대폰 처음 준비 중 (약 5분) — 한 번만 합니다",
    "engine.state.provisioning_rebuild": "휴대폰을 다시 준비하는 중 (약 5분)",
    "engine.state.provisioning_gpu": "그래픽 장치가 바뀌어 휴대폰을 다시 준비하는 중 (약 5분)",
    "engine.state.provisioning_save": "휴대폰 상태를 저장하는 중…",
    "engine.state.stopping": "휴대폰 끄는 중…",
    "engine.state.failed": "휴대폰을 켜지 못했습니다",
    # 사전 점검 칩 (Issue.text_key, §7.4)
    "issue.hw_nfc": "NFC는 PC 휴대폰에서 동작하지 않습니다. 실제 휴대폰으로 시험하세요.",
    "issue.hw_bluetooth": "Bluetooth는 PC 휴대폰에서 동작하지 않습니다. 실제 휴대폰으로 시험하세요.",
    "issue.hw_serial": "USB 시리얼(Serial)은 PC 휴대폰에서 동작하지 않습니다. 실제 휴대폰으로 시험하세요.",
    "issue.needs_mic": "이 앱은 마이크를 씁니다. PC 휴대폰의 마이크는 꺼져 있어 녹음은 무음이 되고, 음성 인식은 'No Match'(오류 3806)가 됩니다.",
    "issue.scanner": "PC 휴대폰 카메라는 가짜 영상이라 실제 바코드를 비출 수 없습니다. UseExternalScanner를 끄면 오류 1501 없이 스캐너 화면은 열리지만 인식은 되지 않습니다.",
    "issue.legacy": "오래된 App Inventor로 빌드한 앱입니다. 설치할 때 호환 옵션을 자동으로 붙입니다.",
    "issue.legacy_review": "오래된 App Inventor로 빌드한 앱입니다. 처음 실행하면 권한 검토 화면이 먼저 나옵니다 — [계속]을 누르세요.",
    "issue.arm_translation": "ARM용 부품을 번역해서 실행하므로 느릴 수 있습니다.",
    "issue.orientation_locked": "가로로 고정된 앱입니다. 실행하기 전에 PC 휴대폰을 가로로 돌립니다.",
    "issue.vibrate": "진동은 PC에서 느낄 수 없습니다.",
    "issue.location": "위치는 처음에 서울시청입니다. LocationSensor 기본값(TimeInterval 60000 ms, DistanceInterval 5 m)에서는 첫 위치 뒤 약 1분마다, 5 m 넘게 움직일 때만 LocationChanged가 옵니다. 빨리 보려면 TimeInterval을 1000으로 바꾸세요. 권한 창에서 '대략적인 위치'를 고르면 10분에 한 번만 갱신되니 [이 앱 권한 모두 허용]으로 정확한 위치를 허용하세요.",
    "issue.tts_missing": "이 PC 휴대폰의 말하기(TTS) 엔진이 꺼져 있어 TextToSpeech가 동작하지 않습니다. [휴대폰 초기화]를 하면 처음 상태로 돌아옵니다.",
    "issue.speech_missing": "이 PC 휴대폰에는 음성 인식 앱이 없어 SpeechRecognizer가 동작하지 않습니다.",
    "issue.cold_fresh_app": "느린 그래픽 모드에서는 휴대폰 초기화가 수 분 걸려서, '실행할 때마다 휴대폰 초기화' 대신 이 앱의 데이터만 지우고 실행했습니다.",
    # 실행 중 문제 카드의 자세한 문구 (OrcusError.message_key, §7.8)
    "app.speech_missing": "이 PC 휴대폰에는 음성 인식 앱이 없어 오류가 났습니다. 음성 인식은 실제 휴대폰이나 Companion으로 시험하세요. (SpeechRecognizer의 UseLegacy를 끄면 오류 창 대신 ErrorOccurred로 받습니다.)",
    "app.tts_missing": "이 PC 휴대폰의 말하기(TTS) 엔진이 응답하지 않아 말하기가 실패했습니다. [휴대폰 초기화] 뒤 다시 실행해 보세요.",
    "app.error.0": "SpeechRecognizer가 음성 인식 서비스에 연결하지 못했습니다(오류 0 'Try Again'). 다시 시도하거나 실제 휴대폰으로 시험하세요.",
    "app.error.601": "ActivityStarter가 실행할 화면(액티비티)을 찾지 못했습니다(오류 601). Action·ActivityPackage·ActivityClass 값을 확인하세요.",
    "app.error.908": "권한이 거부되었습니다(오류 908). [이 앱 권한 모두 허용]을 누르거나, 앱이 다시 물을 때 [허용]을 누르세요.",
    "app.error.1501": "PC 휴대폰에는 바코드 스캐너 앱이 없습니다(오류 1501). 카메라도 가짜 영상이라 UseExternalScanner를 꺼도 실제 바코드는 인식되지 않습니다.",
    "app.error.2701": "TextToSpeech가 아직 준비되지 않았습니다(오류 2701). AvailableLanguages·AvailableCountries는 말하기 엔진이 준비된 뒤에 읽으세요.",
    "app.error.3802": "SpeechRecognizer에서 앱 쪽 오류가 났습니다(오류 3802 'Client Side Error'). 다시 실행해 보세요.",
    "app.error.3806": "SpeechRecognizer가 말소리를 알아듣지 못했습니다(오류 3806 'No Match'). PC 휴대폰의 마이크는 꺼져 있어 항상 이렇게 됩니다. 음성 인식은 실제 휴대폰으로 시험하세요.",
    # 실행 단계 (RunEvent.stage)
    "run.stage.staging": "파일을 가져오는 중…",
    "run.stage.inspecting": "앱 정보를 읽는 중…",
    "run.stage.checking": "PC 휴대폰에서 되는지 확인하는 중…",
    "run.stage.waiting_engine": "휴대폰이 켜지기를 기다리는 중… 이어서 설치합니다",
    "run.stage.installing": "설치 중…",
    "run.stage.launching": "실행하는 중…",
    "run.stage.running": "실행 중",
    "run.stage.exited": "앱이 종료되었습니다",
    "run.stage.crashed": "앱이 멈췄습니다",
    "run.stage.failed": "실행하지 못했습니다",
    "run.stage.cancelled": "취소했습니다",
    # 결정 대화상자의 선택지 (Prompt.choices)
    "prompt.choice.replace": "바꾸기",
    "prompt.choice.cancel": "취소",
}


def spec(code: ErrorCode) -> MessageSpec:
    return ERRORS[code]


def error(code: ErrorCode, detail: str = "", log_excerpt: str = "", *,
          message_key: str | None = None, remedy_label_key: str | None = None) -> OrcusError:
    """OrcusError with the table's primary remedy.

    `message_key` picks a more precise sentence than the code's default (same channel and remedy).
    """
    action = ERRORS[code].remedy
    remedy = Remedy(action, remedy_label_key or f"remedy.{action}") if action else None
    return OrcusError(code, message_key or f"error.{code}", detail=detail, remedy=remedy, log_excerpt=log_excerpt)


def text(key: str, **fmt: object) -> str:
    """Resolve a TEXT key, 'error.<code>' or 'remedy.<action>'. Unknown keys raise KeyError."""
    if key in TEXT:
        value = TEXT[key]
    elif key.startswith("error."):
        value = ERRORS[ErrorCode(key.removeprefix("error."))].text
    elif key.startswith("remedy."):
        value = REMEDY_LABELS[RemedyAction(key.removeprefix("remedy."))]
    else:
        raise KeyError(key)
    return value.format(**fmt) if fmt else value


IT_REQUEST_WHPX = """[IT 지원 요청] Windows 하이퍼바이저 플랫폼(WHPX) 사용

PC 이름: {computer}
사용자: {user}
요청: Windows 기능 'Windows 하이퍼바이저 플랫폼'(HypervisorPlatform)을 켜 주세요. 재부팅이 1회 필요합니다.
관리자 명령: dism /online /enable-feature /featurename:HypervisorPlatform /all /norestart
이유: 수업용 App Inventor 앱 실행기(Orcus APK Emulator)가 Google Android Emulator를 이 기능 위에서 실행합니다.
결과: {detail}
"""

IT_REQUEST_V3 = """[IT 지원 요청] 백신 실시간 검사 예외 (AhnLab V3 / Microsoft Defender)

PC 이름: {computer}
사용자: {user}
요청: 아래 폴더를 실시간 검사 예외로 등록해 주세요.
  D:\\OrcusAPK\\sdk  (Google Android Emulator {build}, platform-tools)
  D:\\OrcusAPK\\avd  (가상 휴대폰 디스크)
실행 파일: emulator.exe, qemu-system-x86_64-headless.exe, qemu-system-x86_64.exe, adb.exe
이유: 압축을 푼 SDK 파일이 사라지거나 검사 중에 잠겨 설치가 멈췄습니다. {detail}
"""


def it_request(template: str, detail: str, **extra: str) -> str:
    return template.format(computer=os.environ.get("COMPUTERNAME", "?"), user=current_user(), detail=detail,
                           **extra)


def current_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user
