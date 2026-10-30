# Orcus APK Emulator

> **MIT App Inventor 전용 초경량 Windows 11 Android APK 가상 실행기**  
> 드래그 앤 드롭으로 10초 내에 앱을 설치하고 실행하는 고성능·저지연 데스크톱 런타임

[![Version](https://img.shields.io/badge/version-v0.1.0-blue.svg)](pyproject.toml)
[![Python](https://img.shields.io/badge/Python-3.14-green.svg)](pyproject.toml)
[![GUI](https://img.shields.io/badge/GUI-PySide6--Essentials-orange.svg)](pyproject.toml)
[![Hypervisor](https://img.shields.io/badge/Hypervisor-WHPX-purple.svg)](docs/ARCHITECTURE.md)
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](docs/ARCHITECTURE.md)

---

## 📌 프로젝트 소개 (Overview)

**Orcus APK Emulator**는 MIT App Inventor로 개발된 교육용 Android 애플리케이션(DEX 기반, 표준 View/Canvas/센서)을 Windows 11 환경에서 가장 빠르고 간편하게 테스트할 수 있도록 설계된 전용 실행기입니다.

BlueStacks, Nox 등 무거운 범용 상용 에뮬레이터의 복잡한 UI, 광고, 과도한 백그라운드 리소스 소모 문제를 해결하고, 공식 **Google Android Emulator**를 창 없이(Headless, `-no-window -gpu host`) 구동하여 토큰 기반 **gRPC 스트리밍**으로 화면과 입력을 완벽하게 제어합니다.

### 🎯 핵심 목표
- **드래그 앤 드롭 즉시 실행**: APK 파일을 윈도우 창으로 드롭하면 10초 이내에 화면이 표시되고 바로 조작 가능.
- **골든 스냅샷(Golden Snapshot) 기반 초고속 부팅**: 매번 콜드 부팅을 하지 않고 깨끗하게 준비된 스냅샷에서 10초 이내에 인스턴트 부팅.
- **최소 상주 리소스**: GUI, 전용 Private ADB Server, `emulator.exe`, `qemu` 등 4개 프로세스로 경량화 유지.
- **완벽한 프로세스 수명 관리**: Windows Job Object (`KILL_ON_JOB_CLOSE`)를 통해 프로그램 종료 또는 비정상 종료 시 에뮬레이터 자식 프로세스 누수를 100% 차단.

---

## 🏗️ 시스템 아키텍처 (Architecture)

```text
+---------------------------------------------------------------------------------------+
|  OrcusAPK.exe (Python 3.14 + PySide6, Windows Job Object: KILL_ON_JOB_CLOSE)          |
|                                                                                       |
|   [GUI Layer]                                                                         |
|     - PySide6 9:16 모바일 뷰포트 (저지연 래스터 렌더링)                               |
|     - 마우스 좌표 -> 터치 이벤트 변환, 하드웨어 키(Back/Home/Recents)                |
|     - 한글 입력 바 (Windows IME 조합 -> 클립보드 주입)                                |
|     - [앱 카드] / [로그 & 진단 카드] 패널                                             |
|        │ 비차단 호출 (Non-blocking SessionApi)                                        |
|        ▼                                                                              |
|   [Core Layer: orcus.contracts & orcus.common]                                        |
|     ├─ ApkService    : APK 판정/스테이징, 매니페스트 파싱, 설치 및 라이프사이클 감시  |
|     ├─ EngineControl : VM 상태 머신, 소유권 잠금, 포트 관리, 골든 스냅샷 관리         |
|     └─ DeviceSession : gRPC 어댑터 (RGB888 화면 스트림, 입력 큐, 센서/로그캣 제어)    |
+----------------───────┬───────────────────────────────────┬───────────────────────────+
                        │                                   │ localhost:8640 (gRPC Bearer)
                        ▼ [Child Process]                   ▼ [Child Process]
             adb.exe -P 15037 server              emulator.exe -no-window -gpu host
             (Private 격리 ADB 서버)                         │
                                                            ▼ [Grandchild Process]
                                                   qemu-system-x86_64-headless.exe
                                                   (Android 16 / API 36, WHPX, Host GPU)
```

### 계층 분리 원칙
- **엄격한 의존성 격리**: `orcus.contracts`에 표준 라이브러리 기반 Protocol, Dataclass, ErrorCode 정의.
- `engine`, `device`, `apk`, `setup` 모듈은 Qt(PySide6)를 전혀 참조하지 않음.
- `gui` 모듈은 `contracts`와 `common` 인터페이스만 참조하며 코어 비즈니스 로직에 직접 결합되지 않음 (pyright 및 AST import 테스트로 강제).

---

## 🛠️ 기술 스택 (Tech Stack)

| 구분 | 선택 기술 | 선정 이유 |
|---|---|---|
| **백엔드 언어** | **Python 3.14** (install-manager 기반) | 런타임 컴파일러 불필요, 우수한 AXML 파서 생태계, 단일 언어 스택 유지 |
| **GUI 프레임워크** | **PySide6-Essentials 6.12** (LGPL) | WebEngine 제외 82 MB 경량화, 1:1 저지연 래스터 렌더링, IME 완벽 지원 |
| **에뮬레이터 엔진** | **Google Android Emulator 37.2.12** + **WHPX** | Windows Hyper-V/VBS 활성화 환경 구동 가능한 공식 gRPC 탑재 엔진 |
| **시스템 이미지** | **Android 16 (API 36) google_apis x86_64** | App Inventor 최신 권장 API 타깃 지원, 한국어 TTS 및 Root 권한 지원 |
| **화면/입력 전송** | **Emulator gRPC API** (`streamScreenshot` / `streamInputEvent`) | 게스트 OS 내 별도 인코더 부하 없음, 프레임/입력/센서 통합 제어 |
| **APK 분석** | **apkInspector 1.3.7** | 순수 Python 30 KB 초경량 매니페스트 파서 (UTF-16 문자열 풀 완벽 대응) |
| **디버그 브릿지** | **Private ADB Server (포트 15037)** | 기본 5037 포트 및 외부 휴대폰/디바이스와의 간섭을 완전 차단 |
| **저장소 전략** | **D:\OrcusAPK** (SDK/AVD), **D:\OrcusDev** (개발/캐시) | 한글 경로 및 Google Drive 동기화 충돌 방지, C: 드라이브 용량 절약 |

---

## 📊 현재 진행 현황 (v0.1.0 / Phase 2)

현재 프로젝트는 **Phase 1(아키텍처 및 상세 설계)을 승인 완료**하고, **Phase 2(프로젝트 골격, 공통 인프라, 설치 및 검증 모듈 구축)** 단계의 핵심 기반 구현을 달성했습니다.

```mermaid
flowchart LR
    A["Phase 1: 설계 & 아키텍처 검증<br/>(완료)"] --> B["Phase 2: 골격 & 인프라 & 스파이크<br/>(현재: v0.1.0)"]
    B --> C["Phase 3a: 핵심 런타임 & GUI<br/>(예정)"]
    C --> D["Phase 3b: 센서 & 한글 & 설정<br/>(예정)"]
    D --> E["Phase 4: 패키징 & 정식 배포<br/>(예정)"]
    style A fill:#2d6a4f,stroke:#1b4332,color:#fff
    style B fill:#1d3557,stroke:#457b9d,color:#fff
    style C fill:#4a4e69,stroke:#22223b,color:#fff
    style D fill:#4a4e69,stroke:#22223b,color:#fff
    style E fill:#4a4e69,stroke:#22223b,color:#fff
```

### ✅ 구현 완료 내역 (v0.1.0)

1. **상세 설계 및 아키텍처 확정 (`docs/`)**
   - [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): 적대적 검토 66건 및 정밀 점검 52건을 반영한 v3 설계 명세서 완성.
   - [docs/research/phase1-research-brief.md](docs/research/phase1-research-brief.md): 6개 주제 리서치 요약 및 17건 팩트체크 완료.
   - [plan.md](plan.md): 전체 마스터 플랜 및 수용 기준(A1~A9, B1~B15) 수립.
2. **계층 분리 계약 정의 (`src/orcus/contracts/`)**
   - `apk.py`: APK 파일 분류(APK/AAB/AIA/AIX), 매니페스트 메타데이터, 설치/실행 인터페이스 정의.
   - `device.py`: 프레임 스트림, 마우스/터치/키보드 입력 큐, 센서/클립보드/로그캣 프로토콜 정의.
   - `engine.py`: 에뮬레이터 수명주기, 상태 머신, 포트/토큰/골든 스냅샷 관리 인터페이스.
   - `errors.py`: 에러 코드 체계(`ErrorCode`, `OrcusError`) 수립.
   - `session.py`: GUI-엔진 간 비차단 Session API 계약 정의.
   - `setup.py`: 환경 셋업 단계(`SetupStep`, `SetupProgress`, `DoctorReport`) 정의.
3. **OS 특화 및 공통 인프라 모듈 (`src/orcus/common/`)**
   - `winjob.py` / `win32.py`: Win32 Job Object 기반 프로세스 그룹화 및 프로세스 수명 강제 종료 (`KILL_ON_JOB_CLOSE`).
   - `paths.py`: ASCII 안전 경로 격리 (`D:\OrcusAPK`, `D:\OrcusDev`) 및 경로 유효성 검사.
   - `childenv.py`: 자식 프로세스 환경변수 엄격한 화이트리스트 구성 (시스템 오염 방지).
   - `ports.py`: 포트 충돌 방지 및 안전한 임시 포트 탐색.
   - `accel.py`: WHPX(Windows Hypervisor Platform) 상태 및 `emulator-check` 가속 진단.
   - `messages_ko.py`: 사용자 친화적 한국어 안내 메시지 및 에러 코드 매핑.
   - `config.py`, `log.py`, `jsonio.py`, `clock.py`, `pins.py`, `hostinfo.py`.
4. **SDK 자동화 설치 파이프라인 (`src/orcus/setup/`)**
   - `repo_index.py`: Google 공식 SDK 리포지토리 XML 파싱 및 다운로드 링크 추출.
   - `download.py`: SHA-1 해시 무결성 검증 및 이어받기 지원 다운로더.
   - `extract.py`: Zip-Slip 방지 안전 압축 해제 및 백신(V3) 격리 감지 로직.
   - `license.py`: `android-sdk-license` 무인 우회 금지 및 사용자 직접 확인 인터페이스.
   - `whpx.py`: DISM을 통한 WHPX 기능 활성화 및 UAC/재부팅 대기 처리.
   - `steps.py`: 8단계 순차적 셋업 워크플로 구현.
5. **독립 개발 환경 및 도구 체계 (`tools/`)**
   - `bootstrap.py`: Python 3.14 기반 독립 가상환경(`D:\OrcusDev\venv`) 및 해시 락 의존성 설치 자동화.
   - `dev.py`: D: 드라이브 캐시 격리 기반 `test`, `typecheck`, `check`, `cli` 통합 러너.
   - `lock.py`: `pip-tools` 기반 의존성 해시 고정(`requirements.lock`, `requirements-dev.lock`).

---

## 📂 디렉터리 구조 (Directory Structure)

```text
Orcus_APK_Emulator/
├── docs/                       # 상세 설계 문서 및 조사 보고서
│   ├── ARCHITECTURE.md         # 아키텍처 상세 명세 (v3)
│   └── research/               # Phase 1 기술 리서치 브리프
├── src/
│   └── orcus/                  # Orcus 메인 패키지
│       ├── common/             # 공통 유틸리티 (Win32, Job, 경로, 포트, 로깅)
│       ├── contracts/          # 계층 간 계약 및 인터페이스 정의 (Qt 독립)
│       └── setup/              # SDK 다운로드, 압축 해제, WHPX 설정 파이프라인
├── tools/                      # 개발 전용 도구 (Python 전용, D: 드라이브 격리)
│   ├── bootstrap.py            # 개발 가상환경 및 의존성 셋업
│   ├── dev.py                  # 테스트 / 타입체크 / CLI 실행 러너
│   ├── devenv.py               # 개발 환경 경로 및 런타임 제어
│   └── lock.py                 # requirements.lock 갱신 도구
├── plan.md                     # 마스터 계획 및 수용 기준 (A1~A9, B1~B15)
├── pyproject.toml              # 프로젝트 메타데이터 및 빌드 설정
├── requirements.in             # 런타임 의존성 명세
├── requirements.lock           # 해시 고정 런타임 락파일
├── requirements-dev.in         # 개발용 의존성 명세
└── requirements-dev.lock       # 해시 고정 개발용 락파일
```

---

## 🚀 개발 환경 설정 및 검증 (Quick Start)

### 시스템 사전 요구사항
- **OS**: Windows 11 (64-bit)
- **CPU**: 가상화(VT-x / AMD-V) 지원 및 Windows 하이퍼바이저 플랫폼(WHPX) 활성화 가능 환경
- **디스크**: `D:` 드라이브 여유 공간 최소 20 GB 권장 (SDK, AVD, venv 격리 저장용)
- **Python**: Python 3.14 (기본 설치 경로 권장)

### 개발 환경 구축
```powershell
# 1. 저장소 클론 및 이동
git clone https://github.com/OrcusExtreme/Orcus_APK_Emulator.git
cd Orcus_APK_Emulator

# 2. 가상환경 생성 및 의존성 설치 (D:\OrcusDev에 격리 설치)
py -3.14 tools/bootstrap.py

# 3. 정적 타입 검사 (Pyright)
py -3.14 tools/dev.py typecheck

# 4. 전체 검사 (테스트 + 타입체크)
py -3.14 tools/dev.py check
```

### SDK·가상 휴대폰 설치 (`orcus-cli setup`)
라이선스 동의는 사람이 직접 입력해야 하므로 **본인 터미널**(Windows Terminal 또는 VS Code 터미널)에서 실행합니다.
```powershell
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli setup    # 설치 1–8단계 (D:\OrcusAPK, 약 13–14 GiB)
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli doctor   # 상태 점검 (아무것도 바꾸지 않음)
```
PC 점검 → Google SDK 라이선스 전문 확인 후 `동의` 입력 → Windows 하이퍼바이저 플랫폼 켜기(UAC [예]) → 내려받기·압축 풀기
→ Windows 다시 시작(로그인하면 자동으로 이어짐) → 가속 확인 → 가상 휴대폰 만들기. 테스트 앱 준비는 [docs/fixtures.md](docs/fixtures.md).

---

## 🗺️ 향후 로드맵 (Roadmap)

- [x] **Phase 1: 아키텍처 & 상세 설계** (완료, 2026-10-30)
- [ ] **Phase 2: 골격 구축 및 검증 스파이크** (현재 단계)
  - [x] 프로젝트 레이아웃, `orcus.contracts`, `orcus.common`, `orcus.setup` 기본 구현
  - [ ] gRPC proto 스텁 생성 (`orcus.emugrpc`) 및 Mock 테스트 서버
  - [ ] 스파이크 하네스 (`tools/spikes/`) 구축 및 S0a ~ S9 측정/검증
  - [ ] PyInstaller onedir 패키징 스모크 테스트
- [ ] **Phase 3a: 핵심 실행 런타임 & GUI**
  - [ ] `engine`: 상태 머신, Private ADB 감시, 골든 스냅샷 로드/저장
  - [ ] `apk`: 드래그 앤 드롭 파이프라인, 설치 및 `am start`, logcat 감시
  - [ ] `gui`: PySide6 메인 창, 9:16 모바일 뷰포트, [앱] 및 [로그] 패널
- [ ] **Phase 3b: 센서, 한글 입력 및 설정**
  - [ ] [센서] 패널: 흔들기(Shake), 화면 회전, 기울기, 가상 위치(GPS)
  - [ ] 한글 입력 바: Windows IME 조합 텍스트 주입 지원
- [ ] **Phase 4: 마감, 패키징 및 배포**
  - [ ] Inno Setup 기반 사용자 인스톨러 생성
  - [ ] 상태 유지(Keep-state) 모드 및 최종 안정화

---

## 📄 라이선스 및 저작권

- 본 프로젝트의 자체 개발 코드는 독점 및 내부 연구/교육 목적으로 관리됩니다.
- 사용하는 외부 오픈소스 및 툴킷(PySide6-Essentials, gRPC, apkInspector, Android SDK)은 각 라이브러리의 원본 라이선스를 준수합니다.
- Android Emulator 및 SDK 구성 요소는 Google의 `android-sdk-license` 정책을 따르며 재배포되지 않고 사용자의 로컬 환경에서 정식 채널을 통해 다운로드됩니다.
