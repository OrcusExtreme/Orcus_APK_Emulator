# Orcus APK Emulator — 계획

> **상태: Phase 3b 작업 순서 1–6과 수용 시험 완료 — 사용자 수동 점검(B10)과 3b 승인 대기 (§4 끝).** Phase 3a는 2026-10-31에 승인되었다(사용자 커밋 v0.1.8, "Phase 3b까지 계속 진행"). Phase 2는 2026-10-30에 끝났다(A9 승인). Phase 1(v3)은 2026-10-30에 승인되었다(★D0–★D4 모두 권장값, D5–D17도 권장값으로 진행).
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
| 메모리 | VM에 2.5 GB 할당(이미지 최소값). **유휴 목표 약 3.3 GB**(Orcus + SDK 합계, 3a에서 고침. B9 실측 3,328 MB = 게스트 2,560 MB + qemu 자체 약 0.5 GB + 창 약 175 MB). GUI ≤ 250 MB | 최소 RAM 4 GB |
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
- **B9.** 정지 화면에서 GUI CPU < 1%. 애니메이션 중 30 fps 이상. 작업 집합 ≤ 250 MB. 전체(Orcus + SDK) 유휴 RAM을 기록하고 목표(약 3.3 GB, §1.7. 3a에서 S4 추정 2.8 GB를 고침)와 비교한다.
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
- **3a에서 미룬 것:** COLD에서 FRESH_PHONE을 FRESH_APP으로 바꿀 때 알림, 앱별 마이크 동의 재적용, 빠른 타이핑 글자 순서, 입력 스트림 재연결 뒤 첫 입력 손실, 옆 버튼 끌어서 취소, 종료 중 재실행 파일 손실, 파이프 상대 SID 확인.

**작업 순서(3b 안)** — 단계마다 유닛 테스트 → `py tools/dev.py check` → 필요하면 기기 확인. 진행은 §4에 적는다.
1. **device 센서:** `device/shake.py`(순수 파형 + App Inventor 감지기 이식 유닛 테스트), 회전 15°씩 0.1 s(감지기로 0회 확인, 실행 전 회전은 한 번에), 위치 3회 재전송, 자세·마이크 상태를 `DeviceControls`에서 읽기.
2. **device 입력:** 휠(기기 실험 뒤 결정), 붙여넣기 묶기·순서, 스트림 재연결 뒤 입력 보존.
3. **apk·app:** 방향 불일치 감지(실행 중) → 배너, 마이크 동의 적용(실행·크래시 재시작), COLD FRESH_APP 안내, 호스트 마이크 유무.
4. **gui:** [센서] 탭(흔들기+감도, 회전, 기울기 패드, 위치+힌트, 마이크), 옆 버튼(회전·흔들기·가), 단축키 F6·F7·F8·Ctrl+T, 한글 입력 바, 설정 대화상자, 닫을 때 안내, 옆 버튼 끌기 취소, 단일 인스턴스 두 건.
5. **스모크:** 흔들기·회전·위치.
6. **수용 시험** B3·B5·B6·B13·B14 + B10(수동 항목은 사용자).

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
- **Phase 3a 진행 (작업 순서 1–7)**
  - **1 engine 기반:** `engine/seams.py`(주입 지점, `Seams.real`), `adb.py`(private adb 서버·재등록·RawShell·EpochShell), `provision.py`(준비 11개·flush·session_init·위치 테스트 제공자·기능 탐지), `snapshots.py`(유효성 키·`provision.json`, `PROVISION_V=3`), `gpu.py`, `exitlog.py`.
  - **2 EngineManager**(`engine/manager.py`): supervisor·worker·procwait·keepalive. 작업이 띄운 VM은 실패·취소 때 그 작업이 정리하고, READY VM만 supervisor가 가진다. 진행 이벤트에 작업 번호를 붙여 오래된 이벤트를 버린다. 가짜 세계(`tests/unit/_engine_fakes.py`: 설정 유지·RAM 속성·골든 복원·빠른 시계)로 18개 시험(첫 준비·검증 부팅, 초기화 epoch +1, epoch에 묶인 셸, 정지·잠금, ENGINE_IN_USE, 미설치, 크래시 1회 재시작, 골든 불일치 재구축 1회, 초기화 실패 복구, 준비 실패·설정 유실 시 저장 안 함, 소프트웨어 GPU COLD).
  - **실기기**(`tests/device/test_engine_real.py`): 엔진의 `provision.json`이 없어 스스로 재구축 → READY **173.8 s**(원인 REBUILD), 초기화 **8.3 s**(epoch 2, RESET), 정지 5.0 s → 감시 프로세스 대기를 3 s에서 0.5 s로 줄임. 정지 뒤 SDK 프로세스 0개.
  - **3 device:** `rotation.py`(§9.2 표), `frames.py`(format 크기·회전 기록·재요청), `input.py`(떼기 보장·붙여넣기 순서·ASCII도 붙여넣기·휠 무시), `controls.py`(FIFO·흔들기·회전·기울기·마이크·위치 주입), `logcat.py`(Text 모드·백로그 버리기), `session.py`. FakeEmulatorServer가 Text 모드 logcat을 보내게 고침. 유닛 16개.
  - 유닛 184개, pyright 0건.
  - **4 apk:** `intake.py`(내용 판정·SHA 스테이징·LRU), `inspect.py`(방향 숫자 표, 픽스처 4개로 확인), `eras.py`, `compat.py`(사전 점검. 음성 인식 칩은 `<queries>`에 RecognitionService가 있는 앱만, targetSdk ≤ 22는 권한 검토 화면 안내), `install.py`(§7.5 표·재시도, 매니페스트를 못 읽으면 `pm list packages -3 -f` 전후 비교로 패키지 찾기), `launch.py`(실행 전 회전: "landscape"는 LANDSCAPE, "reverseLandscape"는 REVERSE_LANDSCAPE, sensor·user 변형은 지금 가로면 그대로. [화면 방향 맞추기]는 `mCurrentRotation`), `watcher.py`(S5 문자열, 우선순위, FATAL 스택은 끝날 때까지 모아 RECOGNIZE_SPEECH면 음성 인식 없음, 같은 오류 3 s 묶기, 908은 [이 앱 권한 모두 허용]), `service.py`(ApkService).
    - **ApkService 결정:** 스테이징을 판정보다 먼저 한다(Drive '온라인 전용' 파일은 읽는 순간 내려받으므로 시간 제한이 있는 복사에서만 읽음). logcat은 epoch마다 스트림 하나를 READY 때 열고, 실행 전에 첫 줄을 기다린다(게스트 시계가 늦으면 스트림 앞부분이 잘리므로). `am start`와 PID 사이의 줄은 링 버퍼에서 감시기에 다시 넣는다. 발견은 파이프라인 큐로 돌려보내 RunEvent는 모두 `apk-pipeline` 스레드에서 나간다. 새 epoch의 자세는 골든의 세로로 본다(3a에는 회전 버튼이 없음 → 3b에서 회전 버튼을 넣을 때 자세 공유 방법을 정한다).
    - **미룬 것(3b):** COLD에서 FRESH_PHONE을 FRESH_APP으로 바꿀 때 사용자 알림(지금은 로그만), 앱별 마이크 동의 재적용.
    - 유닛 95개 추가(`test_apk.py`, `test_apk_service.py` + `_apk_fakes.py`). 전체 279개, pyright 0건.
  - **5 app:** `app/session.py`(Session = SessionApi + HeadlessSession: EngineManager + `device.connect`(위치는 adb 테스트 제공자로 보내는 싱크) + ApkService, 설정 미리보기·저장, [보고서 복사], `open_paths`는 마지막 파일만, `shutdown`은 §4 순서), `engine/readonly.py`(다른 프로세스의 VM을 소유자 기록 + discovery로 읽기만), `app/smoke.py`(§12.5 3a: 시작→화면→Home 키→설치·실행→탭(HelloPurr 진동 기록으로 확인)→붙여넣기→초기화→정지, `logs\smoke-*.txt`), CLI `run | smoke | engine status|shot`.
    - **실기기 `orcus-cli run HelloPurr.apk`:** 골든 부팅 10.1 s → 설치 2.8 s → 드롭 후 18.4 s에 실행. 실행 단계에서 logcat 첫 줄을 3 s 기다린 것을 찾아, 스트림을 READY 직후(설치 전)에 열도록 고침.
    - **apkInspector 1.3.7이 import 때 `logging.basicConfig(level=DEBUG)`를 부른다** → 모든 라이브러리의 디버그 로그가 콘솔로 나옴. import 직후 루트 로거를 되돌림(`apk/inspect.py`).
    - **감시기 변경:** `Bind to system recognition service failed with error 10`은 `DeviceCaps`와 상관없이 음성 인식 없음(캐시된 기능 탐지는 골든 기준이라 실행 중에 꺼진 제공자를 모름). 그 뒤의 errorNumber 0은 같은 카드로 묶음.
    - 유닛 6개 추가(`test_app_session.py`: 가짜 엔진 세계에서 조립·위치 싱크·설정·보고서·읽기 전용 접속). 전체 285개, pyright 0건.
  - **6 기기 통합 시험**(`tests/device/test_apk_real.py`): ① 설치·같은 파일 빠른 경로·초기화 뒤 재설치(epoch +1, RESET), ② OrcusProbe 오류 카드(1501·601·908→[이 앱 권한 모두 허용]·3806·블록 실행 오류·ANR·`am crash`), ③ TTS·음성 인식 제공자를 끈 상태(legacy·error 10 모두 음성 인식 없음, 2701, TTS 없음) → [휴대폰 초기화]로 복구.
    - **발견 1 — gRPC logcat의 `contents`에는 줄바꿈이 없다**(메시지 하나 = 항목 하나). 줄바꿈으로 나누던 구현은 줄을 하나도 내보내지 않고 버퍼만 키웠다(6 s에 1.2 MB). 다음 타임스탬프에서 나누도록 고침(`common/logtext.py`).
    - **발견 2 — 스냅샷 로드([휴대폰 초기화]) 뒤 gRPC `streamLogcat`은 아무것도 보내지 않는다.** 게스트 `goldfish-logcat`(`logcat -f /dev/hvc1`)을 다시 시작해도 돌아오지 않았다(호스트 쪽 경로가 끊김). → logcat 원천을 **`adb logcat -v threadtime -T 1`**(engine이 epoch마다 자식 프로세스로)로 바꾸고 app이 DeviceSession에 주입한다. 실패하면 gRPC로 대체. ARCHITECTURE §1.2·§4·§7.8 갱신.
    - 결과: 드롭→실행(엔진 예열) 설치 7.3 s, 같은 파일 1.3 s, 초기화 뒤 재설치 3.4 s(B1 ≤ 10 s). ①·③ 통과.
    - 시험 도우미 수정: uiautomator가 내비 바 아래로 반쯤 숨은 버튼을 13 px 조각으로 보고해 빗나간 탭 → 50 px 이상 보이는 버튼만 누름. 스와이프 직후의 관성 스크롤로 위치가 바뀌지 않았는지 다시 덤프해 확인.
    - **발견 3 — 네이티브 크래시:** WebViewer가 있는 앱(OrcusProbe)은 렌더러가 죽으면 `chromium: … crash wasn't handled by all associated webviews, triggering application crash` → `F libc: Fatal signal 5 (SIGTRAP)`로 끝난다. `am crash`도 렌더러를 먼저 죽여 이 경로가 된다. → 앱 PID의 `Fatal signal`도 `APP_CRASH`.
    - **발견 4 — Java 크래시 뒤 App Inventor Form이 `Uncaught Exception` + `errorNumber = 909`를 남긴다**(HelloPurr). 크래시 카드가 실행을 끝내므로 909 카드는 없다(시험으로 고정).
    - **발견 5 — 3806(No Match)까지 걸리는 시간이 일정하지 않다:** S5 5.5 s, 단독 6 s, 908 직후 26 s.
    - **발견 6 — App Inventor Form은 `onGlobalLayout()`·`keyboard hidden!` D 줄을 초당 여러 번 남긴다** → [보고서 복사]의 앱 로그는 I 이상 50줄 + 경고·오류 30줄로 바꿈.
    - 최종: 기기 시험 3개 모두 통과(4분). 3806은 Google 인식기가 NO_SPEECH 없이 끝나는 실행이 있어, 앱이 3806을 남겼을 때만 카드를 확인한다(앞선 실행 2회에서 카드 확인).
  - **7 gui**(`gui/`: keymap·bridge·frame_pump·screen·phone·overlays·panels·gpu_dialog·window·instance·app, `__main__`): 9:16 크기 계산(이 PC 주 모니터 432×768), 1:1 그리기, 정규 좌표 입력과 떼기 보장, Esc·오른쪽 클릭=뒤로, 가운데 클릭=홈, Ctrl+V=호스트 클립보드 붙여넣기, 베젤 키(길게 누르기), 진행 오버레이·토스트·배너·엔진 실패 패널, [앱]·[로그] 패널, 코드→채널 표대로 문제 표시, `RemedyAction`마다 처리기, GPU 모드 대화상자, 단일 인스턴스(named mutex + 사용자 SID 이름의 QLocalServer), 로그오프 빠른 정지. EngineView 계약에 `retry()` 추가(엔진 실패 패널 [다시 시도] = 재구축 한도 초기화).
    - 실기기: 창을 띄워 READY 10.7 s → HelloPurr 드롭 5.7 s 뒤 실행 → 창의 폰 화면을 마우스로 탭 → HelloPurr 진동 기록 확인(GUI→입력→gRPC→게스트 전체 경로). Windows 다크 테마에서 베젤 아이콘이 안 보여 팔레트 글자색으로 그림.
    - `python -m orcus`: 두 번째 실행(.apk 인자)이 0.7 s에 넘겨주고 끝남 → 첫 창에서 실행(B15 3 s 기준). GUI 프로세스를 강제 종료하면 3 s 안에 SDK 프로세스 0개(B7 일부).
    - **3a 스모크**(`orcus-cli smoke --apk HelloPurr.apk`): 8단계 모두 PASS(시작 10.7 s, 설치·실행 2.9 s, 탭→진동, 초기화 7.1 s, 정지 3.5 s 뒤 SDK 프로세스 0개). 탭 대상은 앱의 가장 큰 Button(처음엔 액션 바 ⋮를 눌러 실패).
    - 유닛 295개, pyright 0건.
  - **8 준비:** §12.4의 "3a 수용 시험 전" 픽스처 6개(PaintPot, BallBounce, MoleMash, HelloPurrKo, HelloPurrMin36, HelloPurrLand)를 `tools/ai2/projects.py`에 추가하고(빼기·무작위 정수 블록, Ball·ImageSprite·Clock 추가) `.aia`를 `D:\OrcusDev\fixtures\src`에 만들었다. minSdk는 `project.properties`의 `androidminsdk`(appinventor-sources Form.AndroidMinSdk) — 가져오기에서 버려져 Screen1 속성으로도 넣게 고침(발견 12). 유닛 313개, pyright 0건.
  - **8 수용 시험**(2026-10-30–31. 사용자가 웹 IDE로 픽스처 6개를 빌드. 기기: `tests/device/test_acceptance_3a.py` + `test_apk_real.py` + `test_engine_real.py`, 창·패키징본은 스크립트로 측정)

    | 기준 | 결과 |
    |---|---|
    | B1 | 엔진 예열 뒤 드롭→실행(5종) 1.8–4.9 s, 앱 카드 0.03–0.08 s. 시작 직후 드롭 14.1–14.4 s(≤ 20 s). **전체 실행에서 BallBounce 1회 22.6 s로 실패**(발견 13). 같은 시험을 다시 돌려 통과(3.1–4.9 s) |
    | B2 | PaintPot 선 끊김 0열, 마우스→화면 88–100 ms, 프레임 나이 p95 7.7 ms, 드래그 중 44 fps. BallBounce 플링 91 → 284 px/s(마우스 1,000 → 3,000 px/s). Probe 300 px 드래그 Dragged 27회 |
    | B4 | Back → BackPressed, Home → 런처, Recents → 개요 화면 |
    | B7 | PaintPot에 손가락을 누른 채 창 프로세스를 강제 종료 → 3 s 뒤 SDK 프로세스 0개. 다시 켜서 탭하면 점만 찍힘(이전 손가락의 선 없음) |
    | B8 | ARM 전용·AAB·AIA·AIX·손상 파일에 각각 맞는 안내. 한글 경로(로컬 폴더) 1.6 s 실행, 라벨 "고양이 쓰다듬기". 초기화 뒤 재드롭은 재설치. 가로 고정 launcher는 실행 전에 LANDSCAPE, 표시 ROTATION_90, 1280×720. minSdk 36은 다시 빌드한 APK(2026-10-31, 발견 12)가 4.4 s에 실행, 설치된 패키지 `minSdk=36 targetSdk=36`(기기 API 36). 레거시 APK는 구하지 못해 해당 없음. Drive 폴더(`G:\내 드라이브`, 스트리밍)의 한글 이름 APK는 복사 직후 드롭해 3.5 s에 실행(사용자 허락 2026-10-31, 시험 뒤 삭제, `ORCUS_DRIVE_DIR`를 줄 때만 도는 시험). 로컬 캐시에 없는 온라인 전용 파일은 시험하지 않음 |
    | B9 | 정지 화면 창 CPU 0.31%(한 코어 기준, 16코어 전체 0.02%). 애니메이션(BallBounce) 35–47 fps. 작업 집합 174–179 MB. 유휴 RAM 합계 3,328 MB(qemu 3,081, 창 174, adb 28, netsimd 22, emulator 12, crashpad 11) → 목표(VM 2.5 GB + GUI ≤ 250 MB ≈ 2.8 GB)보다 약 0.5 GB 많다. qemu 작업 집합이 게스트 RAM 2,560 MB보다 약 520 MB 크다. **다시 잼(2026-10-31, READY 60 s 뒤):** qemu 작업 집합 3,103 MB 중 비공유 3,048 MB, 공유(파일·DLL) 55 MB, 비공유 커밋 3,587 MB. 초과분은 qemu 자체의 비공유 메모리라 Windows가 거둘 수 있는 파일 페이지가 아니다. 게스트 RAM은 이미지 최소값(2,560 MB)이라 설정으로도 줄일 수 없다. S4 목표가 qemu 부가분을 빠뜨린 추정이었으므로 **목표를 약 3.3 GB로 고쳤다(2026-10-31 사용자 결정, §1.7)**. B9의 합격 기준(CPU·fps·작업 집합)은 모두 통과 |
    | B11 | `ENGINE_IN_USE`, 첫 VM 유지. 초기화 epoch +1(RESET, 재설치 없음). qemu 강제 종료 → 16.4–18.5 s에 CRASH_RESTART, 마지막 앱 재설치. 골든 모드 크래시 안내는 유닛 시험. GPU software → COLD READY 131–147 s + 배너·칩, host로 돌아와 골든 재구축 264–354 s |
    | B12 | `test_apk_real.py` 3개 통과 |
    | B15 | 유닛 320개·pyright 0건. 기기 시험 §12.3 1–4·6–15 통과(7은 minSdk 36·ARM 전용, 레거시는 해당 없음). 패키징본 3a 스모크 8단계 PASS. 패키징본 두 번째 실행이 0.7 s에 첫 창으로 넘김. 로그오프는 패키징본 창에 WM_QUERYENDSESSION/WM_ENDSESSION을 보내 흉내 → 2.83 s에 빠른 정지(Windows 대기 약 5 s 안), SDK 프로세스 0개, 스냅샷 파일 변화 없음. **실제 로그오프(2026-10-31 11:10, 원격 데스크톱, 새 정지 방식의 패키징본, 약 17분 사용 뒤):** 1.2 s에 정지, 다시 로그인했을 때 SDK 프로세스 0개, 충돌 보고서 0개, 스냅샷 본체 그대로, discovery·`netsim.ini` 남지 않음 |
    | 패키징 스모크 | 빌드 44–66 s, 앱 폴더 121.9 MB. selftest 2개, `--boot-check` host·host_hidden(WHPX), 창 READY 8.5–8.8 s → 강제 종료 뒤 SDK 프로세스 0개 |

    - **발견 7 — 게스트가 바쁠 때 저장한 골든은 로드 뒤 프레임워크를 잃는다.** 재구축 직후의 골든으로 시험하다 초기화에서 표식이 빈 값으로 보이고 `svc power stayon`이 멈췄다. 로드 뒤 system_server가 Watchdog으로 죽어 `package`는 Broken pipe, `power`는 DEAD_OBJECT였다. 저장 때 load average가 약 12(첫 부팅의 앱 컴파일)였다. → 저장 전에 CPU 사용률 대기(0.35 미만 2회 연속, 최대 180 s)와 프레임워크 확인, 검증 부팅은 로드 60 s 뒤 확인, 모든 GOLDEN SESSION_INIT에서 프레임워크 확인(망가졌으면 재구축). ARCHITECTURE §5.1·§6.5. 재구축 294 s(68 s에 조용해짐), 이후 재구축 264 s(58 s).
    - **발견 8 — READY 중에 private adb 서버가 죽으면 아무도 다시 띄우지 않았다.** → `engine-adbwait` 스레드 + 재시작 작업, 0.5–0.6 s에 `emulator-5640` 복귀(§4·§5.5).
    - **발견 9 — 입력 스트림이 끊길 때 눌린 손가락이 남을 수 있었다.** → 새 스트림이 먼저 손가락을 뗀다(§12.3 6 통과).
    - **발견 10 — 패키징:** 단일 인스턴스(`gui/instance.py`)가 쓰는 QtNetwork를 빌드가 빼고 있었다 → 포함하고 selftest에 `PySide6.QtNetwork`·`orcus.gui.app` import를 넣었다. `build.py --gui-check`는 SDK 프로세스를 이름으로 찾다가 Google Drive가 띄운 `crashpad_handler.exe`에 걸렸다 → 이미지 경로(`D:\OrcusAPK\sdk` 아래)로 찾는다.
    - **발견 11 — BallBounce:** 0.6 s 드래그는 플링으로 잡히지 않았다 → 시험은 같은 240 px를 0.25 s와 0.08 s에.
    - **발견 12 — App Inventor `.aia` 가져오기는 `project.properties`의 `androidminsdk`를 읽지 않는다**(`YoungAndroidSettingsBuilder`는 쓰기만 한다). 그래서 HelloPurrMin36이 minSdk 14로 빌드됐다. AndroidMinSdk는 Screen1 속성(웹 IDE [프로젝트 속성] > Publishing)이라 생성기가 Screen1 `.scm`에도 넣도록 고쳤다(nb207 릴리스 노트에 이 속성의 버그 수정이 있음). 사용자가 Project Properties 대화상자에서 36으로 바꿔 다시 빌드했고, minSdk 36과 B8을 확인했다(2026-10-31). `.scm`에 넣은 값이 가져오기에서 살아남는지는 시험하지 않았다. 웹 IDE 새 화면에서는 이 대화상자가 [프로젝트] 메뉴에 없고, 디자이너 도구 막대의 − 버튼 오른쪽 아이콘으로 연다. 오른쪽 속성 패널에도 나오지 않는다.
    - **발견 13 — B1에서 한 번 22.6 s:** BallBounce 설치(0.7 s) 뒤 실행까지 21.7 s가 걸렸고 그 사이 로그가 없었다. 같은 조건(골든 로드 직후 5개 연속)으로 45회와 B1 재실행 5회는 모두 1.8–6.0 s여서 재현되지 않았다. 원인을 모른다. `am start -W` 첫 시도가 실패하면 출력과 시간을 남기고(재시도가 이를 가렸을 수 있음), 10 s를 넘는 adb 호출과 실행 단계 시간도 로그에 남기게 했다. 다시 나오면 원인을 잡는다. → **2026-10-31 다시 나왔고 새 로그가 원인을 잡았다:** 리뷰 수정 뒤 기기 시험에서, 부팅 직후 첫 `adb install`이 게스트 안에서 12.3 s 걸렸다(실행까지 14.2 s, 이후 설치는 0.8–1.6 s). 다시 돌리니 1.4 s로 통과했다. 우리 코드가 아니라 골든 로드 직후 게스트 패키지 관리자의 일시 지연이다.
    - **수동 점검 전후 C: 비교(§11.5, 2026-10-31):** 사용자가 패키징본으로 자기 앱(ElementQuiz, 3.1 s)·OrcusProbe·HelloPurrLand를 쓰고 창을 두 번 닫았다. 오류 로그는 없었다. 비교 도구는 `D:\OrcusDev\footprint\footprint.py`(저장소 밖)로, 파일 크기·시각과 레지스트리 키 이름·값 이름만 기록한다.
    - **발견 14 — qemu가 스스로 끝나다 죽어 `C:\ProgramData`에 충돌 보고서가 남았다(§11.5 위반):** `WER\ReportArchive\AppCrash_qemu-system-x86_…`가 같은 서명으로 31개 있었다(10-30 13:32부터 29개, 10-31에 사용자 창 닫기 2번). 모두 `combase.dll` fail-fast(0xc0000602)였고, `setVmState(SHUTDOWN)` 뒤 qemu가 스스로 끝나는 중에 났다. Phase 2에도 한 번 기록이 있다(docs/spikes.md). 이때 정지가 6 s 걸렸다(정상 1.4–2.2 s). 스크립트로는 재현하지 못했다. 짧은 세션, 고양이 소리, 6분 유지 + 프레임 스트림이 모두 깨끗이 끝났고, 실제 사용에서만 났다. **고침:** 정지를 "일시정지(QEMU가 디스크를 비움) → qemu 종료 → 디스커버리 파일 삭제"로 바꿨다(engine, bootcheck, ARCHITECTURE §5.1 STOPPING). 정지는 0.7–0.9 s, 남는 프로세스·파일이 없고, 다음 시작의 골든 로드도 정상이었다(성공 로드 12→13→14). 고친 뒤 기기 시험(engine + 3a 수용, 골든 재구축 2번 포함) 16개 통과, 패키징 빌드·자체 시험·부팅 점검(host·host_hidden, 정지 0.14 s)·창 점검 통과, 그동안 충돌 보고서 0개. 창을 강제로 죽이면 discovery 파일과 `netsim.ini*`가 남지만, 다음 시작의 고아 정리(§5.7)와 netsimd가 처리한다. 실제 사용 뒤 창 닫기로 한 번 더 확인한다.
    - **발견 15 — Qt 파일 대화상자가 `HKCU\Software\QtProject\OrganizationDefaults\FileDialog`에 썼다(§11.5 위반):** QFileDialog는 네이티브 대화상자도 닫힐 때 마지막 폴더·Qt 버전을 저장한다. 새 `gui/dialogs.py`가 대화상자(APK 열기, 스크린샷·로그 저장)마다 그 키를 원래대로 되돌린다(유닛 4개).
    - **발견 16 — 골든 `snapshot.pb`는 로드할 때마다 바뀐다:** 에뮬레이터가 성공 로드 횟수(필드 11)를 올려 다시 쓴다. 스냅샷 본체(`ram.bin` 등)는 00:13 저장 그대로다. "스냅샷이 저장되지 않았다" 확인에서 뺐다(§12.6).
    - **발견 17 — 허용 목록에 둘을 더했다(§11.5):** netsimd의 `%LOCALAPPDATA%\Temp\netsim.ini*`(실행 중에만 있고 정지 때 지워짐)와 PowerShell 시작 캐시(`StartupProfileData-NonInteractive`, 사전 점검의 WHPX 확인 때 갱신). 나머지 변화(Recent·점프 목록·ComDlg32·Shell Bags·UserAssist·`.apk` OpenWithList, Edge·Zoom·OneDrive·Python 3.11 같은 다른 프로그램)는 §11.5가 빼는 OS 기록과 다른 앱이다.
    - **발견 18 — 원격 데스크톱 연결이 끊긴 동안 휴대폰을 켜면 에뮬레이터가 골든을 지운다(2026-10-31):**
      - **무슨 일:** 사용자 세션이 11:46:48에 끊긴 상태(`query user` 상태 "디스크")에서 시험을 시작했다. 11:51 골든 로드가 `Failed to load snapshot, because 'host GPU has changed'; Emulator will delete existing snapshot and exit` → `Deleting snapshot orcus_golden`으로 끝났고, engine이 재구축했다(약 4분).
      - **원인:** 골든의 `snapshot.pb` `host.gpu_driver`는 `10de-NVIDIA GeForce GTX 1660-`였다. 끊긴 세션에서는 `EnumDisplayDevices`가 0개라, 에뮬레이터의 GPU 목록이 비고 "Your GPU cannot be used for hardware rendering"도 찍혔다(렌더링은 그대로 GTX 1660). WMI는 GPU를 그대로 보여 준다.
      - **남은 영향:** 새로 만든 골든은 `gpu_driver`가 빈 값이다. 그래서 다시 연결한 뒤 처음 켤 때 같은 이유로 한 번 더 지워지고 재구축된다(약 5분). 연결이 끊긴 채 켤 때마다 이 일이 되풀이된다.
      - **고침(2026-10-31, 사용자 결정, ARCHITECTURE §5.1):**
        - (A) PREFLIGHT에서 화면 장치가 0개이고 골든이 GPU를 기록했으면 이번만 COLD로 켠다(배너 `ENGINE_NO_DISPLAY`, 칩 "이번만 느린 시작", 초기화는 재시작). 검증 부팅은 예외로 `PRE_NO_DISPLAY`. "host GPU has changed"로 지워진 경우의 재구축 문구는 "그래픽 장치가 바뀌어…"이다.
        - (B) 화면 장치가 0개이면 골든 준비를 시작하지 않고, 깨끗한 부팅 직전에도 확인한다(`PRE_NO_DISPLAY`).
        - 유닛 6개(엔진 4, 부품 2).
      - **실기기(2026-10-31, 원격 연결 상태):** 빈 GPU를 기록한 골든으로 켜자 에뮬레이터가 "host GPU has changed"로 지웠고, engine이 "그래픽 장치가 바뀌어…" 문구로 재구축했다(291 s 뒤 READY). 새 골든의 `gpu_driver`는 `10de-NVIDIA GeForce GTX 1660-`이다. 이것으로 사용자의 다음 시작에서 5분 재구축이 생기지 않는다.
    - **3a 코드 리뷰(2026-10-31, 사용자 요청 "추가 버그·취약점 분석과 수정"):** 4개 영역(설치, APK 파이프라인, 엔진, GUI·IPC)을 읽기 전용 리뷰어가 나눠 보고 41건을 냈다. 하나씩 코드로 확인한 뒤 고쳤다(유닛 348개, pyright 0건).
      - **고침, 보안:**
        - 저장된 discovery PID가 다른 프로세스로 재사용되면 고아 정리가 그 프로세스(예: 사용자 브라우저)를 죽일 수 있었다. 이제 우리 SDK 아래 qemu일 때만 끝낸다. 공유 폴더 `<home>\tmp`의 discovery도 읽지 않는다.
        - APK 압축 폭탄(매니페스트·assets·아이콘을 끝까지 풀었고, bzip2/LZMA는 제한할 수 없었다)은 상한 있는 읽기와 stored/deflated만 허용으로 막았다. 항목 수가 5만을 넘는 zip은 거부한다.
        - 앱 라벨·크래시 문구가 HTML로 그려졌다(SMB `<img>`, 클릭하면 열리는 `file://` 링크). 이제 모든 앱·엔진 문구는 일반 텍스트다.
        - `explorer.exe`·`netstat`을 이름으로 실행하던 것을 전체 경로로 바꿨다.
        - bootcheck PowerShell에 홈 경로를 끼워 넣던 것(`'` 주입)을 환경 변수로 넘기게 바꿨다.
        - 넘겨받은 경로는 존재하는 로컬 파일만 받고(UNC 거부, 64 KiB 상한), 설치 실패는 정확한 `Failure [CODE]`로만 판단한다.
        - "앱 교체?" 제거 대상은 우리가 읽은 패키지뿐이다. adb 출력에서 뽑은 이름은 쓰지 않는다.
        - 압축 해제 직전에 zip 해시를 다시 확인한다.
      - **고침, 버그:**
        - 망가진·ANSI(CP949) 상태 JSON 때문에 앱이 로그도 없이 죽었다. 이제 `.corrupt`로 치우고 기본값으로 시작하며, 쓸 때는 fsync한다.
        - 압축 해제가 중단된 SDK(`.partial`)를 설치된 것으로 봤다.
        - 라이선스 재시도가 동의 없이 통과했다.
        - 재부팅 판정을 시각 대신 BootId로 한다.
        - `setup.json` 저장 실패로 작업 스레드가 조용히 죽었다.
        - 다운로드 재시도가 진행이 있어도 줄었고, 416을 처리하지 않았다.
        - 홈 경로 검사가 로그 폴더를 만든 뒤에 돌았다.
        - 취소된 시작이 VM을 남겼다. owner 기록을 지우다 실패하면 상태 기계가 멈췄다.
        - 실패할 때 READY VM을 정지하지 않았다. 정지 뒤 정리가 SDK 아래 모든 프로세스(다른 adb 포함)를 죽였다.
        - owner 기록 쓰기가 실패하면 다시 쓰지 않았다.
        - 예외가 난 실행이 끝 단계 없이 남았다.
        - 드롭 뒤 누른 [다시 실행]이 새 드롭을 취소했다.
        - 빠른 종료(로그오프)가 파이프라인을 5 s 기다렸다.
        - 스테이징 임시 파일 이름이 겹쳤다. `staging_keep` 0이면 방금 넣은 파일이 지워졌다.
        - ABI 판정이 `lib/x/notes.txt`도 셌다. 두 번째 설치 시간 초과에 잘못된 오류가 나왔다.
        - 배너 버튼이 동작하지 않았다(`clicked(bool)`).
        - 로그오프가 취소되면 창 없는 프로세스가 남았다.
        - 배율이 다른 모니터로 옮기면 화면이 잘리고 탭이 어긋났다.
        - 저장 실패와 예외가 창 빌드에서 사라졌다.
        - 넘겨받은 파일 창을 앞으로 가져오지 않았다. 붙여넣기 크기에 상한이 없었다(64 KiB로 제한).
      - **미룸(근거):**
        - 엔진: 재시작마다 adb 포트가 번갈아 바뀐다(동작은 정상, 재시작 약 0.5 s). adb 복구 중 초기화하면 감시가 사라질 수 있다(드묾).
        - APK: 휴대폰 안에서 지운 앱을 같은 파일로 다시 실행하면 설치를 건너뛴다(드묾, [제거]나 초기화로 풀림). `android:process`가 다른 앱의 PID를 못 찾는다(App Inventor 앱은 해당 없음).
        - GUI: 같은 PC 다른 계정의 파이프 선점은 상대 SID 확인이 필요하다(경로 검증으로 피해는 줄임). 빠른 타이핑 때 글자 순서가 바뀔 수 있다. 입력 스트림이 다시 연결된 뒤 첫 입력을 잃는다. 종료 중 다시 실행하면 파일을 잃는다. 옆 버튼을 끌어서 취소해도 눌린다. 이 다섯은 3b 입력·한글 작업과 함께 본다.
      - **폴더 권한: 사용자 결정(2026-10-31) "1번 = 이 계정만 쓰게 잠금".**
        - 문제: `D:\OrcusAPK`·`D:\OrcusDev`가 D:의 "Authenticated Users: 수정"을 물려받아, 다른 계정이 SDK 실행 파일을 바꿀 수 있었다.
        - 설치(CHECK)는 이제 루트를 이 계정·SYSTEM·Administrators 전용으로 잠그고(`common/acl.py`, 계정마다 한 번), 실패하면 `SETUP_PERMISSIONS`로 멈춘다(유닛 2개).
        - 이 PC의 두 폴더도 잠갔다(icacls 확인: 세 주체만 남고 하위는 상속). 잠근 뒤 실기기 엔진 시험이 통과했다.
        - 되돌리기: `icacls <폴더> /reset /T`. ARCHITECTURE §5.7·§11.4에 반영.
    - **발견 19 — 재구축 중 문구가 "처음 준비 중 — 한 번만 합니다"로 바뀌었다:** 위 실기기 재구축 90 s쯤 설정 단계의 진행 갱신이 문구 키 없이 와서 상태 기본 문구로 돌아갔다. 같은 상태 안의 진행 갱신은 지금 문구를 유지하게 고쳤다(유닛으로 확인).
    - **§5.5 최후 수단(`adb connect`) 필요성 검토(2026-10-31): 넣지 않는다.** 서버를 잃은 3번이 모두 0.65–0.72 s에 재등록으로 돌아왔고, 실패하면 지금도 FAILED(`ENGINE_ADB_LOST`)로 끝난다. serial이 바뀌는 파급이 커서 이득보다 위험이 크다. 로그에 재등록 실패가 나오면 다시 본다(ARCHITECTURE §5.5).
    - UI 안내 시간을 "약 3분"에서 "약 5분"으로 바꿨다(7a 대기와 60 s 검증 때문).
    - 참고: 다른 프로세스의 `orcus-cli engine status`(`engine/readonly.py`)는 gRPC `booted`를 "준비됨"으로 보여, 소유자가 SESSION_INIT 확인 중인 약 4 s 동안에도 준비됨이다. 소유자 상태를 소유자 기록에 남길지는 3b에서 정한다. → **3a에서 고침(2026-10-31, 사용자 결정):** engine이 상태가 바뀔 때마다 소유자 기록에 상태·문구 키·epoch·모드를 쓰고(깨우기 전에), `readonly.py`가 그것을 보여 준다(유닛 2개). 실기기: SESSION_INIT 동안 다른 프로세스의 CLI가 "휴대폰 마무리 준비 중…", 소유자 READY 0.1 s 뒤 "준비됨".
  - **사용자 확인이 필요한 것:**
    1. (끝남, 2026-10-31) HelloPurrMin36 다시 빌드 → minSdk 36 확인, B8 기기 시험 3개 통과.
    2. (끝남, 2026-10-31) Drive 폴더 한글 이름 APK 시험 → 사용자 허락 뒤 실행, 3.5 s에 실행, 시험 폴더 삭제.
    3. (끝남, 2026-10-31) §12.6 수동 점검: 사용자가 "정상 작동"으로 확인.
    4. (끝남, 2026-10-31) 새 패키징본으로 약 17분 쓴 뒤(OrcusProbe, 스크린샷 저장) 앱 실행 중 실제 로그오프. 위 B15 행 참고. 따로 창을 닫는 단계는 건너뛰었지만, 로그오프 정지도 같은 새 정지 경로다. C: 비교(10:44 → 11:14)에서 우리 것은 OS 기록(`Recent\orcus.png.lnk` 등)뿐이었고, Qt 키는 값이 그대로 되돌려졌다. 고치기 전 빌드가 남긴 `HKCU\Software\QtProject\OrganizationDefaults\FileDialog`(값: `lastVisited=file:///D:/OrcusDev/fixtures`, `qtVersion=6.12.0`)는 지우려 했으나 권한 분류기가 막아 사용자에게 맡겼다.
- **2026-10-31 — Phase 3a 승인, Phase 3b 시작:** 사용자가 3a를 v0.1.8로 커밋하고 "Phase 3b까지 계속 진행"을 지시했다. 3b 작업 순서는 §3에 있다.
- **Phase 3b 진행**
  - **1 device 센서:**
    - `device/shake.py`: 흔들기 펄스와 회전을 순수 파형으로 만든다. App Inventor `AccelerometerSensor.java`(10개 표본 캐시에 먼저 넣고 축마다 |평균 − 표본|, 대역 5/13/20, 400 ms)를 유닛 테스트에 옮겼다(`tests/unit/test_shake.py`).
      - 모델 확인: S7 실측과 같다. 한 번에 도는 90°는 Strong에서, 180°는 Moderate·Strong에서 울리고, 감도 교차 표(Moderate+Δ25 → 1, Moderate+Δ9 → 0 등)도 같다.
      - 맞는 감도의 펄스는 네 자세, 표본 위상, 펄스 40–100 ms 모두에서 정확히 1번 울린다.
      - **회전 15°씩 0.1 s:** 표본 편차가 3.4 m/s² 아래라 세 감도 모두 0번이다(90°·180° 8가지). [화면 방향 맞추기]와 회전 버튼은 이 방식이고, 실행 전 회전은 앱이 아직 듣지 않으므로 한 번에 돈다.
    - `DeviceControls`에 `posture`·`microphone`(가장 최근 요청값, epoch마다 세로·꺼짐에서 시작)을 넣었다. 3a에서 ApkService가 따로 기억하던 자세를 이것으로 바꾼다(회전 버튼과 실행 전 회전이 같은 값을 본다).
    - 위치는 1 s 간격으로 3번 보낸다(S7). 새 위치가 오면 그것으로 바꿔 다시 센다.
  - **휠 기기 실험(OrcusProbe, 2026-10-31):** gRPC `injectWheel`은 S7에서 스크롤되지 않았지만, **`cmd input mouse scroll x y --axis VSCROLL,n`은 된다**(0.06 s). 한 칸이 128 px였고, **Canvas 위에서 굴려도 Canvas는 터치·드래그를 받지 않고(드래그 0 유지) 바깥 화면만 스크롤됐다.** → ARCHITECTURE §9.3의 "Canvas 밖에서만 짧은 세로 드래그" 대신 이 명령을 쓴다. Canvas 위치를 알아낼 필요가 없어진다.
  - **2 device 입력:**
    - 휠은 `cmd input mouse scroll … --axis VSCROLL,n`(app이 주입하는 `ScrollSink`, `device-wheel` 스레드에서 칸을 더해 보냄). ARCHITECTURE §9.3을 고쳤다.
    - **빠른 타이핑 글자 순서(3a 미룸):** 큐에서 기다리는 붙여넣기를 하나로 묶고, Paste 키 뒤 100 ms가 지나야 다음 setClipboard를 한다(유닛: 0.7 s 동안 쌓인 5개 → setClipboard 1번, 순서 그대로).
    - **스트림 재연결 뒤 첫 입력 손실(3a 미룸):** gRPC는 실패한 호출의 요청 이터레이터를 한 번 더 당기는데, 그 당김이 다음 입력을 가져가 버렸다. 이제 스트림 세대를 두고 실패하자마자 세대를 올려, 옛 이터레이터는 아무것도 가져가지 않고 끝난다. 가짜 서버에 스트림을 끊는 키를 넣어 확인했다(고치기 전 코드로는 이 유닛이 실패함).
  - **3 apk·app:**
    - 방향 불일치: 앱이 RUNNING인 동안 2 s마다 표시 회전과 자세를 비교해, 같은 불일치를 두 번 연속 보면 `RunEvent.warnings`에 `APP_ORIENTATION_MISMATCH`(배너, 해결 [화면 방향 맞추기])를 넣는다. 맞으면 빼고, 앱이 끝나면 비운다. 엔진이 READY가 아니면 건너뛴다.
    - 마이크: ApkService가 실행할 때마다(크래시 재시작 포함) `privacy.mic_allowed_packages`대로 켜고 끈다(3a 미룸 해결).
    - COLD에서 FRESH_PHONE을 FRESH_APP으로 바꿀 때 앱 카드에 안내 칩을 단다(3a 미룸 해결).
    - PC 녹음 장치 유무는 `waveInGetNumDevs()`(이 PC: 0개).
  - **4 gui:**
    - [앱]·[센서]·[로그] 탭. [센서]: 흔들기(큰 버튼, 누르고 있으면 450 ms마다, 감도는 앱마다 기억), 회전(왼쪽/오른쪽, 지금 자세, [화면 방향 맞추기]), 기울기 패드(±20°, 최대 20 Hz, 더블클릭 수평), 위치(프리셋·위도·경도·힌트 2개), 마이크(앱 동의 저장, 녹음 장치 없으면 꺼짐). 컨트롤마다 일으키는 App Inventor 이벤트를 적었다.
    - 폰 옆 버튼에 회전 2개·흔들기·[가]를 더했다. 단축키 F6·F7·F8·Ctrl+T, 폰 화면의 한/영 키(`VK_HANGUL`도 확인)는 한글 입력 바를 연다.
    - 한글 입력 바(폰 아래): Enter 보내기, Shift+Enter 줄바꿈, Esc 닫기, 툴팁 "휴대폰 클립보드를 덮어씁니다. 비밀번호에는 쓰지 마세요."
    - 설정 대화상자(`gui/settings_dialog.py`, GPU 대화상자 대체): 바뀐 키만 저장, GPU는 재구축 확인 뒤.
    - 닫을 때 안내(GOLDEN, 앱을 설치·실행한 세션): [닫기]·[취소]·□ 다시 묻지 않기. COLD는 묻지 않고, 로그오프도 묻지 않는다.
    - **옆 버튼 끌어서 취소(3a 미룸):** `released` 대신 `clicked`로 보낸다(밖에서 떼면 안 눌림).
    - **종료 중 재실행(3a 미룸):** 정지를 시작하면 리스너부터 닫는다. 두 번째 실행은 넘기기에 실패하면 mutex가 사라질 때까지 최대 20 s 기다렸다가 그 자리에서 시작한다.
    - **파이프 상대 SID(3a 미룸):** 넘기기 전에 파이프 서버 프로세스의 계정 SID가 우리 것인지 확인한다(`win32.pipe_server_sid`). 이것으로 3a에서 미룬 GUI 다섯 건과 COLD 안내·마이크 재적용을 모두 처리했다.
    - 유닛: GUI 3b 13개(`tests/unit/test_gui_3b.py`) 등, 전체 402개, pyright 0건.
  - **5 스모크(실기기, 2026-10-31):** 흔들기·회전·위치를 더했다. 처음 두 번은 흔들기가 FAIL이었다. Shaking은 일어났지만 탭 단계의 고양이 소리 0.3 s 뒤라 App Inventor Sound의 MinimumInterval(500 ms)이 재생을 막았다("Unable to play because MinimumInterval has not elapsed"). 0.6 s 기다리고 두 로그 줄을 모두 증거로 받게 고친 뒤 **전체 PASS**: 흔들기 0.9 s, 회전 0.7 s에 가로 프레임과 앱 표시 `ROTATION_90`, 위치 0.7 s에 gps·fused 둘 다 갱신, 초기화 7.0 s, 정지 1.6 s.
    - 함께 고침: 위치 재전송이 뒤따른 초기화와 겹쳐 오류 추적을 남겼다(→ 버림, info 로그). 방향 점검이 초기화 중 2 s마다 로그를 남겼다(→ READY가 아니면 조용히 건너뜀).
  - **6 수용 시험(실기기 `tests/device/test_acceptance_3b.py`, 2026-10-31):**
    - **첫 실행에서 찾은 것:**
      - **(제품) 위치 앱이 "loc -"에 머물렀다.** 테스트 제공자는 값을 보낼 때만 알리므로, 세션 시작 때 한 번 보낸 위치는 나중에 업데이트를 요청한 앱에 닿지 않는다. 이제 위치 권한이 있는 앱이 RUNNING이 될 때와 App Inventor 화면이 다시 나올 때(앱 로그 `Form … got onResume`, 권한 대화상자 뒤 포함) 가장 최근 위치(처음에는 첫 프리셋)를 다시 3번 보낸다(`DeviceControls.resend_location`). 유닛 2개.
      - **(제품, 검토 중 발견) 파이프라인 큐에 명령이 2 s보다 자주 들어오면 방향 점검이 계속 밀릴 수 있었다.** 점검을 시계 기준으로 바꿨다(유닛: 20 ms마다 명령을 넣어도 배너가 뜸).
      - (시험) 프레임 스트림을 멈춘 뒤에는 탭·휠 좌표가 마지막 프레임의 옛 회전으로 계산됐다. 모듈 전체에 스트림 하나를 켜 두게 고쳤다(창은 늘 그렇게 동작함). 가로 화면에서는 라벨이 화면 밖으로 밀려 휠로 내려 읽는다. 라벨 이름 "touch2"의 2를 좌표로 읽던 것도 고쳤다.
    - **B9 영향:** 방향 점검 하나는 adb 프로세스라 우리 프로세스 CPU 7.3 ms다(60번 연속 측정). 2 s마다면 한 코어의 0.36%이고, 3a 창 0.31%와 합치면 1%에 가까워진다. 그래서 평소 3 s(0.24%)로 늘리고, 실행 직후와 화면이 다시 나올 때만 1.2 s 간격으로 두 번 바로 본다. 배너는 Screen2를 누르고 4.2 s에서 2.7 s로 빨라졌다.

  | 기준 | 결과 (2026-10-31) |
  |---|---|
  | B3 | 흔들기 클릭 10번 → Shaking: Weak·Moderate·Strong 세로 각 **10/10**, Moderate 거꾸로·가로·반대 가로 각 **10/10**. TalkToMe: OrcusProbe가 한국어 음성으로 "안녕하세요"를 말함(AfterSpeaking true), TalkToMe는 흔들기 0.4 s 뒤 "Stop shaking me!"를 말함(앱 로그 `InternalTTS speak called`). **통과** |
  | 회전 방식(§9.7) | Strong 감도에서 15°씩 0.1 s로 6번 돌림(90°·180° 섞음, 13 s) → Shaking **0번**. 같은 조건에서 한 번에 90° → **1번**(S7과 같음). **통과** |
  | B5 | 탭 오차(Canvas Touched, 패널 px): 세로 0.0/0.0, 가로 1.2/0.8, 거꾸로 1.2/0.8(표시는 직전 90° 그대로, S7과 같음), 반대 가로 0.8/0.8 → 모두 ±4 px 안. 가로 고정 Screen2: 누르고 **2.7 s** 뒤 방향 불일치 배너 → [화면 방향 맞추기] 뒤 탭 오차 0.0 px, Screen2를 닫아도 배너 없음. 창이 프레임 회전을 따라 16:9로 다시 맞추는 것은 GUI 유닛. **통과** |
  | B6 | 붙여넣기: TextBox "안녕하세요 Orcus", MultiLine "안녕하세요⏎Orcus"(줄바꿈 유지), NumbersOnly "안녕123" → "123". 한글 입력 바의 Enter·Shift+Enter·Esc는 GUI 유닛. **통과** |
  | B13 | 위치: 아무 조작 없이 실행만 하면 서울시청이 들어옴(probe 시작 7.8 s 안), 부산시청 **2.2 s**, 남산서울타워 **3.1 s**(목표 10 s). 위치 힌트는 [센서] 탭과 앱 칩에 있음(GUI 유닛). 마이크 동의: 동의한 앱 실행 → 에뮬레이터 `realAudioEnabled` 켜짐, 동의 없이 다시 실행 → 꺼짐. 기울기: OrientationSensor pitch −90 → −75(앞뒤 15°), 좌우 기울임에 roll 변화. **통과** |
  | B14 | 설정 대화상자: 바뀐 키만 저장, GPU 변경은 재구축 확인 뒤(거절하면 아무것도 저장 안 함), 닫을 때 안내는 GOLDEN에서 앱을 실행한 뒤에만(COLD·앱 없음·로그오프는 묻지 않음), "다시 묻지 않기" 저장 → GUI 유닛 `test_gui_3b.py`. 실제 GPU 재구축 경로는 3a B11 기기 시험. **통과(유닛)** |
  | 휠 | 두 칸 아래 → Form 256 px 스크롤, Canvas 위에서 두 칸 위 → 제자리, Canvas의 드래그 수 그대로. **통과** |
  | 스모크 | 흔들기·회전·위치 포함 전체 PASS(위 5단계) |
  | B10 | 유닛 406개·pyright 0건·3b 기기 시험·스모크 통과. 수동 점검(배율 100/125/150%, 다른 모니터, Mirage, V3, 실제 한/영 키·휠·한글 입력 바)은 사용자 몫 |
  - **사용자 확인이 필요한 것(B10 수동 점검, 3b):**
    1. 배율 100/125/150%와 다른 모니터에서 창을 열고 [센서] 탭·한글 입력 바가 잘리지 않는지, 가로 앱에서 창이 16:9로 다시 맞춰지는지.
    2. 실제 키보드: 폰 화면을 누른 뒤 **한/영 키**로 한글 입력 바가 열리는지(Ctrl+T, [가] 옆 버튼도). 바에서 IME로 "안녕하세요" → Enter → OrcusProbe "plain" 입력란에 들어가는지, Shift+Enter 줄바꿈, Esc 닫기.
    3. 실제 마우스 휠로 OrcusProbe 화면이 스크롤되는지(Canvas 위에서도 그림이 그려지지 않는지).
    4. F6을 누르고 있으면 약 0.5 s마다 흔들리는지, F7/F8로 돌릴 때 앱이 흔들기로 착각하지 않는지.
    5. GOLDEN에서 앱을 실행한 뒤 창을 닫으면 데이터 안내가 뜨고 [취소]로 안 닫히는지. [설정]에서 항목을 바꾸면 반영되는지.
    6. Mirage 켬, V3 실시간 감시 켬 상태에서 위 동작.
    - 이 PC에는 녹음 장치가 없어 마이크 버튼은 꺼져 있고 "이 PC에 마이크가 없습니다"가 보이는 것이 정상이다.
  - **3a 회귀(실기기, 2026-10-31, 3b 코드로):** `test_acceptance_3a.py`·`test_apk_real.py`·`test_engine_real.py` 20개 중 18개 실행·**모두 통과**(GPU 재구축 두 번과 Drive 업로드 시험은 3b와 무관해 뺌). B1 따뜻한 엔진 1.8–2.6 s, 시작과 동시 드롭 16.3 s, B2 PaintPot 지연 77–80 ms·프레임 나이 p95 6.6 ms, 입력 스트림이 끊긴 뒤 손가락 고착 없음(item 6, 새 스트림 세대 코드), 가로 고정 launcher는 실행 전에 한 번에 회전(B8), 크래시 재시작 재설치(B11).
  - **패키징 스모크(2026-10-31):** `py tools/dev.py build --gui-check` → 패키징본 selftest 모두 OK, 패키징본 창이 **14.2 s**에 READY("준비됨"), 강제 종료 뒤 SDK 프로세스 0개.
  - **3b 상태:** 작업 순서 1–6 완료. 유닛 406개·pyright 0건, 3b 기기 시험 10개 통과, 3a 회귀 18개 통과, 스모크·패키징 스모크 PASS. 남은 것은 사용자의 B10 수동 점검과 3b 승인이다. 커밋하지 않았다(제안 태그 v0.1.9).
  - **사용자 점검 뒤(2026-10-31): "한영키를 누르면 아래에 뜨고 입력이 되긴 하는데, 화면에서 바로 입력하면 안 된다 → 가능하면 구현", 마우스 휠은 동작 확인.**
    - **원인:** 폰 화면 위젯은 Qt에서 입력기(IME)를 쓰지 않는 위젯이라, Qt가 그 위젯에서는 Windows 한글 IME를 끄고 날것의 키를 받았다. 3b 첫 구현에서는 그 한/영 키를 입력 바 열기로 썼다.
    - **고침(P4 계획 "폰 화면에 직접 한글 입력"을 앞당김, ARCHITECTURE §9.5·§9.6):** 화면이 IME를 켠다(`WA_InputMethodEnabled`). 확정된 음절은 키와 같은 큐로 붙여넣고, 조합 중인 글자는 화면 아래 말풍선에 보이며 한자 후보 창도 그 자리에 뜬다. 조합 중에 화면을 누르면 먼저 확정한다. 한/영 키는 Windows IME 전환으로 돌려주었고, 입력 바는 Ctrl+T와 [가]로 남겼다.
    - 유닛: IME 이벤트(ㅇ→아→안, 다음 자음에 "안" 확정 …)를 넣어 붙여넣기 순서·말풍선·후보 창 위치를 확인(유닛 407개, pyright 0건). 실제 키보드·IME 확인은 사용자 몫이다(이 PC에서 키 입력을 흉내 내면 사용자 데스크톱에 키가 들어가므로 하지 않았다).
