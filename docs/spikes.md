# Phase 2 스파이크 기록 (S0a–S9)

측정값·결정·바뀐 설계를 여기에 남깁니다. 실행 순서: **S0a → (설치 다운로드) → S0b → S6·S9 → S2·S1 → S4 → S3·S5 → S7·S8**.
설계를 바꾸는 결과는 plan.md 진행 기록에도 적고 Phase 3 계획을 고쳐 다시 승인받습니다.

## S0a — WHPX·권한·V3 (다운로드 전)

| 항목 | 결과 | 시각·출처 |
|---|---|---|
| WHPX InstallState (Win32_OptionalFeature `HypervisorPlatform`) | **2 = 꺼짐** (조회 0.9 s, 관리자 권한 불필요) | 2026-10-30 12:03, `orcus-cli setup` 1단계 |
| CBS RebootPending 키 | 없음 | 같음 |
| 계정 권한(토큰 elevation type) | **limited** = 관리자 계정 + UAC 분리 토큰 → UAC 창에서 [예]만 누르면 됨(암호 불필요) | 같음 |
| AhnLab V3 | `V3Svc` + `EPPAgentSvc`(+SelfProtect, WatchDog) → **EPP 중앙 관리** | 같음 |
| V3 예외를 로컬에서 넣을 수 있는가 | 미확인: 사용자가 V3 [환경 설정] → [검사 예외]가 잠겨 있는지 보면 확정 (D13) | — |
| `IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED)` | False. Hyper-V가 도는 루트 파티션에서는 False가 정상이라 판단에 쓰지 않음(기록만) | 같음 |
| SDK 인덱스 | 에뮬레이터 stable이 **37.2.13 (build 16740729, 2026-10-29 게시)**로 바뀌어 고정한 37.2.12(16428233)는 인덱스에서 빠짐. 고정 URL은 그대로 받을 수 있음(HEAD: 455,342,868 B, Range 지원) → 설계대로 고정 URL·SHA-1 사용 | 2026-10-30 |
| platform-tools 37.0.1, 이미지 google_apis 36 r7 | 인덱스와 고정값 일치(크기·SHA-1, stable, `android-sdk-license`) | `test_live_index_matches_pins` |
| dism UAC 실행 | 사용자가 UAC [예] → `ShellExecuteExW` 성공. 정책 차단 없음 | 2026-10-30 12:37–12:38, `setup.json` |
| dism 종료 코드·시간 | **3010**(성공, 재부팅 필요), 24.9 s | 같음 |
| dism이 호출자의 Job에 들어가는가 | **들어간다** (`IsProcessInJob(dism, 시험용 Job)` = true). 권한 상승 프로세스도 Job을 물려받는다 → [P4] GUI 마법사는 KILL_ON_JOB_CLOSE Job 안에서 dism을 띄우면 GUI가 죽을 때 dism도 죽는다. BREAKAWAY 도우미나 '닫기 막기'가 필요(ARCHITECTURE.md §1.2 반영) | 같음 |

## S0b — 압축 해제 후 가속·백신

| 항목 | 결과 | 시각·출처 |
|---|---|---|
| 내려받기 | 3개 2,359 MB, 평균 4.8 MB/s, SHA-1 모두 일치 | 2026-10-30 12:38–12:46 |
| 실시간 검사 엔진 | **Windows Defender도 켜져 있다**(`Get-MpComputerStatus`: AMRunningMode=Normal, 실시간 보호 켜짐). WSC 등록 백신은 Defender뿐이고, V3는 EPP 서비스로 따로 돈다. 즉 검사기가 둘이다 | 2026-10-30 13:0x |
| 처음 설계(임시 폴더에 풀고 이름 바꾸기) | **실패**: 에뮬레이터 386개 파일(1.07 GB)을 다 푼 직후 `tmp\extract_emulator\emulator → sdk\emulator` 이름 바꾸기가 `WinError 5`(액세스 거부). 사라진 파일은 없었다 → 격리가 아니라 검사 중 잠금 | `setup-20261030.log` |
| 잠금은 일시적인가 | 예. 몇 분 뒤 같은 폴더 이름 바꾸기는 바로 성공. 같은 exe/dll 112개를 다시 풀자 즉시 이름 바꾸기 성공(이미 검사한 파일은 다시 잡지 않는 것으로 보임). Restart Manager로는 잠금을 쥔 프로세스가 보이지 않음(커널 필터 드라이버의 검사로 추정) | `tools/spikes/s0b_extract_lock.py` |
| 설계 변경 | 최종 위치에 바로 풀고(이름 바꾸기 없음) `<패키지>.partial` 표시로 미완료를 구분. 삭제는 잠금이 풀릴 때까지 최대 2분 재시도. 안내 문구를 "격리"와 "검사 중 잠김"으로 나눔 | ARCHITECTURE.md §11.6 5단계 |
| 다시 실행 결과 | **성공**: 에뮬레이터 386개 파일 4.9 s, 시스템 이미지 28개 파일 27.2 s(합계 5,707 MB, `system.img` 4.2 GB). 잠금 오류 없음, `.partial` 남지 않음, zip과 남은 임시 폴더 삭제됨. `sdk` = 5,459 MB | 2026-10-30 12:57–12:58 |
| 실제 배치(S6 참고) | `emulator\emulator.exe`, `emulator\emulator-check.exe`, `emulator\qemu\windows-x86_64\qemu-system-x86_64(-headless).exe`, proto와 `emulator_access.json`은 `emulator\lib\`. `netsimd.exe`·`crashpad_handler.exe` 있음. `source.properties`에 `Pkg.BuildId=16428233`. 이미지 `source.properties`는 `emulator#35.4.9` 이상을 요구 | `setup.json` installed |
| 재부팅 전 상태 | InstallState=**1**, CBS RebootPending 키 **있음**(dism 3010 직후). 설계가 예상한 대로 InstallState만으로는 재부팅 완료를 알 수 없다 | 12:57 점검 |
| `emulator-check accel` (재부팅 전) | 종료 코드 0, `WHPX(10.0.26100) is installed and usable.` → **재부팅 전에도 '사용 가능'이라고 한다.** 그래서 이 검사만으로는 가속이 실제로 되는지 증명할 수 없다. 실제 확인은 에뮬레이터를 부팅하는 boot-check(S4·패키징 스모크)로 한다. setup에서는 재부팅 관문(6단계)이 가속 확인(7단계)보다 먼저라 잘못 통과하지 않는다 | `orcus-cli doctor` |
| 재부팅 뒤 이어서 | RunOnce가 13:03 재부팅 뒤 7–8단계를 마침(13:06:47). RebootPending 키 사라짐, RunOnce 값 삭제됨 | `setup.json` |
| **실제 부팅(재부팅 뒤)** | **성공.** 첫 콜드 부팅(`-gpu host`, 부팅 애니메이션 없음): discovery 10.6 s, getStatus 10.9 s, **Android 부팅 완료 103 s**, SHUTDOWN 정지 3.6 s. 두 번째부터 discovery 1.8 s(백신 캐시) | `orcus-cli doctor --boot-check --full` |
| 가속 증거 | 에뮬레이터 로그 `WHPX on Windows 10.0.26100 detected.` + **`Windows Hypervisor Platform accelerator is operational`**. 그런데 `getStatus.vmConfig.hypervisorType`은 **UNKNOWN**(37.2.12는 이 필드를 채우지 않음) → SESSION_INIT의 WHPX 검사는 로그 줄로 바꾼다 | 같음 |
| `host` / `host_hidden` | 둘 다 부팅 확인 통과(WHPX). host_hidden은 `qemu-system-x86_64.exe`(Qt 창 숨김)로 뜨며 화면에 창이 보이지 않았다 | `--boot-check` |

## S6 — discovery·프로세스·포트

| 항목 | 결과 |
|---|---|
| discovery 위치 | TMP를 `D:\OrcusAPK\tmp`로 바꿔도 **`%LOCALAPPDATA%\Temp\avd\running\pid_<PID>.ini`**. `<TMP>\avd\running`은 쓰이지 않음 |
| 파일 이름의 PID | **qemu**의 PID(emulator.exe 아님) |
| 키 | `avd.dir, avd.id, avd.name, cmdline, emulator.build, emulator.version, grpc.allowlist, grpc.port, grpc.token, launcher.dir, port.adb, port.serial` |
| 정지 후 | discovery 파일 삭제됨 |
| 프로세스 트리 | `emulator.exe`(우리 자식) → `qemu-system-x86_64(-headless).exe` → `crashpad_handler.exe`, `netsimd.exe --host-dns=<학교 DNS>`. 모두 우리 Job 안 |
| crash 보고 | `crashpad_handler --url=https://clients2.google.com/cr/report`, DB는 `D:\OrcusAPK\tmp\AndroidEmulator\`. `-no-metrics`와 별개로 crash 보고는 켜져 있다(개인정보 메모: Phase 3에서 끄는 방법 확인) |
| 수신 포트 | 모두 루프백: 콘솔 5640·adb 5641(127.0.0.1, ::1), gRPC 8640(127.0.0.1만), netsimd 6402 등. 방화벽 창 없음. 로그: "Token/JWT auth requested without tls, restricting access to localhost" |
| 정지 직후 남는 것 | **`emulator -kill <qemu pid> -sleep 20`** 감시 프로세스(1–2개)가 20 s 동안 남는다. qemu가 이미 끝났으면 바로 종료해도 된다 → 정지 정리에서 즉시 종료하면 A5의 N을 짧게(약 5 s) 둘 수 있다 |
| 에뮬레이터가 띄우는 adb | 우리 adb 서버 없이 부팅하면 부팅 직후 에뮬레이터 쪽에서 `adb.exe`가 떠 15037에서 서버를 연다(`D:\OrcusAPK\tmp\adb.log`). 이 서버에는 `ADB_USB=0`·스캔 끄기가 없다 → private 서버를 **반드시** 에뮬레이터보다 먼저 띄우고, 서버가 죽었을 때 15037을 차지한 낯선 adb는 채택하지 말고 종료한 뒤 우리 서버를 띄운다 |
| RAM | config.ini의 `hw.ramSize=2048`을 에뮬레이터가 **2560 MB로 올림**("Increasing RAM size to 2560MB", `hardware-qemu.ini`만 바뀌고 `config.ini`는 그대로) → 기본값을 2560으로 명시 |
| userdata | qcow2 오버레이: 첫 부팅 뒤 `userdata-qemu.img` 6 MB + `.qcow2` 924 MB. 6 GiB를 다 차지하지 않는다(S4 디스크 추정 갱신 근거) |
| 스냅샷 메시지 | `-no-snapshot-save`인데 종료 때 "Not saving state: RAM not mapped as shared" / "Saving snapshot 'default_boot' took 1 ms" 로그. S4에서 확인 |

## S9 — private adb 서버 (2026-10-30 13:26, `tools/spikes/s9_adb.py`)

| 질문 | 결과 |
|---|---|
| `adb -P 15037 nodaemon server`가 Job 안에서 도는가 | **예** (`IsProcessInJob` = true) |
| 에뮬레이터가 15037에 스스로 등록하는가 | **예.** 부팅 뒤 `emulator-5640 device product:sdk_gphone64_x86_64` |
| 공유 adb 키(`ADB_VENDOR_KEYS`) | **됨.** 에뮬레이터가 `D:\OrcusAPK\home\adbkey.pub`를 게스트에 넣었고(로그 `Sending adb public key`), 상태 `device`(unauthorized 아님). 다른 Windows 계정 확인은 남음 |
| 콜드 부팅 시간(두 번째부터) | 연결 1.3 s, **`sys.boot_completed`=1까지 26.9 s**. 첫 부팅(최초 준비) 103 s |
| `-H 127.0.0.1` 클라이언트가 서버를 몰래 띄우는가 | **아니오.** 서버가 없으면 `cannot start server on remote host`로 실패, 15037에 아무것도 안 뜸 |
| 스캔 끄기(`MAX_PORT=5553`) | 서버를 다시 띄우면 장치 목록이 **비어 있음**(스캔 안 함) |
| 재등록 `host:emulator:5641` | **0.06 s** 만에 `device`로 복구, 이어서 셸 동작(`ro.build.version.release`=16) |
| 서버 재기동 시간 | 종료→수신 3.2 s(그중 약 2 s는 이 PC에서 연결 거부를 기다리는 클라이언트 호출) |
| 격리 | 우리가 띄운 외부 5037 서버 목록에 `emulator-5640` **없음**. 우리 서버 목록에는 우리 에뮬레이터만 |
| mDNS | 우리 서버에서 mDNS 탐색이 켜져 있다(`mdns check` 응답) → `ADB_MDNS_AUTO_CONNECT=0` 추가(페어링된 무선 디버깅 휴대폰에 자동 연결 안 함) |
| 실행 중 adb 프로세스 | 2개: 우리 서버 + 에뮬레이터가 쓰는 adb 클라이언트(AVD의 `tmpAdbCmds`). 우리 서버가 먼저 떠 있으면 낯선 adb 서버는 생기지 않음 |
| 정지 후 | 감시 프로세스 `emulator -kill … -sleep 20` 1개 → 하네스가 종료. 그 뒤 SDK 프로세스 0개 |
| 남은 일 | explorer.exe 넘기기(폴더·URL)는 화면에 창을 띄우므로 사용자와 함께 확인 |

## S2 — 화면 프레임 (`-gpu host`, 2026-10-30 13:31, `tools/spikes/s2_s1_probe.py`)

설정 화면을 입력 스트림 드래그로 계속 스크롤시키며 6 s씩 받았다(그리기 비용 제외, Python 프로세스 전체 CPU).

| 형식·크기 | fps | 데이터 | 우리 CPU | 프레임 나이(중앙값) |
|---|---|---|---|---|
| RGB888 432×768 | 48 | 48 MB/s | **0.23 코어** | 6 ms |
| RGB888 720×1280 | 55 | 153 MB/s | 0.87 코어 | 15 ms |
| RGBA8888 432×768 | 56 | 74 MB/s | 0.31 코어 | 8 ms |
| RGBA8888 720×1280 | 51 | 188 MB/s | 0.95 코어 | 20 ms |

- **결정:** 기본은 RGB888, 창 크기 프레임(주 모니터 432×768). 720×1280(세로 모니터)은 비용이 약 4배라 30 fps 상한 후보. `Image.timestampUs`는 벽시계 µs라 프레임 나이를 바로 잴 수 있다.
- 화면이 바뀔 때만 프레임이 온다(정지 화면에서는 0 fps → B9의 "정지 화면 CPU < 1%"와 맞는다).
- 탭 → 첫 프레임: 중앙값 142 ms(10회). 목록 항목의 눌림 표시는 Android가 스크롤과 구분하려고 약 100 ms(tap timeout) 늦게 그리므로 전송 지연이 아니다. 드래그 기준 지연은 후속 측정에 기록.
- **프레임 크기는 `Image.format`에 있다(중요).** 37.2.12는 `Image.width/height`를 **모든 프레임에서 0**으로 보내고, 실제 크기는 `Image.format.width/height`(예: 432×768), 데이터는 정상 길이(995,328 B)다. `getScreenshot`도 같다(PNG 기본 크기 720×1280). 후속 측정 스크립트가 `Image.width==0`을 빈 프레임으로 보고 모두 버리는 바람에 12분 넘게 멈췄다(13:34–13:49). py-spy로 스택을 확인해 원인을 찾았고, 측정 코드·뷰어·FakeEmulatorServer·ARCHITECTURE.md(§4, §5.1, §9.1)를 고쳤다.
- 스트림은 시작하자마자 현재 화면 1장을 보내고, 그 뒤로는 화면이 바뀔 때만 보낸다.

### S2 후속 (`-gpu host`, 13:51, `tools/spikes/s2_followup.py`)

| 항목 | 결과 |
|---|---|
| 게스트 렌더러 | `GLES: Google (NVIDIA Corporation), Android Emulator OpenGL ES Translator (NVIDIA GeForce GTX 1660/PCIe/SSE2), OpenGL ES 3.1 (4.5.0 NVIDIA 595.79)` → **창 없이도 호스트 GPU**. 소프트웨어 판정 문자열(SwiftShader 등)이 아니다 |
| 기본 GPU 모드(결정) | **`host`**(`-no-window`, headless qemu). `host_hidden`은 Qt가 실린 qemu라 더 무겁고, 둘 다 부팅 확인을 통과했으므로 대비책(L2)으로 둔다 |
| 행 순서 | 스트림 프레임과 게스트 `screencap` PNG 200점 비교: 위→아래 일치 197, 뒤집으면 73 → **위→아래**, `frame.flip_rows=false` 유지 |
| `getScreenshot` | PNG·RGB888·RGBA8888 × 요청 크기(0×0, 720×1280, 432×768) 모두 정상. 0×0 요청은 패널 크기(720×1280). 크기는 `format`에 있음 |
| 드래그 → 프레임 지연 | 8회 중앙값 **46 ms**(34–72 ms). 이동 이벤트가 터치 슬롭을 넘는 순간부터 다음 프레임까지 |
| 남은 수동 확인 | 원격제어(Mirage) 켬·끔 상태의 렌더러, 체감(뷰어 `tools/spikes/viewer.py`) |

## S4 — 골든 스냅샷 (13:53–13:58, `tools/spikes/s4_golden.py`)

| 항목 | 결과 |
|---|---|
| 준비 부팅(`-change-locale ko-KR`) | 부팅 25.1 s, 로캘 ko-KR까지 +4.4 s. **그 직후 로캘 변경으로 프레임워크가 재시작**되는 동안 `settings`/`lock_settings`/`location` 서비스가 없어 준비 명령 5개가 실패(`DeadObjectException`, `Can't find service`). → 설계 §6.5 2단계(재시작 관찰 뒤 진행)가 꼭 필요. 이 골든은 버리고 다시 만듦(아래 2차) |
| no-save 상태에서 gRPC `SaveSnapshot` | **성공**(1.8 s). `success`·`err`로 판정 가능 |
| **같은 이름 다시 저장** | **성공(덮어씀)**. 설계가 가정한 "이미 있는 이름 거부"는 37.2.12에서 일어나지 않는다 → Delete 먼저는 남기되(깨끗한 덮어쓰기) 거부 대응은 불필요. FakeEmulatorServer도 실제처럼 덮어쓰게 바꿈 |
| 골든 크기 | 1.09 GB(`ram.bin` 1,079 MB, `textures.bin` 11 MB, `screenshot.png`, `snapshot.pb`, `hardware.ini`). 설계 추정 2.5 GiB보다 작다 |
| **골든 부팅** | 3회 **5.8 / 5.5 / 4.8 s**(연결 1.1–1.3 s 포함) → 목표 10 s 달성. 표식(provision id) 있음, 세션 nonce 빈 값, 로캘 ko-KR, `ListSnapshots` 상태 Loaded |
| 실행 중 초기화(`LoadSnapshot`) | 성공 2.9 s, adb `device` 복귀 0.6 s, 이번 세션 nonce가 빈 값으로 돌아옴, 골든 표식 그대로 → §6.7 검증 방식이 실제로 동작 |
| **게스트 시계** | 골든을 로드하면 **저장 시각으로 돌아가고 따라오지 않는다**: 저장 후 21.6 / 38.3 / 55.6 s 뒤 부팅에서 그만큼 뒤처짐 → `session_init`에 시계 맞추기 필요(2차에서 방법 시험) |
| `-snapshot`만(`-no-snapshot-save` 없이) | **종료할 때 그 이름으로 저장함**(mtime 갱신) → 공식 문서가 맞다. "`-snapshot`이면 반드시 `-no-snapshot-save`" 불변식이 골든을 지킨다 |
| 망가진 스냅샷 강제 로드 | `ram.bin`을 잘라 `-force-snapshot-load`로 부팅: **종료도 삭제도 하지 않고 멈춤**(gRPC는 열림, 96 s 동안 부팅 완료 없음, 로그에 오류 없음). 설계가 예상한 "지우고 종료"와 다른 실패 방식 → GOLDEN의 BOOTING 시간 초과(30 s)도 골든 로드 실패로 보고 재구축해야 한다 |
| `snapshots\default_boot` | 첫 부팅 때 생긴 **2.5 GB `ram.img`**(RAM 크기 파일). 우리 모드는 쓰지 않는다 → 2차에서 지운 뒤 다시 생기는지 확인 |

### S4 2차 — 준비 순서를 고친 골든 (14:00–14:04, `tools/spikes/s4_provision.py`)

| 항목 | 결과 |
|---|---|
| 프레임워크 재시작 | 부팅 완료 뒤 **약 5 s에 재시작**(서비스 사라짐 → `sys.system_server.start_count` 1→2). 서비스가 5회 연속 응답할 때까지 기다리니 부팅 뒤 6.6 s에 준비 가능 |
| 준비 명령 8개 | 모두 적용·다시 읽어 확인(회전 1, 화면 꺼짐 최대, stayon, 잠금 화면 끔, 위치 켬, adb 설치 검증 2개 끔, SDK 대화상자 끔) |
| 골든 | 저장 1.7 s, 1.15 GB. 골든 부팅 5.5 s, 설정·표식·로캘 유지 |
| `default_boot` | 지워도 준비 부팅에서 **다시 생김**(2.5 GB `ram.img`) → 디스크 추정에 넣거나 막는 방법을 찾아야 함 |

### S4 3차 — 골든 로드 뒤 게스트가 멈춘다 (14:05–14:12)

| 관찰 | 결과 |
|---|---|
| 증상 | 골든에서 부팅하면 처음 약 20 s는 정상인데, **로드 뒤 20–25 s에 게스트 전체가 멈춘다**: adb 셸 무응답(`adb devices`는 계속 `device`), 화면 프레임 없음(Home 키를 보내도 그대로), logcat도 그 시점(게스트 시각 로드+20 s)에서 끊김. 호스트 쪽 gRPC `getStatus`는 계속 응답 |
| 재현 | 3회 연속(첫 adb 실패 +25.6 s, +22.9 s 등). 재등록 메시지·`adb reconnect`로 복구 안 됨 |
| 직전 게스트 로그 | `StorageManagerService: Failed to notify volume state changed to the Storage Service … TimeoutException` 반복 |
| 로드 로그 | 매번 `Failed to create process resource for puid 1,2,3,4,9,10,11`(gfxstream이 게스트 프로세스별 GPU 자원을 복원하지 못함으로 추정) |
| 시계 가설 | 기각 쪽: root `date`로 맞추려던 명령 자체가 멈췄고, 시계를 바꾸지 않아도 멈춘다 |
| 이전 S4에서 못 본 이유 | 골든 세션을 모두 20 s 안에 끝냈다 |
| 의미 | **골든 모델의 핵심 가정(로드 뒤 정상 사용)이 이 PC·이 빌드에서 깨진다.** 원인을 가르는 변형 실험 진행 중(아래) |

**변형 실험** (14:13–14:24, `tools/spikes/s4_golden_variants.py`): 콜드 부팅 10 s 뒤 시험용 스냅샷 저장 → 곧바로 강제 로드 → 60 s 감시(10 s마다 Home/Recents 키)

| 변형 | 저장 | 로드 부팅 | 60 s 동안 |
|---|---|---|---|
| V1 `host`, gRPC 저장 | 2.1 s | 6.8 s | **멀쩡함**(adb 실패 없음, 25 s 뒤 프레임 68장) |
| V2 `host_hidden`, gRPC 저장 | 2.1 s | 6.3 s | 멀쩡함(70장) |
| V3 `host`, 콘솔 `avd snapshot save` | 2.0 s | 6.0 s | 멀쩡함(65장) |
| V4 `software`, gRPC 저장 | 2.4 s | 6.1 s | 멀쩡함(74장) |

→ 저장 방식·GPU 모드는 원인이 아니다. 골든과 다른 점은 (1) **저장 뒤 몇 분 지나서 로드**(게스트 시계가 그만큼 뒤처진 채 깨어남), (2) 준비 세션(`-change-locale`, 프레임워크 재시작 직후)에서 저장, 두 가지.

**저장 4분 뒤 로드** (14:24–14:31, `tools/spikes/s4_skew_test.py`): 일반 콜드 세션에서 저장한 스냅샷을 240 s 뒤 로드 → **시계 오차 −0.1 s**(에뮬레이터가 로드할 때 게스트 시계를 맞춘다), 60 s 동안 멀쩡함(25 s 뒤 프레임 81장) → **(1) 기각. 원인은 준비 세션에서 저장한 것(2).**

| 같은 실험의 부수 결과 | |
|---|---|
| root | 정상 세션에서 `su 0 id` → uid=0(0.1 s), `adb root` 1.2 s, `date -u MMDDhhmmYYYY.ss` 설정 후 오차 −0.1 s, `adb unroot` 0.9 s. 골든 세션에서 `su`가 멈춘 것은 이미 망가진 상태의 증상 |
| "puid" 경고 | 모든 스냅샷 로드(정상 변형 포함)에서 4–8건 → 원인 아님, 무시 가능 |
| `auto_time` | 1(네트워크 시간 켜짐) |

**대책 → 검증됨** (14:35–14:40, `tools/spikes/provision_draft.py`, `s4_provision_v2.py`): 준비 부팅에서는 설정만 적용하고 끈다 → 로캘 옵션 없이 깨끗한 콜드 부팅 → 10 s 안정 → RAM 속성·표식 → 그 세션에서 골든 저장.

| 단계 | 결과 |
|---|---|
| 준비 부팅 | 부팅 24.3 s, 프레임워크 재시작 대기 7.6 s, 설정 7개 모두 적용·확인 |
| 깨끗한 콜드 부팅 | 23.3 s, 설정 7개·로캘 ko-KR 유지, RAM 속성 적용, 골든 저장 성공 |
| 60 s 뒤 골든 부팅 | **6.1 s**, 시계 오차 **−1.2 s**(로드 때 맞춰짐), 표식 정상 |
| 90 s 감시 | **adb 실패 0회**, 25 s 뒤 프레임 156장(60 s 뒤 68장), 90 s 뒤 root 정상 |

→ **골든 모델 유지.** 1회성 준비 전체는 약 90 s(설계 추정 2–4분보다 빠름). 세션마다 시계를 맞추는 단계는 보험으로만 둔다(오차 > 5 s일 때 `su 0 date`).

## S5 일부 — 설치·실행·logcat (App Inventor Companion 2.81u, 새 골든, 14:37–14:40)

사용자 픽스처(HelloPurr 등) 없이 실제 App Inventor 빌드인 Companion APK(18 MB)로 잰 것(`tools/spikes/s5_s8_partial.py`, `s5_crash_logcat.py`).

| 항목 | 결과 |
|---|---|
| 설치 `adb install -r` | -g 없이 6.8 s, **-g로 5.3 s**(18 MB. 보통 AI2 앱 3–6 MB는 더 빠를 것) |
| launcher 찾기 | `cmd package resolve-activity --brief -c android.intent.category.LAUNCHER <pkg>` → `<pkg>/.Screen1` |
| 실행 `am start -W` | 1.45 s(TotalTime 1,308 ms), 화면에 Companion UI("MIT App Inventor 2 … 2.81u") |
| 설치 검증·Play Protect 프롬프트 | 없음(준비 단계에서 `verifier_verify_adb_installs`·`package_verifier_enable`=0) |
| → 드롭→실행 예산 | 설치 3–7 s + 실행 1.5 s + 스테이징·분석 <1 s → **10 s 목표 안** |
| gRPC `streamLogcat` | **Parsed 모드는 아무것도 보내지 않는다**(7 s 동안 0줄). **Text 모드**는 `contents`에 threadtime 텍스트를 보내는데, 열자마자 부팅 이후 버퍼 전체(약 3.4만 줄)를 먼저 보낸다 → watcher는 Text를 직접 파싱하고 세션 시작 이전 줄은 버린다 |
| 크래시 문자열(`am crash <pkg>`) | `E AndroidRuntime: FATAL EXCEPTION: main` / `E AndroidRuntime: Process: <pkg>, PID: <pid>` / `android.app.RemoteServiceException$CrashedByAdbException: shell-induced crash`. `am crash`는 즉시 반환하고 크래시는 몇 초 뒤에 난다 |
| 골든 로드 직후 부수 문제 | **Bluetooth 프로세스 크래시**(`com.google.android.bluetooth`, `on_hardware_error: Hardware Error Event with code 0x42`, netsim BT). 화면에 "계속 중지됨" 대화상자가 뜰 수 있다 → Phase 3a 준비 단계에서 Bluetooth 끄기를 시험 |

## S8 일부 — 한글 붙여넣기 (설정 검색창, 14:38)

| 방식 | 결과 |
|---|---|
| `setClipboard` → 100 ms → **Paste 키** | **"안녕하세요 Orcus" 정확히 입력** |
| `setClipboard` → Ctrl 누름 / `v` / Ctrl 뗌 | **실패**: Ctrl이 조합되지 않아 `v`만 입력되고, ko-KR Gboard가 `v`를 **`ㅍ`**으로 바꿈 |
| 결정 | `input.paste_method="paste_key"` 유지. 또한 **ASCII 키 이벤트도 한글 자모로 바뀌므로** `type_ascii`도 붙여넣기 경로로 보낸다. App Inventor 입력란별(Password·NumbersOnly·WebViewer)은 OrcusProbe로 남은 확인 |

## S7 일부 — 내비게이션·키·회전 (OrcusProbe 없이, 14:43–14:45, `tools/spikes/s7_partial.py`, `s7_navbar.py`)

| 항목 | 결과 |
|---|---|
| `hw.mainKeys=yes`로 내비 바가 숨는가 | **아니오.** API 36은 제스처 내비(`navigation_mode=2`)로 동작 |
| 3버튼 내비 강제 | `cmd overlay enable-exclusive --category com.android.internal.systemui.navbar.threebutton` → `navigation_mode=0`, Back 정상 → **준비 단계에 추가**(가장자리 제스처 제거) |
| 베젤 키 | `GoBack`: 설정 → 홈. `AppSwitch`: 최근 앱 화면이 열림(화면 해시·하단 밝기 변화). `GoHome`: 홈 복귀. 셋 다 동작 |
| 회전 | `setPhysicalModel(ROTATION, z=90)` → 프레임이 `432×243, rotation=LANDSCAPE`(요청한 432×768 상자 안에 가로 비율로 맞춤). 홈 런처는 세로 고정이라 UI는 옆으로 누운 채 → 설계(§9.2, 프레임 회전 = 기기 자세)와 같다. 가로일 때는 768×432로 다시 요청(retarget) |
| 남은 확인(OrcusProbe 필요) | 흔들기 3개 감도·역방향 자세, 4방향 탭 좌표 오차, 가로 고정 Screen2, 기울기 20° 제한, 휠, 이동 이벤트 밀도, 입력 고착 |

## 패키징 스모크 (A8, 14:41–14:43, `tools/build.py --boot-check`)

| 항목 | 결과 |
|---|---|
| 빌드 | PyInstaller onedir, 창 EXE `OrcusAPK.exe` + 콘솔 EXE `orcus-cli.exe`가 한 폴더(`D:\OrcusDev\dist\OrcusAPK`), 40 s, **119 MB**(Qt 포함. 설계 추정 55–70 MB → Phase 4에서 Qt 파일 정리) |
| 첫 빌드에서 잡은 문제 | PySide6·apkInspector가 빠짐(아직 코드가 문자열로만 import) → `--selftest`가 잡아냄 → spec에 hiddenimports 추가 |
| `orcus-cli.exe selftest`, `OrcusAPK.exe --selftest` | **둘 다 통과**: import, 버전 = lock, 스텁 descriptor, 가짜 서버 왕복(한글 클립보드) |
| `OrcusAPK.exe --boot-check` | **host·host_hidden 모두 WHPX 통과**(discovery 1.3–1.5 s, 정지 1.3–2.0 s). Qt DLL이 묶인 상태에서도 에뮬레이터 자식이 정상 → 로더 위생 확인 |
| 백신 | V3·Defender가 패키징본 EXE를 막거나 격리하지 않음 |
| 빌드 도구 수정 | 이전 빌드 복사본 삭제에도 백신 잠금 "액세스 거부"가 나서 재시도 삭제(`devenv.remove_tree`)로 바꿈 |

## A6 — 풋프린트 (14:47)

| 항목 | 결과 |
|---|---|
| `D:\OrcusAPK` | **12.4 GB**(sdk 5.3, avd 7.0: userdata qcow2 2.8, cache 0.6, 골든 1.1, **`default_boot` 2.5**) → 추정 13–14 GiB 안. `default_boot`(쓰지 않는 quickboot RAM 파일)를 막으면 약 10 GB |
| C: | 허용 목록 안: `%USERPROFILE%\.emulator_console_auth_token`, `%USERPROFILE%\.android\adbkey*`(첫 부팅 때 우리 서버 없이 에뮬레이터가 띄운 adb가 만듦), `%LOCALAPPDATA%\Temp\avd\running`(비어 있음). RunOnce 값 삭제됨. 예외: `%LOCALAPPDATA%\Temp\adb.log`는 S9에서 일부러 띄운 외부 5037 서버의 기록(제품 동작 아님) |
| Drive 폴더 | IDE가 기본 인터프리터(Python 3.11)로 테스트를 탐색하며 `__pycache__`(cpython-311)를 만들었음 → 지우고 `tests/conftest.py`에서 `sys.pycache_prefix`를 D:로 지정. 이후 테스트 실행에도 생기지 않음 |

### A5 사전 확인 — 하네스 강제 종료

- 멈춘 하네스 프로세스를 `TerminateProcess`로 죽이자 **0.55 s 안에 SDK 프로세스가 0개**가 됐다(emulator, qemu, crashpad, netsimd, private adb 서버). KILL_ON_JOB_CLOSE가 의도대로 동작한다. 감시 프로세스(`-kill … -sleep 20`)도 생기지 않았다.
- qemu가 정상 종료하지 못했으므로 discovery 파일 `pid_<qemu>.ini`가 남는다 → 설계대로 시작 시 죽은 PID의 파일을 지운다(discovery는 이미 죽은 PID를 무시한다).

## S1 — TTS·음성 인식·IME (google_apis 36 r7)

| 항목 | 결과 |
|---|---|
| 빌드 | `sdk_gphone64_x86_64` **userdebug**(`ro.debuggable=1`) → root 가능 |
| TTS 엔진 | `com.google.android.tts`(Google TTS `20241125.02_p2`). `tts_default_synth`는 비어 있음(엔진이 하나뿐이라 기본으로 쓰임) |
| RECOGNIZE_SPEECH 액티비티 | 있음: Google TTS의 `GoogleTTSActivity` → App Inventor SpeechRecognizer(UseLegacy 켬) 경로에 제공자가 있다 |
| RecognitionService | 2개: Android System Intelligence(`com.google.android.as`), Google TTS → UseLegacy 끔 경로에도 제공자가 있다 |
| IME | Gboard(`com.google.android.inputmethod.latin`), Google 음성 입력 |
| 한국어 음성 데이터 | **들어 있지 않다**(root로 확인: `/data/data/com.google.android.tts/files`에 음성 팩·superpacks 없음, 한국어 파일 없음. 14:48, `tools/spikes/s1_tts_files.py`) |
| 게스트 인터넷 | 됨(`ping 8.8.8.8` 44 ms, 비행기 모드 꺼짐) → Google TTS가 한국어 음성을 내려받거나 온라인 합성할 수는 있다 |
| 남은 질문 | TalkToMe로 실제 한국어 발화가 되는지, 처음 한 번 음성을 내려받는지·로그인이 필요한지 → 되면 준비 단계(§6.5 5단계)에서 미리 내려받아 골든에 넣는다. 음성 인식(OrcusProbe)도 같은 방식으로 확인 |
| 이미지 결정(D8) | google_apis 36 유지(TTS·인식 제공자·root가 모두 있음). playstore 대안은 필요 없어 보임 |
