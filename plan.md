# Orcus APK Emulator — 계획

> **상태: Phase 3a 진행 중.** Phase 2는 2026-10-30에 끝났다(A9 승인). Phase 1(v3)은 2026-10-30에 승인되었다(★D0–★D4 모두 권장값, D5–D17도 권장값으로 진행).
> 라이선스 동의(★D2)와 UAC 승인·재부팅(★D1)은 사용자가 직접 한다. 에이전트는 이 둘을 대신하지 않는다.
>
> - 상세 설계: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 적대적 검토 66건과 수정 점검 52건을 반영했다. 추적 표는 §16에 있다.
> - 조사 근거: [docs/research/phase1-research-brief.md](docs/research/phase1-research-brief.md) — 검증한 사실 요약과 정정 17건.
> - 이 문서의 "Phase 1 결과"와 "승인 요청 사항"이 다른 문서보다 우선한다. 진행하면서 결정이 바뀌면 맨 아래 진행 기록에 남긴다.

## 0. 목표

MIT App Inventor에서 내보낸 APK를 Windows 11 창에 **끌어다 놓으면 바로 설치되고 실행되는** 가벼운 프로그램을 만든다.
- BlueStacks/Nox보다 빨리 뜨고 가볍게 만든다. '가볍다'의 기준은 §1.7과 ★D0(d)에서 정한다.
- Android/ART를 직접 구현하지 않는다. 검증된 에뮬레이터 엔진을 창 없이 띄워 조종한다.
- 대상 APK: 순수 DEX, 표준 View/Canvas/센서/미디어, 네이티브 .so는 거의 없음.

---

## 1. Phase 1 결과 — 아키텍처·기술 스택 결정

### 1.1 한 문단 요약

**Python 3.14 + PySide6** 프로그램 하나가 Google **Android Emulator 37.2.12**를 **WHPX**(Windows 하이퍼바이저 플랫폼) 위에서 창 없이(`-no-window -gpu host`) 띄운다. 화면은 에뮬레이터의 **토큰 보호 gRPC API**로 받고, 입력과 센서 값도 같은 API로 보낸다.
- **휴대폰:** API 36(Android 16) google_apis x86_64 이미지로 만든 AVD 하나. 해상도 720×1280, RAM 2.5 GB(에뮬레이터가 이 이미지에 요구하는 최소값, S6 실측).
- **빠른 시작:** 처음 한 번 준비해 둔 **골든 스냅샷**에서 매 세션 부팅한다. 목표는 10초 이내이고, Phase 2에서 측정한다.
- **설치·실행:** 드롭한 APK를 ASCII 경로로 복사하고 매니페스트를 읽은 뒤 `adb install`과 `am start`로 실행한다. 엔진이 켜져 있으면 드롭부터 화면까지 **10초 이내**가 목표다.
- **규모:** 상주 프로세스 4개(GUI, private adb, emulator, qemu — 부가 프로세스는 S4에서 확인), 런타임 의존성 4개(PySide6-Essentials, grpcio, protobuf, apkInspector).
- **저장 위치:** 큰 파일은 모두 `D:\OrcusAPK`(ASCII, Drive 밖)에 둔다.

### 1.2 구조

```
 OrcusAPK.exe (Python 3.14 + PySide6, one process, KILL_ON_JOB_CLOSE Job, engine ownership lock)
   gui  --(SessionApi, non-blocking)-->  app.Session
                             |-- apk     (2) APK Lifecycle : classify, stage, manifest, install, launch, watch
                             |-- engine  (1) Core Engine   : VM lifecycle, ports, private adb, snapshots
                             '-- device      gRPC adapter  : 9:16 frames, touch/keys, sensors, clipboard, logcat
        |                         |                               |
        v child                   v child                         | localhost:8640 + token
   adb -P 15037 server     emulator.exe -no-window ...  -->  qemu-headless (Android 16, WHPX, host GPU)
```

- **분리:** 패키지끼리 주고받는 타입은 모두 표준 라이브러리만 쓰는 `orcus.contracts` 하나에 정의한다(Protocol, dataclass, ErrorCode).
  - `engine`·`device`·`apk`·`setup`은 Qt를 import하지 않는다.
  - `gui`는 `contracts`·`common`만 보고, `engine`·`device`·`apk`는 보지 않는다.
- **강제:** 이 경계는 허용 목록 방식의 AST import 테스트와 pyright가 지킨다. 나중에 실기기(scrcpy) 백엔드를 붙여도 `device` 구현만 바꾸면 된다.

| 요구 컴포넌트 | 담당 |
|---|---|
| (1) Core Engine Manager — VM 수명주기, 포트, ADB | `engine` (+`setup`, `emugrpc`) |
| (2) APK Lifecycle & Automation — 매니페스트, `pm install`, `am start` | `apk` |
| (3) Host GUI Client — 폰 프레임, **9:16 뷰포트**, 실시간 화면, 마우스→터치, Back/Home/Recents, 드롭·파일 선택 | `gui` (+`device`) |

### 1.3 기술 스택

| 영역 | 결정 | 핵심 이유 | 기각한 대안 |
|---|---|---|---|
| 백엔드 언어 | **Python 3.14** (install-manager 런타임, venv는 `D:\OrcusDev`) | 모든 의존성에 cp314 휠이 있어 컴파일러·SDK가 C:에 오지 않음. 가장 나은 APK 매니페스트 파서가 Python에 있음. 설치·엔진·GUI를 한 언어로 끝냄 | C#/.NET 10+WPF(215 MB SDK가 C:로 감, AXML 파서가 부실, 언어가 둘이 됨), C++/Rust(무거운 일은 에뮬레이터가 하므로 이득이 작음), Store Python 3.13(AppData 가상화 함정) |
| GUI | **PySide6-Essentials 6.12** (LGPL), QtWidgets 래스터 1:1 출력 | WebEngine 없이 82 MB. 원격제어(Mirage) 캡처에도 안전하고 IME 지원 | PyQt6(GPL), Electron(무겁고 IPC가 복잡), Flutter(gRPC·IME 통합 부담), QOpenGLWidget(드라이버 의존) |
| 엔진 | **Google Android Emulator 37.2.12** (build 16428233, stable) + **WHPX** | Hyper-V/VBS가 켜진 이 PC에서 돌 수 있고 gRPC 제어면을 가진, 유지보수 중인 유일한 엔진 | 직접 QEMU + Android-x86(낡았고 Windows virgl 없음), Genymotion, WSA(종료), Waydroid, 차세대 Preview(API 37 전용) |
| 이미지 | **API 36 google_apis x86_64 r7** (S1 후 확정) | 현재 AI2 APK를 전부 설치할 수 있는 유일한 API(minSdk 최대 36). root 가능, ARM64 번역 | AOSP(TTS 없음), playstore(S1 실패 시 대안), API 37(RAM 하한 4 GB) |
| 화면·입력 | **에뮬레이터 gRPC**: `streamScreenshot` RGB888 + `streamInputEvent` | §1.4 | scrcpy, QEMU 공유 메모리 |
| APK 파싱 | **apkInspector 1.3.7** (+ 기기의 `resolve-activity` 폴백) | 순수 Python 30 KB, UTF-16 문자열 풀 처리 | androguard(의존성 36개, 개발 검증용으로만), aapt2(build-tools 추가 다운로드) |
| ADB | **platform-tools 37.0.1 adb.exe** 직접 호출, **private 서버 15037** (USB 끔, 스캔 끔, 직접 재등록) | 의존성 0. 다른 adb(5037)·학생 USB 휴대폰·다른 에뮬레이터와 간섭하지 않음 | adbutils(requests+Pillow, 자체 adb 동봉) |
| 동시성 | 스레드 + 큐. Qt 신호는 GUI 경계에서만 쓰고 GUI 호출은 모두 비차단 | 블로킹 gRPC 스트림과 잘 맞고 테스트가 쉬움 | asyncio/qasync, QtAsyncio |
| 패키징 (Phase 4) | **PyInstaller onedir + Inno Setup 6**. 해시로 잠근 venv에서 빌드. SDK는 첫 실행 때 다운로드 | 개발과 배포 런타임이 같아짐(과거 OpenCV 불일치 교훈). 컴파일러 불필요 | Nuitka(MSVC 필요), SDK 동봉(라이선스상 재배포 금지) |

### 1.4 화면 스트리밍·입력 프로토콜 결정

| 방식 | 장점 | 단점 | 결정 |
|---|---|---|---|
| **에뮬레이터 gRPC 인밴드 프레임** (RGB888, 서버가 창 크기로 줄여서 보냄) | 게스트 쪽 인코딩·디코더가 없음. 화면이 바뀔 때만 보냄. 센서·GPS·클립보드·스냅샷이 같은 채널. Android Studio와 같은 방식 | 프레임당 약 1 MB(432×768) 복사. Python 비용과 지연은 S2에서 측정 | **채택** |
| QEMU 공유 메모리 (gRPC MMAP `SHM_<handle>`) | 같은 에뮬레이터 프레임버퍼를 복사 없이 받음 | Windows에서 끝까지 검증된 적 없음. 동기화가 없어 화면이 찢어짐 | S2에서 fps·CPU·지연 예산을 넘을 때만 선택 |
| scrcpy 5.0.1 (H.264) | 실기기 지원, 대역폭이 작음 | 게스트 vCPU에서 소프트웨어 인코딩(앱과 경쟁). PyAV 약 70 MB + GPL 코덱. 센서 주입 불가. 버전이 정확히 맞아야 함 | 기각 → 나중에 실기기 백엔드로 |

**입력**은 `streamInputEvent` 하나로 보낸다.
- **마우스:** 마우스 하나 = 손가락 하나(`MouseEvent`). GUI는 프레임 안의 정규 좌표만 넘기고, 회전·패널 픽셀 변환은 `device`가 한다.
- **키:** Back/Home/Recents는 `GoBack`/`GoHome`/`AppSwitch` keypress.
- **한글:** Windows IME로 조합 → 클립보드 → Paste. 키 주입으로는 한글을 보낼 수 없다.
- **흔들기:** 클릭당 가속도 펄스 1회. 세기는 App Inventor 감도별로 정하고, 방향은 중력 쪽이다.

### 1.5 드래그앤드롭 → 실행 흐름 (요약)

1. 앱이 뜨자마자 엔진이 골든 스냅샷에서 부팅한다(목표 ≤ 10 s).
2. 드롭: 드래그하는 동안에는 파일을 열지 않고 확장자 힌트만 본다.
3. 드롭 뒤 파일 내용으로 판정한다(APK / AAB / AIA / AIX / 그 외 — APK가 아니면 쉬운 안내).
4. `D:\OrcusAPK\staging\<sha256>.apk`로 복사해 한글·Drive 경로를 격리한다(≤ 0.4 s).
5. 매니페스트에서 package, `.Screen1` launcher, 화면 방향, 이름, SDK, 권한, ABI, App Inventor 빌드 시기를 읽는다. **드롭 후 1 s 안에 앱 카드를 띄운다.**
6. 사전 점검:
   - 막음: minSdk > 36, ARM 32비트 전용 확장 기능
   - 경고: NFC/Bluetooth, 스캐너
   - 안내: 진동, 위치
7. 엔진 READY를 기다린다(켜져 있으면 0 s).
8. `adb install -r`. 옛 APK(target < 24)에는 우회 플래그를 붙인다.
   - **같은 파일을 다시 드롭했고, 데이터 유지 정책이고, 현재 epoch(초기화·재시작 전)에 이미 설치했다면** 설치만 건너뛴다.
9. 가로 고정 앱이면 가상 기기를 먼저 돌리고 `am start -W -n <pkg>/.Screen1`을 실행한다. 이어서 PID와 logcat을 감시한다: 크래시, 블록 실행 오류, 컴포넌트 오류, TTS·음성 인식 없음.
10. 첫 프레임이 나오면 "실행 중 · 4.8초" 토스트를 띄운다. 이후 마우스·키·한글·센서로 조작한다.

| 상황 | 목표 |
|---|---|
| 드롭 → 앱 카드 | ≤ 1 s |
| 엔진이 켜진 상태에서 드롭 → 앱 화면 | ≤ 10 s (p95) |
| 앱을 켜자마자 드롭 | ≤ 20 s |
| 최초 설치(2.3 GB 다운로드, WHPX 재부팅, 첫 준비) | 1회 10–20분 |

### 1.6 이 PC에 맞춘 점

| 이 PC의 사실 | 설계 반영 |
|---|---|
| Hyper-V/VBS 켜짐, **WHPX 꺼짐** (`WinHvPlatform.dll`은 있음) | WHPX만 쓴다(AEHD/HAXM/TCG 불가). 켜짐 여부는 DLL 유무가 아니라 Windows 기능 상태와 RebootPending으로 판단한다. 설치 단계에서 UAC로 켜고 재부팅 1회 |
| **C: 여유 약 20 GB** | 큰 파일은 전부 `D:\OrcusAPK`. C:에는 KB 단위 파일만 남는다(허용 목록). 개발 캐시도 D:로 |
| 프로젝트 폴더가 **한글 경로 + Google Drive 동기화** | 런타임·venv·캐시·빌드는 Drive 밖 ASCII 경로에 둔다. 드롭한 APK는 스테이징한다. 37.2.12의 비 ASCII AVD 경로 멈춤 버그를 피한다 |
| AhnLab V3가 **EPP로 중앙 관리**됨 | 압축 해제 후 파일 확인(격리 감지), 첫 실행 대기를 넉넉히, 예외 요청은 IT 요청문으로 |
| GTX 1660 + Mirage 미러 드라이버 | `-gpu host` 명시 + 렌더러 검증. GPU 모드는 수동 전환(자동 사다리는 Phase 4). 래스터 위젯 |
| **모니터 3대**: 주 1920×1080, 보조 1920×1080, 세로로 돌린 1920×1200(1200×1920) | 폰 화면 크기는 창이 놓인 화면 기준으로 계산한다. 주 모니터에서 약 432×768, 세로 모니터에서는 720×1280까지 |
| 꺼져 있는 Windows 시스템 프록시 설정이 있음 | 켜지면 에뮬레이터에 `-http-proxy`로 넘긴다(에뮬레이터는 시스템 프록시를 읽지 않음) |
| 로케일 cp949, Store Python 3.13 | 자식 출력은 UTF-8로 디코딩. install-manager 3.14만 허용 |
| cmd·PowerShell의 한글 경로 함정 | 개발 도구는 전부 Python(.bat/.ps1 없음) |
| 개발용과 배포용 런타임이 달라 고생한 적이 있음 | 잠근 venv에서 빌드하고, 패키징본으로 `--selftest`·`--boot-check`. Phase 2에 패키징 스모크 테스트 |

### 1.7 '가볍다'를 어떻게 지키나 — BlueStacks와 비교 (Orcus 값은 추정이며 S4에서 측정)

| 항목 | Orcus (목표·추정) | BlueStacks 5 (공식 최소 사양) |
|---|---|---|
| 시작 | 골든 부팅 ≤ 10 s 목표, 드롭 → 실행 ≤ 10 s | 공개 수치 없음 |
| 메모리 | VM에 2.5 GB 할당(이미지 최소값), 실제 사용량은 S4에서 측정 + GUI ≤ 250 MB | 최소 RAM 4 GB |
| 디스크 | **약 13–14 GiB** (에뮬레이터 1.07 + Google 이미지 4.37 + userdata 최대 6 + 골든 2) + 설치 중 임시 2.4 GB | 최소 여유 5 GB |
| 상주 프로세스 | 4개 (+netsimd 등은 S4에서 확인) | 공개 수치 없음 |
| 우리 프로그램 크기 | 설치 파일 20–30 MB, 설치 후 55–70 MB (SDK 제외) | — |

- **디스크는 BlueStacks보다 많이 쓴다.** 원인은 TTS가 든 Google 이미지, 강제 6 GiB인 userdata, 골든 RAM 파일이다.
- **줄이는 방법:** AOSP 이미지로 바꾸면 2.4 GiB가 줄지만 TTS가 없다. userdata가 희소 파일이라면 실제 사용량이 줄어든다(S4).
- **Nox:** 공식 수치를 확인하지 못했다.
- **결정 필요:** '가볍다'를 **시작 시간·상주 프로세스·메모리** 기준으로 정의하는 것이 ★D0(d)의 결정 사항이다.

### 1.8 주요 위험 Top 6 (전체는 ARCHITECTURE.md §13)

1. **WHPX를 켤 수 없음**(관리자 권한, 학교 정책, 복원 프로그램) — 차단 요인이다. Phase 2 첫날 S0a에서 확인한다.
2. **이미지에 한국어 TTS·음성 인식이 없거나 Google 계정 로그인이 필요함** → TalkToMe가 말하지 않는다. S1에서 확인한 뒤 이미지를 확정한다.
3. **헤드리스 GPU가 소프트웨어 렌더링으로 떨어짐** → 느려진다. S2에서 확인하고, 수동 GPU 모드와 COLD 모드로 대응한다.
4. **골든 스냅샷 동작 가정이 틀림** — 예: 강제 로드에 실패하면 에뮬레이터가 골든을 지운다. S4에서 확인한다. 대비책(quickboot 모델)은 '깨끗한 휴대폰' 약속까지 바꾸므로 다시 묻는다.
5. **입력 고착·한글 붙여넣기 실패** → S7·S8에서 확인한다.
6. **V3 격리·오탐 + EPP 정책 때문에 예외를 넣지 못함** → S0b와 패키징 스모크에서 확인하고, IT 요청문으로 대응한다.

---

## 2. 승인 요청 사항 (권장 기본값)

**Phase 2를 시작하려면 ★D0–★D4에 답이 필요하다.** 나머지는 권장값으로 진행하며, 언제든 바꿀 수 있다.

| # | 결정 | 권장 기본값 | 왜 중요한가 |
|---|---|---|---|
| ★D0 | **아키텍처와 범위 승인** — (a) Python 3.14 + PySide6-Essentials (b) 화면·입력은 에뮬레이터 gRPC 인밴드(scrcpy·공유 메모리 아님). 그래서 요청서의 **scrcpy-server jar는 받지 않음** (c) **Phase 2 범위 = 골격·설치 스크립트·버리는 스파이크 하네스**, 엔진 본체는 Phase 3 (d) '가볍다' = 시작 시간·상주 프로세스·메모리 기준, 디스크는 약 13–14 GiB | (a)–(d) 모두 예 | 요청서의 후보와 다른 길을 고른 부분, 그리고 범위를 명시적으로 확인받기 위해서 |
| ★D1 | **Windows 하이퍼바이저 플랫폼(WHPX) 켜기** — UAC 1회 + 재부팅 1회 | Phase 2 첫날 진행 | 필수다. 없으면 아무것도 돌지 않는다(대체 수단 없음). 학교 정책이 막으면 이 PC에서는 진행할 수 없다 |
| ★D2 | **Google android-sdk-license 동의** | "Phase 2 첫날 **내 터미널에서** 전문을 읽고 직접 '동의'를 입력하겠다" (예/아니오) | 다운로드의 전제다. 에이전트는 대신 동의할 수 없게 되어 있다(우회 옵션 없음) |
| ★D3 | **배포 범위** | 개인·수업용 무서명 빌드부터. 공개 배포는 법률 검토 후 | Google SDK 재배포 금지, ARM 번역 조항, 서명 비용 |
| ★D4 | **버전 관리** | **Phase 2 시작부터 추적**: 기존 `D:\Project_Lab` 저장소에서(지금 이 폴더는 미추적). 커밋은 요청할 때만, 단계 승인마다 태그를 제안 | 단계별 변경을 diff로 검토하고 되돌릴 수 있다. 참고: `D:\Project_Lab`의 `.git`도 Drive가 동기화한다. 더 안전한 대안은 Drive 밖 별도 저장소 |
| D5 | 권한 대화상자 | **실제 휴대폰처럼 표시** + [이 앱 권한 모두 허용] 버튼. 자동 허용(`-g`)은 옵트인 | 학생이 권한 요청 흐름(거부 시 오류 908 등)을 그대로 겪는다. 반대안: 편의를 위해 `-g` 기본 |
| D6 | 세션 모델 | **매 세션 깨끗한 골든 휴대폰** + 닫을 때 "저장 데이터가 지워집니다" 안내(골든 모드에서만). 세션 간 유지(keep-state)는 **Phase 4** 선택 기능 | 빠르고 예측 가능하고 크래시에 안전하다. keep-state도 그래픽 드라이버·에뮬레이터를 업데이트하면 보관 상태가 초기화된다 |
| D7 | 내비게이션 | ~~게스트 내비 바 숨김(`hw.mainKeys=yes`)~~ → **S7 결과로 변경: 준비 단계에서 3버튼 내비 강제** + 베젤의 ◁ ○ ▢ 버튼 | API 36은 `hw.mainKeys=yes`여도 제스처 내비로 동작했다. 마우스로는 제스처 내비가 안 되고 Canvas 가장자리 드래그와 충돌하므로 3버튼으로 바꾼다(게스트 화면 아래 바가 보임) |
| D8 | 시스템 이미지 | google_apis API 36 x86_64, **S1 후 확정**. 개인 Google 계정은 골든에 넣지 않음 | TTS(TalkToMe)와 root. TTS가 playstore에만 있으면 playstore 36 |
| D9 | 폴더 | 런타임 `D:\OrcusAPK`(공유 adb 키 포함), 개발 `D:\OrcusDev` | ASCII·Drive 밖·C: 밖이어야 한다. 약 14 GiB. 공유 키가 S9에서 안 되면 Windows 계정이 바뀔 때마다 재구축(약 3분) |
| D10 | 게스트 언어·시간대 | ko-KR, Asia/Seoul | 한국어 UI·TTS. 바꾸면 골든을 다시 만든다(약 3분) |
| D11 | 화면 | 720×1280 @320dpi를 9:16 비율로 창에 맞춤. 주 모니터 1920×1080에서 약 432×768, 세로 모니터에서는 오른쪽 패널을 접으면 720×1280 | 프레임이 가볍다. 창이 놓인 모니터 기준으로 크기를 계산한다 |
| D12 | 마이크 | 기본 끔, 앱별 동의 + 켜짐 표시 | 개인정보 |
| D13 | AhnLab V3 예외 | 처음엔 두지 않는다. S0b·S4에서 격리되거나 부팅이 20% 넘게 느려질 때만 **사용자 또는 IT 담당자가** `sdk`·`avd` 폴더를 예외로 추가(EPP 관리 PC는 IT 요청문) | 보안 트레이드오프. 영향을 재려고 잠시 예외를 두거나 실시간 감시를 꺼도 되는지도 정해야 한다 |
| D14 | Python | 설치된 3.14.x 유지(venv는 3.14 고정). 최신 3.14.x 업그레이드는 선택 | 시스템 변경을 최소로 |
| D15 | UI 언어 | 한국어(문자열은 `common/messages_ko.py` 한 파일). 영어는 Phase 4 | — |
| D16 | MVP에서 미루는 것 | scrcpy·PyAV, MMAP, adbutils, numpy, qasync, QtMultimedia, playstore, API 30, 다중 AVD, Companion | 다시 볼 조건은 ARCHITECTURE.md §15.4 |
| D17 | Phase 3 중간 확인 | **3a(핵심 실행: 엔진·APK·GUI 핵심, [앱]·[로그] 패널)를 마치면 한 번 승인받고 3b([센서] 패널·한글 입력·설정)로** | 범위가 큰 Phase 3를 두 번에 나눠 확인한다 |

### Phase 2에서 사용자가 할 일 (모두 합쳐 약 1.5–2시간)

| 할 일 | 언제 | 예상 시간 |
|---|---|---|
| WHPX 켜기 UAC 승인 + 재부팅 (★D1) | 첫날 | 5분 + 재부팅 |
| 라이선스 전문을 읽고 **내 터미널에서** '동의' 입력 (★D2) | 첫날 | 10분 |
| App Inventor(nb207)로 HelloPurr, TalkToMe 빌드 → `D:\OrcusDev\fixtures` | 스파이크 전 | 20분 |
| **OrcusProbe** 빌드(1쪽 명세 제공. WebViewer·녹음·BarcodeScanner·음성 인식 버튼 포함) | **S3·S5 전** | 30–40분 |
| PaintPot, BallBounce, MoleMash, 한글 이름 앱, AndroidMinSdk = 36 빌드, 가로 고정 앱 | **3a 수용 시험 전** | 35분 |
| (있다면) 2018년 이전 레거시 APK 제공. 없으면 레거시 시험은 '해당 없음'으로 기록 | 아무 때나 | — |
| V3 영향을 재려고 잠시 예외를 두거나 실시간 감시를 꺼도 되는지 결정 (D13) | S0b | — |

### Phase 2에서 이 PC에 생기는 변화

- Windows 기능 'Windows 하이퍼바이저 플랫폼'이 켜진다(재부팅 1회).
- 새 폴더 두 개가 생긴다:
  - `D:\OrcusAPK`: SDK, AVD, 공유 adb 키, 골든. 약 13–14 GiB.
  - `D:\OrcusDev`: venv, pip·pyright·npm 캐시, 빌드.
- C:에는 KB 단위 파일만 남는다: `%USERPROFILE%\.emulator_console_auth_token`, `.android\adbkey*`, `%LOCALAPPDATA%\Temp\avd\running\*`, 재부팅 재개용 RunOnce 1개(실행 후 자동 삭제).
- 시스템 전역 환경변수는 바꾸지 않는다. 관리자 권한은 dism 실행 한 번에만 쓴다.

---

## 3. Phase 계획

### Phase 2 — 골격·설치 스크립트·검증 스파이크

엔진 본체(상태 기계·골든 수명주기)와 APK 파이프라인은 **만들지 않는다.** 버리는 스파이크 하네스로 가정을 먼저 검증하고, 그 결과를 반영해 Phase 3에서 만든다.

**산출물**
1. **프로젝트 골격**
   - `pyproject.toml`(`requires-python = ">=3.14,<3.15"`)
   - `requirements.lock`(해시 고정): 런타임 4개 + 개발용(grpcio-tools, pytest, pytest-timeout, pyright, androguard, PyInstaller)
   - `src/orcus/` 레이아웃, `NOTICE`, `README.md`
   - 허용 목록 레이어 테스트, pyright 설정
2. **개발 도구**(Python만 사용)
   - `tools/bootstrap.py`: pip 캐시를 D:로 지정한 뒤 `D:\OrcusDev\venv`를 만들고 해시 설치
   - `tools/dev.py`: pycache·pip·pyright·npm·pytest 캐시를 D:로 돌린 뒤 test / typecheck / run / build 실행
   - `tools/gen_protos.py`, `tools/fetch_fixtures.py`
3. **`orcus.contracts`** — ARCHITECTURE.md §3에 이름이 나오는 모든 타입을 정의한다.
4. **`orcus.common`** 완성: 경로 가드, 설정, pins, AVD 파일, 자식 환경, `accel`(WHPX 상태·emulator-check), 타임아웃 보장 실행기, Job·잠금·프로세스 점검, 포트, `messages_ko`(문자열과 코드 → 채널 표), 로그
5. **`orcus.emugrpc`**: proto 라이선스 헤더 확인 → vendoring → 스텁 생성 → 채널 팩토리 → `FakeEmulatorServer`
6. **최소 조립**:
   - engine 순수 부품(명령줄 빌더 + 불변식 테스트, discovery 파서)
   - `engine/bootcheck.py`·`engine/selftest.py`
   - `app/compose.py`
7. **`orcus.setup` + `orcus-cli setup | doctor [--boot-check]`**: 설치 1–8단계
   - 경로 가드, **사람이 직접 하는 라이선스 동의**
   - WHPX 상태 판정 + UAC로 dism 실행(실행 실패와 종료 코드를 구분) + 재부팅 뒤 재개
   - 이어받기 다운로드 + SHA-1 검증
   - 압축 해제(zip-slip 방지, V3 격리 확인 + IT 요청문)
   - AVD 작성, 공유 adb 키 생성, 가속 확인
8. **스파이크 하네스 `tools/spikes/`**(버리는 코드, 패키징하지 않음):
   - 고정 명령줄 실행, discovery·토큰 채널, adb 호출과 재등록 메시지
   - 준비 스크립트 초안
   - 최소 PySide6 뷰어(프레임·마우스·키·한글 입력 바)
9. **스파이크 S0a–S9** → `docs/spikes.md`(측정값, 결정, 바뀐 설계)
10. **패키징 스모크**: 소스를 Drive 밖으로 복사한 뒤 골격을 PyInstaller onedir(창 EXE + 콘솔 EXE)로 빌드한다. 그다음 `--selftest`, `--boot-check`(`host`·`host_hidden`), V3 검사.
11. **유닛 테스트** 전부, **OrcusProbe 1쪽 명세**와 픽스처 목록

**스파이크** — 실행 순서: **S0a → (설치 다운로드) → S0b → S6·S9 → S2·S1 → S4 → S3·S5 → S7·S8**. 결과가 설계를 바꾸면 Phase 2 코드와 Phase 3 계획을 고쳐 다시 승인받는다.

| # | 질문 | 설계를 바꾸는 결과 |
|---|---|---|
| S0a (다운로드 전) | WHPX 상태: InstallState + RebootPending. UAC·정책으로 dism을 실행할 수 있는가? dism이 실행 호출자의 Job에 들어가는가(`IsProcessInJob`)? V3가 EPP로 관리되는가, 로컬에서 예외를 넣을 수 있는가? | 켤 수 없음 → 이 PC에서는 중단하고 보고 |
| S0b (압축 해제 후) | `emulator-check accel` 출력. 재부팅 뒤에도 켜져 있는가? V3가 다운로드·압축 해제·첫 실행을 방해하는가? | V3 격리 → 예외 설정(IT 요청)을 필수 설치 단계로 |
| S6 | `pid_*.ini`의 PID와 위치(TMP 변경의 영향), 남은 파일, `-port`만 줄 때의 gRPC 동작, 부팅 뒤 프로세스 목록, **시스템 프록시를 `-http-proxy`로 넘기는 동작** | discovery 규칙 확정 |
| S9 | `adb nodaemon server`가 Job 안에서 도는가. 15037 자동 등록, **재등록 메시지 `host:emulator:5641`로 바로 복구되는가(시간 측정)**. `MAX_PORT=5553`(스캔 끔). `-H 127.0.0.1` 클라이언트(서버를 몰래 띄우지 않음, `-s` 동작). `ADB_USB=0`, mDNS. **공유 adb 키(`ADB_VENDOR_KEYS`)를 다른 Windows 계정에서 써도 되는가.** 외부 5037과 양방향 격리. explorer.exe 넘기기(폴더·URL). Job 프로세스 점검 | 재등록 실패 → `adb connect`. nodaemon 불가 → DEVNULL start-server. 공유 키 불가 → 계정마다 재구축(D9). 넘기기 실패 → BREAKAWAY 경로 |
| S2 | 헤드리스 `-gpu host` 렌더러(Mirage·원격제어 켜고 끈 상태), `host` 대 `host_hidden`. **주 모니터 기본 크기(약 432×768)와 720×1280**에서 fps·CPU·프레임 나이·마우스→화면 지연. 행 순서, RGB888 대 RGB32, 이름 있는 키, 휠. (선택) 자식 환경 전면 허용 목록이 안전한가 | 기본 GPU 모드 결정. 예산 초과 → 프레임 크기·30 fps·MMAP |
| S1 | google_apis 36(필요하면 playstore 36도): TTS 엔진, 오프라인 ko-KR 음성, RECOGNIZE_SPEECH 제공자, 한국어 IME. **음성·인식기 설치에 Google 계정 로그인이 필요한가** | TTS 없음 → 이미지 변경(D8). 로그인 필요 → 다른 경로나 안내 카드 |
| S4 | 아래 항목을 측정·확인한다(실험은 임시 이름으로) | named 스냅샷 동작 불량 → 대비책(quickboot 모델, D6 재확인). 골든 부팅 > 10 s → 목표 재조정. 일시적 원인으로 골든이 지워짐 → 강제 로드 없이 + RAM 표식 방식 |
| S3 | 헤드리스 qemu에서 소리가 나는가(Orcus 창이 앞일 때와 최소화했을 때). OrcusProbe 녹음 버튼으로 `setMicrophoneState` 녹음이 되는가 | 무음 → `host_hidden` → `streamAudio` + QtMultimedia(+40 MB) |
| S5 | HelloPurr 설치·실행 시간, `-g`의 영향, 레거시 우회 + 대화상자 억제, 권한 대화상자·`pm grant`. 실제 logcat 문자열(OrcusProbe 버튼으로): 크래시(`am crash`), ANR(Probe의 ANR 버튼), RuntimeErrorAlert, E/Form(1501·2701·908·601·3802), InternalTTS(D 수준 로그가 전달되는가), SpeechRecognizer 세 경로. 2701과 음성 인식 없음은 해당 패키지를 `pm disable-user`로 끈 상태에서 본다. Play Protect·검증 프롬프트, 카메라 `imagefile`, 진동 기록 | 감시 패턴 표 확정. 검증 프롬프트 → 준비 단계에서 끔. imagefile이 됨 → Phase 4 카메라 이미지 기능 |
| S7 | OrcusProbe로: 흔들기 3개 감도(실제 표본 속도, **역방향 자세 포함**), 자동 회전, 회전 좌표 부호, 표시 회전을 읽는 명령, 가로 고정 Screen2의 프레임 동작, **기울기 20° 제한이 충분한가**, 휠 위치, 이동 이벤트 밀도, 입력 고착, `hw.mainKeys=yes`로 내비 바가 숨는가와 Back/Home/Recents | 흔들기 상수·좌표 표 확정. 내비 바가 안 숨음 → 3버튼 강제. 이동이 성김 → `GetMouseMovePointsEx` |
| S8 | 한글 붙여넣기: TextBox / MultiLine / Password / NumbersOnly / **WebViewer(Probe)**. Paste 키 대 Ctrl+V, 클립보드 토스트, Qt의 한/영 키 이벤트 | 기본 방식 결정. 둘 다 실패 → 입력 바를 주 경로로 + 게스트 IME 검토 |

**S4에서 확인할 것**
- **시간:** 콜드·첫 준비·골든 부팅 시간. 유휴 RAM·CPU(전체 프로세스). 디스크 증가(희소 파일인가).
- **골든 로드와 저장:**
  - `-force-snapshot-load` 실패 시 동작(골든이 지워지는가)
  - no-save 상태에서 `SaveSnapshot`의 `success`. gRPC로 이미 있는 이름에 저장하면 어떻게 되는가
  - 디스크 복원, 강제 종료 뒤 골든 무결성
  - 실행 중 `LoadSnapshot` + adb 재연결 + nonce 검증
  - 오래된 골든의 시계
- **설정이 바뀔 때:** 콘솔 포트·`-http-proxy` 변경의 영향. `-snapshot`만 줄 때 종료 시 저장하는가.
- **강제 로드 없이 부팅할 때:** 로드가 실패하면 조용히 콜드 부팅되는지, 스냅샷이 남는지, RAM 표식이 없는지(골든 대비책과 Phase 4 보관 슬롯에 필요).
- **Phase 4용:** 같은 이름 재저장과 저장 중 강제 종료.
- **준비 과정:** `-change-locale` 재시작 신호, 준비 명령(설정·잠금·위치·stayon), 첫 부팅 중 idle timeout.
- **부가 프로세스:** netsimd·crashpad가 뜨는가, Job에 속하는가, 얼마나 남는가.
- **V3:** 예외 전후 부팅 시간 비교(D13, 허용될 때).

**수용 기준**
- **A1.** 빈 `D:\OrcusAPK`에서 `orcus-cli setup`이 1–8단계를 끝까지 마친다. 라이선스는 사용자가 직접 동의하고, WHPX 켜기와 재부팅, 다운로드 검증, AVD 작성, 공유 adb 키, 가속 확인을 거친다. 두 번째 실행은 아무것도 하지 않는다.
- **A2.** `emulator-check.exe accel`(단독 실행)이 'WHPX 사용 가능'을 보고하고, 결과가 기록된다.
- **A3.** 스파이크 하네스로 첫 준비 → 골든 저장 → 골든 부팅을 확인한다. 콜드·준비·골든 부팅 시간을 **절대값으로** `docs/spikes.md`에 기록한다.
- **A4.** 스파이크 하네스로 HelloPurr를 설치·실행해 Screen1이 resumed 상태가 된다.
- **A5.** 하네스를 정지한 뒤, 그리고 하네스 프로세스를 강제 종료한 뒤 N s(S4에서 정함) 안에 **이미지가 `D:\OrcusAPK\sdk` 아래인 프로세스가 하나도 남지 않는다.**
- **A6.** 풋프린트: `D:\OrcusAPK` 크기를 추정치와 비교해 기록한다. C:는 ARCHITECTURE.md §11.5의 비교 범위에서 OS가 남기는 기록을 뺀 뒤, 우리 프로세스가 만든 변화가 허용 목록 안에만 있다. Drive 폴더에는 소스만 있다.
- **A7.** 유닛·레이어·pyright 검사를 통과하고, 플래그 불변식을 테스트한다. §3에 이름이 나오는 모든 타입이 `orcus.contracts`에 정의되어 있다.
- **A8.** 패키징본(창 EXE)에서 `--selftest`와 `--boot-check`(`host`·`host_hidden`)가 통과한다. V3가 막지 않는다. 막으면 대응을 기록한다.
- **A9.** S0a–S9 결과를 기록하고 설계 변경을 문서에 반영한다. Phase 3 계획을 고쳐 **사용자가 승인한다.**

### Phase 3a — 핵심 실행: 엔진 컨트롤러·APK 파이프라인·GUI 핵심

**산출물**
- **`engine`:** 상태 기계 전체. 소유권 잠금, epoch와 원인, STOPPING 의도별 출구, 준비·골든 수명주기, 초기화 검증, 실패 재구축 1회, 자동 재시작 1회, private adb 감시와 재등록, COLD 모드, GPU 렌더러 감지 + 배너 + GPU 모드 작은 대화상자(툴바 [설정]과 배너에서 연다).
  - **준비(Phase 2 결과, ARCHITECTURE §6.5):** 기기 설정 11개(3버튼 내비, 스타일러스 필기 끄기, Bluetooth 끄기, 클립보드 토스트 끄기 포함) → 3 s 뒤 `sync` → 깨끗한 콜드 세션에서 설정이 남았는지 확인 → 저장 → 검증 부팅(로드 30 s 뒤 adb 응답). 어느 단계든 실패하면 골든을 저장하지 않는다.
  - `session_init`: 위치 테스트 제공자(gps·fused) 준비 + 서울시청.
- **`device`:** 프레임, 입력 큐(포인터, 키, chord, 붙여넣기), 컨트롤 FIFO 스레드, logcat, 스크린샷. 포인터는 §9.2 표(S7에서 오차 0–1 px로 확정)로 바꾸고 떼기 보장을 지킨다(S7: 안 떼면 다음 "누름"이 같은 손가락의 이동이 됨). ASCII 문자도 붙여넣기로 보낸다(키 이벤트는 Gboard가 자모로 바꿈).
- **`apk`:** 판정·스테이징, 매니페스트(방향은 숫자 값), 사전 점검, 설치 정책과 오류 대응, 실행 전 방향 맞추기, 실행, 감시(크래시·블록 실행 오류·컴포넌트 오류·TTS·음성 인식, S5 실측 문자열: ANR은 WindowManager 줄, 음성 인식 없음은 `error 10`/errorNumber 0, 토스트 경고 3줄은 무시), epoch 기반 빠른 경로, 크래시 뒤 재설치.
- **`app.Session` / `HeadlessSession`**, `orcus-cli run | smoke`(3a 범위).

**작업 순서(3a 안)** — 단계마다 유닛 테스트 → `py tools/dev.py check` → 필요하면 기기 확인. 진행은 §4에 적는다.
1. **engine 기반:** `seams.py`(ProcessHost·JobControl·DiscoverySource·AdbRunner·PortProber·Clock·ChannelFactory), `adb.py`(private adb 서버·재등록·DeviceShell), `provision.py`(준비 단계·`session_init`·기능 탐지, `tools/spikes/provision_draft.py` 승격), `snapshots.py`(유효성 키·`provision.json`), `gpu.py`(렌더러 판정), 조기 종료 분류.
2. **EngineManager**(`engine/manager.py`): supervisor·worker·proc-wait·keepalive 스레드, §5.1 표 전체. FakeEmulatorServer와 가짜 seam으로 유닛 테스트(§12.2).
3. **device:** DeviceSession, 프레임, 입력(§9.2 표·떼기 보장·붙여넣기), 컨트롤 FIFO(3a는 최소), logcat(Text 모드 파서·백로그 버리기), 스크린샷.
4. **apk:** 판정·스테이징, 매니페스트(방향 숫자 표), 사전 점검, 설치, 실행, 감시(S5 문자열), 시기 판정, ApkService(빠른 경로·epoch 처리).
5. **app:** Session / HeadlessSession 조립, `orcus-cli run | smoke | engine status|shot`.
6. **기기 통합 시험:** 골든 부팅 → 픽스처 설치·실행 → OrcusProbe 오류 카드(§12.3의 3a 범위).
7. **gui:** 메인 창, 폰 화면(프레임 펌프·9:16·회전), 입력 매퍼, 베젤 키, 툴바, 드롭, [앱]·[로그] 패널, 배너·토스트, 보고서 복사, 단일 인스턴스, 로그오프 빠른 정지, GPU 모드 대화상자.
8. **수용 시험** B1·B2·B4·B7·B8·B9·B11·B12·B15 + 패키징 스모크.
- **`gui`:**
  - 메인 창, 9:16 폰 화면(1:1, 화면별 크기), 프레임 펌프, 정규 좌표 입력, 베젤 키, 툴바.
  - 드롭(I/O 없는 사전 검사)과 파일 선택.
  - **[앱]·[로그] 패널**(앱 카드, 문제 카드, 원본 로그), 오버레이·토스트·배너, 보고서 복사.
  - 단일 인스턴스와 파일 전달, 로그오프 시 빠른 정지.

**수용 기준**
- **B1.** 엔진이 켜진 상태에서 드롭 → 상호작용 ≤ 10 s(p95, 5회). 앱 시작 직후 드롭은 ≤ 20 s. 앱 카드는 ≤ 1 s.
- **B2.** PaintPot 선이 끊기지 않는다. BallBounce 플링 속도가 마우스 속도를 따른다. OrcusProbe 300 px 드래그에 Dragged가 20회 이상. 프레임 나이와 마우스→화면 지연이 S2에서 정한 예산 안이다.
- **B4.** Back을 누르면 BackPressed가 일어나고, Home·Recents가 동작한다.
- **B7.** 드래그 중 GUI를 죽였다가 다시 켜도 손가락이 눌린 채로 남지 않고, SDK 프로세스도 남지 않는다.
- **B8.** 레거시(있을 때)·minSdk 36 APK가 설치된다. ARM 전용·AAB·AIA·AIX·손상 파일에는 각각 맞는 안내가 나온다. Drive 폴더의 한글 이름 APK가 동작한다. 초기화 뒤 같은 파일을 다시 드롭하면 다시 설치된다. 가로 고정 launcher 앱은 실행 전에 기기가 가로로 돌아 있어 바르게 보인다.
- **B9.** 정지 화면에서 GUI CPU < 1%. 애니메이션 중 30 fps 이상. 작업 집합 ≤ 250 MB. 전체(Orcus + SDK) 유휴 RAM을 기록하고 S4 목표와 비교한다.
- **B11.** 엔진 동작:
  - 다른 VM 소유자가 있으면 `ENGINE_IN_USE`가 나오고, 첫 VM은 살아 있다.
  - 초기화하면 앱이 사라지고 epoch가 **정확히 1** 오른다(원인 RESET, 재설치 없음).
  - 크래시 뒤 자동 재시작하면 마지막 앱을 다시 설치하고, 골든 모드에서는 데이터 초기화 안내를 띄운다(원인 CRASH_RESTART).
  - GPU 모드 대화상자로 `software`를 고르면 재구축을 확인받은 뒤 COLD로 READY가 되고, "느린 그래픽 모드" 배너와 칩이 뜬다.
- **B12.** 오류 카드가 각각 맞게 뜬다: 크래시(`am crash`), ANR, 블록 실행 오류, 컴포넌트 오류(601, 1501, 3806, 그리고 908에는 권한 허용 버튼). 2701, 음성 인식 없음(UseLegacy 켬·끔 세 경로), TTS 없음은 해당 패키지를 끈 상태(`pm disable-user`)에서 확인한다. 기대 문자열은 docs/spikes.md S5에 있다.
- **B15.** 테스트와 앱 동작:
  - 유닛·레이어·pyright, 3a 범위 기기 테스트(ARCHITECTURE.md §12.3의 1–4, 6–15), 3a 스모크가 모두 통과한다.
  - `.apk`를 인자로 두 번째 실행을 하면 3 s 안에 첫 창에서 열린다.
  - 로그오프하면 SDK 프로세스가 남지 않고 스냅샷도 저장되지 않는다.

### Phase 3b — 센서·한글·설정 (3a 승인 후, D17)

**산출물**
- **[센서] 패널:**
  - 흔들기(감도별, 패키지마다 기억, 중력 방향 펄스 — S7에서 네 자세 모두 확인)
  - 회전과 [화면 방향 맞추기] + 방향 불일치 배너(표시 회전은 `dumpsys window displays`). 회전 버튼은 15°씩 0.6 s 이상에 걸쳐 돌리는 방식을 시험한다(S7: 한 번에 돌리면 Shaking이 잡힘)
  - 최소형 기울기 패드(20° 제한, 회전 벡터의 x·z축 — y는 가속도를 바꾸지 않음)
  - 위치(프리셋·힌트) — **프레임워크 테스트 제공자로 넣는다**(S7: gRPC `setGps`는 App Inventor 앱에 닿지 않음)
  - 마이크(앱별 동의, 녹음 장치가 없는 PC에서는 끄고 안내)
- **휠:** 게스트에 휠 장치가 없다(S7) → Canvas 밖에서만 짧은 세로 드래그로 흉내
- **한글 입력 바**(Paste 키 확정, S8)
- **최소형 설정 대화상자**(`ApplyResult`로 재시작·재구축 확인)
- **닫을 때 데이터 안내**(골든 모드)
- **3b 스모크**(흔들기·회전·위치 추가)

**수용 기준**
- **B3.** TalkToMe가 한국어로 말한다(S1 긍정: 내장 한국어 음성). 흔들기: 맞는 감도에서 클릭 1회 = Shaking 1회, 10회 중 9회 이상(역방향 자세 포함).
- **B5.** 4방향 회전에서 탭 오차 ±4 패널 px 이내. 창과 화면이 회전을 따른다. 가로 고정 Screen2는 [화면 방향 맞추기] 뒤 바르게 보인다.
- **B6.** "안녕하세요 Orcus"가 AI2 TextBox·MultiLine에 정확히 들어간다(NumbersOnly에서는 걸러짐).
- **B13.** 위치: 프리셋을 고르면 OrcusProbe가 10 s 안에 갱신된다(정확한 위치 허용 상태. Phase 2 실측 7.6 s 이내, 3b에서 정밀 측정 뒤 목표를 줄임). 위치 힌트가 보인다. 마이크 동의를 기억한다. 기울기 패드로 OrientationSensor 값이 바뀐다.
- **B14.** 설정 변경이 반영된다(GPU 모드 변경은 재구축 확인 뒤). 골든 모드에서 앱을 실행한 뒤 창을 닫으면 데이터 안내가 뜬다. COLD 모드에서는 뜨지 않는다.
- **B10.** 유닛·기기·스모크 테스트가 모두 통과한다. 수동 점검도 통과한다: 배율 100/125/150%, 다른 모니터로 창 이동, Mirage 켬, V3 켬.

### Phase 4 — 마감·확장·배포

- **세션 간 휴대폰 유지(keep-state, A/B 슬롯)** + [이번에는 휴대폰 유지하고 닫기]
  - 수용: 저장·복원 왕복이 된다. 저장 중 강제 종료해도 이전 슬롯이 남는다. 로드에 실패하면 골든으로 시작하고 슬롯은 보존된다.
- **GPU 자동 사다리**(재확인 부팅, 원격 세션 예외)
  - 수용: 가짜 시나리오 테스트 + Mirage를 켠 상태에서 수동 확인.
- **GUI 설치 마법사·라이선스 대화상자**(CLI와 같은 단계 모델)
  - 수용: 다른 Windows 계정에서 빈 폴더로 시작해 마법사를 끝까지 진행할 수 있다.
- **확장 기능:**
  - 폰 화면에 직접 한글 입력(S8 통과 시), 두 손가락 핀치
  - 조도·근접·배터리, 문자·전화 수신
  - 사진·소리 파일 드롭, 카메라에 이미지 보여주기(`-camera-back imagefile` 확인은 Phase 2에서 못 해 착수 전에 함) 또는 웹캠, 진동 표시(`dumpsys vibrator_manager`, S5 확인)
  - 설치된 앱 목록, 영어 UI, 진단 묶음
  - 업그레이드 관문(첫 엔진 업그레이드 전에), 개발용 detached 모드(선택)
- **배포:**
  - PyInstaller onedir(불필요한 Qt 파일 제외, Qt DLL은 별도 파일), 창 EXE + 콘솔 EXE.
  - Inno Setup 사용자별 설치(기본 `D:\OrcusAPK\app`, 관리자 권한 불필요).
  - `.apk` '연결 프로그램' 등록, 제거 시 `D:\OrcusAPK` 보존 여부 질문, NOTICE, 서명(★D3에 따름).
  - 수용: 새 Windows 계정에서 설치 → 첫 앱 실행. 패키징본에서 스모크가 통과하고, `--selftest`의 버전이 lock과 같다.

### 이후 (각각 별도 승인)

- **Companion 실시간 테스트:** `MITAI2Companion.apk` + `aicompanion://comp/<코드>` 딥링크, 또는 aiStarter 호환 127.0.0.1:8004 shim.
- **실기기 백엔드:** scrcpy 5.0.1. `device`의 `DeviceSession` 구현만 추가한다(센서가 없으므로 `controls=None`).
- **그 밖에:** MMAP 프레임 전송, 차세대 엔진 어댑터, 수업용 기능(보고서 내보내기 등).

---

## 4. 진행 기록

- **2026-10-29 — Phase 1 제안 v1**
  - 6개 주제(엔진, gRPC, App Inventor, 호스트 스택, 대안, scrcpy)를 병렬로 조사하고, 비평가가 교차 검증했다.
  - 경량·견고·학습자 UX 관점의 설계안 3개를 따로 만들었다.
  - 심사위원 3명(시스템·제품·기술 리드)이 채점했다(C > A > B 두 표, A > C > B 한 표).
  - A안을 뼈대로 종합하고, 심사위원들의 필수 수정 사항을 반영했다.
- **2026-10-29 — Phase 1 제안 v2**
  - 독립 검토자 4명(Windows·에뮬레이터, App Inventor UX, 유지보수·범위, 일관성·완전성)과 검증자 4명이 적대적 검토를 했다.
  - 66건이 나왔다(차단 1, 주요 19, 경미 41, 사소 5). 반박된 것은 0건이다.
  - 주요 변경: WHPX 판정 교체(차단 요인), 엔진 소유권 잠금, COLD 모드, keep-state를 Phase 4로, epoch, 로더 위생, App Inventor 동작(위치 주기·오류 두 경로·TTS·방향), Phase 2 범위를 요청서 정의로, Phase 3 분할, ★D0 추가.
- **2026-10-29 — Phase 1 제안 v3**
  - 수정 점검자 3명(반영 범위, 회귀, 새 주장 사실 확인)이 52건을 더 찾았다. 반영 범위: 66건 중 53건 완전, 13건 부분, 0건 누락.
  - 주요 변경:
    - **상태 기계:** epoch를 한 번만 올리고 원인을 붙임, nonce가 골든에 저장되던 문제, STOPPING 의도별 출구, FAILED 정리.
    - **에뮬레이터 소스에서 확인한 사실:** 강제 로드에 실패하면 골든을 지움, gRPC 저장은 같은 이름을 거부함.
    - **adb:** 재등록 메시지와 스캔 끄기(이 PC에서 스캔은 약 86 s), 공유 adb 키.
    - **설치:** UAC 거절(1223)을 실행 실패로 처리, 디스크 기준 24/6 GiB.
    - **이 PC 실측:** 모니터 3대, 주 모니터 1920×1080. 화면 크기는 창이 놓인 화면 기준.
    - **센서:** 흔들기 부호, 기울기 제한.
    - **Phase 2·3 구성:** S0을 S0a/S0b로 분할, Probe를 S3·S5 전으로, 3a/3b 패널 정리와 수용 기준 B15.
  - 최종 점검(2명): 52건 중 44건이 완전히, 8건이 부분적으로 반영되어 있었다. 회귀 22건도 찾았다. 그중 주요 1건은 잠금 없이 FAILED에 들어가면 다른 소유자의 VM까지 정리하던 문제다. 모두 반영했다.
  - 산출물: 이 문서, `docs/ARCHITECTURE.md`(v3), `docs/research/phase1-research-brief.md`(정정 17건).
  - 다음: 사용자 승인(★D0–★D4) → Phase 2.
- **2026-10-30 — Phase 1 승인, Phase 2 시작**
  - 사용자가 ★D0–★D4를 모두 권장값으로 승인했다. D5–D17도 권장값으로 진행한다.
  - ★D4에 따라 이 폴더를 기존 `D:\Project_Lab` 저장소에서 추적한다. 커밋은 사용자가 요청할 때만 한다.
- **2026-10-30 — Phase 2 1차: 골격·설치 CLI**
  - 개발 환경: `D:\OrcusDev\venv`(CPython 3.14.6). 해시 고정 `requirements.lock`(런타임 6개) + `requirements-dev.lock`(개발 16개). `tools/bootstrap.py`·`dev.py`·`lock.py`.
  - 만든 것: `orcus.contracts`(§3 전체), `orcus.common`(경로 가드, 설정 층, pins, AVD 파일, 자식 환경, WHPX 판정, 실행기, Job·잠금·프로세스 점검, 포트, 한국어 메시지 표, 로그), `orcus.setup`(인덱스, 라이선스 기록, 이어받기 다운로드, 안전한 압축 해제, UAC dism, 단계 모델), `orcus-cli setup | doctor`, engine 순수 부품(명령줄 빌더·불변식, discovery 파서).
  - 검증: 유닛 105개 통과(18.6 s), 네트워크 테스트(인덱스 대 고정값) 통과, pyright 0건. `orcus-cli setup`은 PC 점검을 마치고 라이선스 단계에서 "콘솔에서 사람이 직접 입력"을 요구하며 멈춘다(에이전트 실행 환경은 콘솔이 아니므로 동의 불가 — ★D2 확인).
  - 설계 대비 변경:
    - 설정의 내장 기본값은 `defaults.toml` 대신 contracts의 dataclass 기본값 하나로 둔다(같은 값을 두 곳에 두지 않기 위해).
    - androguard는 Phase 3a로 미룬다. Phase 2 코드가 쓰지 않고 의존성이 36개다.
    - 에뮬레이터 stable이 2026-10-29에 37.2.13(build 16740729)으로 바뀌어 37.2.12는 인덱스에서 빠졌다. 설계(§11.6 4단계)대로 고정 URL·SHA-1로 받는다. 37.2.13 전환은 스모크 관문을 거치는 업그레이드로 나중에 검토한다.
    - WHPX 되돌림을 3단계에서도 감지한다: 이전 부팅에서 dism이 성공했는데 InstallState가 다시 2면 `SETUP_WHPX_REVERTED`를 한 번 안내하고(IT 요청문), 다음 실행에서 UAC를 다시 시도한다(무한 재부팅 방지).
    - 관리자 여부는 `IsUserAnAdmin` 대신 토큰 elevation type으로 본다(이 PC: limited = UAC 동의만 필요).
    - venv의 `python.exe`는 실제 인터프리터를 자식으로 띄우는 런처다. 개발 중 프로세스 점검·잠금 시험은 이 점을 고려한다(패키징본에는 해당 없음).
  - 다음: 사용자가 자기 터미널에서 `orcus-cli setup` 실행(라이선스 동의 ★D2, UAC ★D1, 재부팅) → S0a 나머지·S0b 기록 → `orcus.emugrpc`(proto 헤더 확인·vendoring·스텁·FakeEmulatorServer) → 스파이크 하네스와 S6·S9 이후.
- **2026-10-30 — 설치 첫 실행: 압축 해제 실패와 수정 (S0a·S0b)**
  - 사용자가 직접 라이선스에 동의하고 UAC를 승인했다. dism은 25 s 만에 3010(재부팅 필요)을 돌려주었다. **권한을 올린 dism도 호출자의 Job에 들어갔다** → [P4] 마법사에 BREAKAWAY 도우미나 닫기 막기가 필요하다(§1.2).
  - 2.36 GB를 내려받은 뒤 압축 해제에서 실패했다: 임시 폴더에 다 푼 에뮬레이터 폴더를 `sdk\emulator`로 옮기는 이름 바꾸기가 "액세스 거부"(WinError 5). 사라진 파일은 없었다.
  - 원인: 이 PC는 V3와 함께 **Windows Defender 실시간 검사도 켜져 있다**. 검사기가 새 실행 파일을 잠깐 열어 두는 동안에는 폴더 이름 바꾸기가 실패한다(일시적. 몇 분 뒤에는 성공). 기존 안내("파일이 사라짐, 격리")는 틀린 분류였다.
  - 수정(§11.6 5단계): 최종 위치에 바로 풀고 `.partial` 표시로 미완료를 구분한다. 삭제는 잠금이 풀릴 때까지 최대 2분 재시도한다. 안내를 "격리"와 "검사 중 잠김"으로 나누고, IT 요청문에 Defender를 함께 적는다. 압축 해제 진행률은 전체 기준·8 MB 단위로 보인다. 유닛 110개, pyright 0건.
  - 다시 실행(라이선스·UAC는 이미 끝나 사람이 결정할 단계 없음): 5,707 MB를 32 s에 풀었고 재부팅 관문에서 RunOnce를 등록했다.
  - 새로 안 것: `emulator-check accel`은 **재부팅 전에도** 'WHPX usable'을 보고한다. 가속을 실제로 증명하는 것은 에뮬레이터 부팅(boot-check·S4)이다. setup은 재부팅 관문이 가속 확인보다 앞이라 영향이 없다.
  - 다음: 사용자 재부팅 → RunOnce가 7–8단계(가속 확인, AVD·공유 adb 키) 실행 → `orcus.emugrpc` → 스파이크.
- **2026-10-30 오후 — 설치 완료, gRPC 모듈, 스파이크 S0b·S6·S9·S2·S1·S4 (상세는 `docs/spikes.md`)**
  - 재부팅 뒤 RunOnce가 설치 7–8단계를 마쳤다(13:06). `orcus.emugrpc`: proto 3개(Apache-2.0 확인) vendoring, 스텁(gencode protobuf 7.35.1), 토큰 채널, FakeEmulatorServer. `engine/bootcheck.py`(`doctor --boot-check`), `engine/selftest.py`(`orcus-cli selftest`, `OrcusAPK.exe --selftest`). 스파이크 하네스 `tools/spikes/`.
  - **S0b:** WHPX로 실제 부팅 성공(첫 부팅 103 s, 이후 콜드 27–31 s). `host`·`host_hidden` 모두 통과.
  - **S2:** 게스트가 창 없이도 호스트 GPU(GTX 1660)로 그린다 → 기본 GPU 모드 `host`. 432×768 RGB888 약 50 fps에 우리 CPU 0.23 코어, 프레임 나이 6 ms, 드래그→프레임 46 ms, 행 순서 위→아래.
  - **S9:** private adb 서버 설계가 그대로 맞았다(Job 소속, 자동 등록, 공유 키, 몰래 서버 안 띄움, 재등록 0.06 s, 5037과 격리).
  - **S4:** 골든 부팅 4.8–5.8 s(목표 10 s), 실행 중 초기화 2.9 s + adb 0.6 s, nonce 검증 동작.
  - **설계가 실측과 달라 고친 것:**
    - 프레임 크기는 `Image.format.width/height`에 있다(`Image.width/height`는 항상 0). 원래 설계("0×0 프레임은 버린다")대로 만들면 화면이 나오지 않는다. 이 판정 때문에 측정 스크립트가 12분 멈췄고, py-spy로 원인을 찾았다.
    - WHPX 확인은 에뮬레이터 로그 줄로 한다(`getStatus.vmConfig.hypervisorType`은 항상 UNKNOWN).
    - RAM 기본값 2560 MB(에뮬레이터가 이 이미지를 2560 아래로 띄우지 않음).
    - gRPC `SaveSnapshot`은 같은 이름을 거부하지 않고 덮어쓴다.
    - 망가진 골든은 종료·삭제 없이 멈출 수 있다 → GOLDEN BOOTING 시간 초과도 골든 로드 실패로 보고 재구축.
    - 골든을 로드하면 게스트 시계가 저장 시각으로 돌아간다 → `session_init`에 시계 맞추기 추가.
    - 로캘 변경 직후 프레임워크가 재시작되므로 준비 명령은 서비스가 돌아온 뒤에 실행한다.
    - adb 서버 환경에 `ADB_MDNS_AUTO_CONNECT=0`을 추가했다. 15037을 차지한 낯선 adb는 채택하지 않는다.
    - 정지 직후 남는 `emulator -kill … -sleep 20` 감시 프로세스는 바로 정리한다.
    - discovery 위치는 `%LOCALAPPDATA%\Temp\avd\running` 고정이고, 파일 이름의 PID는 qemu다.
  - **A5 사전 확인:** 하네스 프로세스를 강제 종료하자 0.55 s 안에 SDK 프로세스가 0개가 됐다(KILL_ON_JOB_CLOSE).
  - **S5·S8·S7 일부(사용자 픽스처 없이):** App Inventor Companion APK로 설치 5–7 s·실행 1.5 s(드롭→실행 10 s 목표 안), 크래시 로그 문자열 확보, gRPC logcat은 Text 모드만 동작. 한글은 Paste 키로 정확히 들어가고 Ctrl+V는 실패(`ㅍ` 입력) → Paste 키 확정, ASCII도 붙여넣기로. 골든 로드 직후 Bluetooth 프로세스 크래시(3a 준비 단계에서 BT 끄기 시험). 3버튼 내비 강제(D7 변경), 베젤 키 3개 동작, 회전 시 프레임이 LANDSCAPE로 옴.
  - **패키징 스모크(A8) 통과:** 창 EXE·콘솔 EXE의 `--selftest`, 창 EXE의 `--boot-check`(host·host_hidden). 첫 빌드에서 selftest가 PySide6 누락을 잡아냈다. 앱 폴더 119 MB(Qt 정리는 Phase 4).
  - **S4 골든 멈춤과 해결:** 준비 세션에서 저장한 골든은 로드 약 21 s 뒤 게스트 전체가 멈췄다(adb·화면·logcat. 3회 재현, 처음 S4는 세션을 20 s 안에 끝내 못 봤다). 변형 4개(저장 방식·GPU 모드)와 "저장 4분 뒤 로드" 실험으로 원인을 "로캘 변경으로 프레임워크가 재시작된 준비 세션에서 저장한 것"으로 좁혔다. 준비 부팅에서는 설정만 적용하고 끈 뒤, 깨끗한 콜드 부팅에서 골든을 저장하자 로드 뒤 90 s 동안 멀쩡하고 시계도 맞았다(−1.2 s). §6.5에 7a단계로 반영, 1회성 준비는 약 90 s.
- **2026-10-30 저녁 — 픽스처(HelloPurr·TalkToMe·OrcusProbe) 생성기, APK 빌드는 승인 대기**
  - 웹 IDE는 Google 로그인이 필요해 에이전트가 쓰지 않는다. 대신 `tools/ai2/`에 프로젝트 생성기를 만들었다: 블록 DSL(`blocks.py`) 하나로 .bky와 .yail을 함께 만들고, `aia.py`가 .scm·project.properties·.aia·빌드용 zip을 만든다. 형식은 appinventor-sources `0712c114`의 YAIL 생성기와 `YaVersion`(239/40)·컴포넌트 버전·이벤트 인자 이름·속성 타입을 원본 Java에서 확인해 맞췄다. 미디어는 직접 그리고 합성했다.
  - 정적 검사(괄호 균형, XML·JSON 파싱, 컴포넌트·전역 변수 참조, 명세 이름, 라벨/버튼 문구 충돌)와 단위 테스트 20개 추가 → 전체 140개 통과, pyright 0건. `tests/fixtures/OrcusProbe.aia` 생성.
  - 명세에서 바꾼 것(docs/fixtures.md 반영): Screen1 화면 방향은 고정하지 않음(S7·B5의 자동 회전 확인용), `lblLangs`에 "langs " 접두어, 버튼 문구가 라벨 첫 단어와 겹치지 않게 함, 블록 오류는 `evaluate but ignore`로 실행.
  - **APK 빌드는 아직 못 했다.** 빌드 서버를 Ant로 빌드하는 명령이 "외부에서 받은 코드 실행"으로 자동 차단됐다(appinventor-sources의 build.xml과 Java 코드가 이 PC에서 실행된다). 사용자가 허용하거나 직접 실행하거나, 웹 IDE에 .aia를 가져와 빌드하는 셋 중 하나를 정해야 한다. 그 뒤 에뮬레이터 설치·실행 확인 → S1·S3·S5·S7·S8 나머지.
- **2026-10-30 저녁 2 — 픽스처 빌드, A4, S1·S3·S5·S7·S8 (상세는 `docs/spikes.md`, 스크립트 `tools/spikes/fx_*.py`)**
  - 사용자가 생성한 .aia 3개를 웹 IDE에서 빌드했다(가져오기·빌드 모두 성공). minSdk 14, targetSdk 36.
  - **A4 통과:** HelloPurr 설치 1.8 s → 실행 1.0 s → Screen1 resumed.
  - **S1:** Google TTS에 내장된 한국어 음성으로 바로 말한다(내려받기·로그인 없음) → 준비 단계의 음성 단계 삭제.
  - **S5:** 1501·601·908·2701·3806·RuntimeErrorAlert·ANR·음성 인식 없음의 실제 문자열 확보. 설계와 다른 것: ANR은 `WindowManager: ANR in Window{…}`, 인식 서비스 없음은 `error 10` + errorNumber 0, 마이크 꺼진 인식은 3806. 진동은 `dumpsys vibrator_manager`로 보인다.
  - **S8:** Paste 키로 TextBox·MultiLine·Password·WebViewer에 한글이 그대로 들어간다(NumbersOnly는 숫자만). ASCII 키 이벤트는 자모가 된다("abc"→"뮻").
  - **S7:** §9.2 좌표 표를 네 방향 모두 오차 0–1 px로 확정(B5). 흔들기 상수(25/16/9) 확인, 네 자세에서 감지. 가로에서 26° 기울이면 프레임이 뒤집힌다. 휠은 게스트에 장치가 없다. 이동은 약 60 Hz로 묶인다. 손을 떼지 않으면 손가락이 남는다.
  - **S3(기술 절반):** 게스트 소리 출력과 녹음 흐름은 정상이다. 이 PC에는 녹음 장치가 없다. 스피커로 들리는지는 사람 확인이 남았다(`tools/spikes/fx_s3_listen.py`).
  - **설계가 실측과 달라 고친 것:**
    - Gboard가 가상 터치 장치를 스타일러스로 보고, 세션마다 첫 텍스트 상자 탭에서 전체 화면 "스타일러스 사용해 보기" 시트를 띄웠다(입력이 막힘) → `stylus_handwriting_enabled 0`을 준비에 추가.
    - **위치:** App Inventor LocationSensor는 fused 제공자에 BALANCED로 요청해 gRPC `setGps`가 닿지 않는다 → 프레임워크 테스트 제공자로 넣는다.
    - Bluetooth 끄기(로드 뒤 크래시 0건)와 클립보드 접근 토스트 끄기를 준비에 추가.
    - 준비 세션의 마지막 설정이 사라졌다(SettingsProvider 지연 쓰기 + 전원 끄기) → 3 s 뒤 `sync`.
    - 회전 자체가 Shaking을 일으킨다 → 3b에서 천천히 돌리기 시험.
    - 매니페스트 방향은 숫자 값이다.
  - **골든 사고:** 종료 중 qemu 크래시 다음 준비 부팅이 조용히 죽었는데, 스크립트가 다음 세션에서 골든을 저장해 로드 뒤 adb가 붙지 않는 골든이 생겼다(뒤 시험 3개 실패) → `-wipe-data`로 처음부터 다시 준비했다(첫 부팅 92 s). 준비에 "실패하면 저장 안 함 + 검증 부팅 30 s"를 넣었고, 하네스에 gRPC 유지 호출을 넣었다(120 s 유휴 종료 방지).
  - **Phase 2에서 하지 못한 것:** S3 스피커 확인(사람), 카메라 `imagefile`(Phase 4 기능 판단용 → 착수 전), Qt 한/영 키(GUI가 있어야 함 → 3b), `ScreenOrientationChanged` 라벨.
  - 다음: A9 — 위 결과로 고친 Phase 3 계획(§3 Phase 3a·3b)을 사용자에게 승인받는다.
- **2026-10-30 밤 — S3 사람 확인, A9 승인, Phase 3a 시작**
  - S3: 사용자가 `fx_s3_listen.py`로 고양이 소리와 "안녕하세요"가 PC 스피커로 잘 들리는 것을 확인했다 → 소리 대체 경로(streamAudio + QtMultimedia)는 넣지 않는다.
  - **A9 승인:** 사용자가 고친 Phase 3 계획으로 Phase 3a 시작을 지시했다("이제 Phase3a를 시작"). Phase 2 완료.
