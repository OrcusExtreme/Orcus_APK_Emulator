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
| 재부팅 뒤 이어서 | RunOnce `OrcusAPKSetup` = `D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli setup --resume` 등록됨. 사용자 재부팅 대기 | |

## S6 · S9 · S2 · S1 · S4 · S3 · S5 · S7 · S8

(대기)
