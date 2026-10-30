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
| dism UAC 실행 / 종료 코드 / dism이 호출자 Job에 들어가는가 | **사용자 실행 대기** (`setup.json`의 `dism.in_probe_job`에 기록됨) | — |

## S0b — 압축 해제 후 가속·V3

(대기)

## S6 · S9 · S2 · S1 · S4 · S3 · S5 · S7 · S8

(대기)
