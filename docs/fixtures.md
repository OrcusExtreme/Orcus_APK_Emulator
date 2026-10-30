# 테스트 앱(픽스처)과 OrcusProbe 명세

APK는 크기와 라이선스 때문에 저장소에 넣지 않고 `D:\OrcusDev\fixtures\`에 둡니다. OrcusProbe의 `.aia`(프로젝트 파일)만
`tests/fixtures/OrcusProbe.aia`로 저장소에 넣습니다.

## 만드는 방법

HelloPurr·TalkToMe·OrcusProbe는 `tools/ai2/projects.py`에 데이터로 정의되어 있고, `tools/ai2/build_fixtures.py`가
App Inventor 프로젝트 파일(.scm·.bky·.yail·project.properties)을 IDE와 같은 형식으로 만듭니다. 형식과 버전 번호는
appinventor-sources 커밋 `0712c114`(2026-10-29)의 YAIL 생성기(`blocklyeditor/src/generators/yail.js`)와 `YaVersion.java`를 따릅니다
(YaVersion 239, 블록 언어 40). 미디어(kitty.png, meow.wav)는 직접 그리고 합성하므로 외부 파일이 들어가지 않습니다.

```
py tools/ai2/build_fixtures.py            .aia와 빌드용 zip 생성 + 정적 검사 (App Inventor 없이)
py tools/ai2/setup_toolchain.py           JDK 17·Ant·appinventor-sources를 D:\OrcusDev 아래에 설치 (한 번)
py tools/ai2/ant.py buildserver           App Inventor 빌드 서버 빌드 (한 번)
py tools/ai2/build_fixtures.py --build    APK 빌드 → D:\OrcusDev\fixtures\<이름>.apk
```

- 빌드 서버를 쓰지 않는 길: `D:\OrcusDev\fixtures\src\<이름>.aia`를 ai2.appinventor.mit.edu에서 [프로젝트] → [내 컴퓨터에서 프로젝트
  가져오기(.aia)]로 열고 [빌드] → [Android 앱 (.apk)]. (자기 계정으로 직접 합니다.)
- 서명 키는 첫 빌드가 만든 `D:\OrcusDev\fixtures\src\android.keystore` 하나를 세 앱이 같이 씁니다. 다시 빌드해도 업데이트로 설치됩니다.
- `projects.py`를 고치면 `build_fixtures.py`를 다시 실행해 `tests/fixtures/OrcusProbe.aia`를 갱신합니다(단위 테스트가 확인).

## 언제 무엇이 필요한가

| 파일 이름(`D:\OrcusDev\fixtures\`) | 만드는 법 | 필요한 때 |
|---|---|---|
| `HelloPurr.apk` | 공식 튜토리얼 HelloPurr의 디자인·블록(쓰다듬기 → 소리 + 진동, 흔들기 → 소리) | 스파이크 전(S5) |
| `TalkToMe.apk` | 공식 튜토리얼 TalkToMe 1·2편(말하기 + 흔들면 말하기) | 스파이크 전(S1·S5·S7) |
| `OrcusProbe.apk` + `OrcusProbe.aia` | 아래 명세 | **S3·S5 전** |
| `PaintPot.apk` | 공식 튜토리얼(터치 → 점, 드래그 → 선, 색 버튼, Wipe). `projects.paint_pot` | 3a 수용 시험 전(B2) |
| `BallBounce.apk` | 공식 튜토리얼(Flung → Heading·Speed, EdgeReached → Bounce). `projects.ball_bounce` | 3a 수용 시험 전(B2) |
| `MoleMash.apk` | 공식 튜토리얼 짧은 판(1 s마다 두더지 이동, 맞히면 점수). 두더지 그림은 직접 그림. `projects.mole_mash` | 3a 수용 시험 전 |
| `HelloPurrKo.apk` | HelloPurr의 앱 이름(AppName)만 "고양이 쓰다듬기" | 3a 수용 시험 전(B8) |
| `HelloPurrMin36.apk` | HelloPurr + 프로젝트 속성 AndroidMinSdk = 36(`project.properties`의 `androidminsdk`) | 3a 수용 시험 전(B8) |
| `HelloPurrLand.apk` | HelloPurr의 Screen1 ScreenOrientation만 landscape | 3a 수용 시험 전(B8) |

3a 수용 시험용 6개(위 표의 PaintPot부터)는 `py tools/ai2/build_fixtures.py`가 `D:\OrcusDev\fixtures\src\<이름>.aia`로 만들어 둡니다(2026-10-30 생성).
웹 IDE에서 각 .aia를 가져와 빌드한 뒤 `D:\OrcusDev\fixtures\<이름>.apk`로 저장하면 됩니다. ARM 전용 APK와 합성 파일(AAB·AIA·AIX
이름만 바꾼 zip, 잘린 zip)은 시험 코드가 HelloPurr.apk로부터 직접 만듭니다.
| (있다면) 2018년 이전에 빌드한 APK | 예전에 만든 파일 | 아무 때나. 없으면 '해당 없음' |

`MITAI2Companion` APK(파서 회귀용)는 `tools/fetch_fixtures.py`가 받습니다(Phase 2 후반).

## OrcusProbe 명세 (1쪽)

목적: 에뮬레이터가 App Inventor 이벤트를 제대로 일으키는지 라벨로 확인합니다. 라벨 값은 `uiautomator dump`로 읽으므로
**컴포넌트 이름과 라벨 문구를 아래와 같이** 지어 주세요. Screen1의 `ErrorOccurred` 이벤트는 **처리하지 마세요**(오류가 기본 방식으로 기록되어야 함).
라벨은 첫 단어("touch", "drag", …)로 찾으므로, 어떤 버튼 문구도 라벨의 첫 단어로 시작하지 않게 합니다(버튼: reset, weak, moderate, strong,
speak, voices, record, stop, listen (legacy), listen (new), scan, block error, bad intent, ANR loop, open Screen2, close).
`uiautomator dump`는 화면 밖 노드를 빼므로 아래쪽 항목은 스크롤한 뒤 읽습니다.

**Screen1** (화면 방향은 **기본값(unspecified)**: 자동 회전(S7, 수용 기준 B5)을 보려고 세로로 고정하지 않음. Sizing=Responsive,
Scrollable 체크, AppName `OrcusProbe`, 패키지 기본값)

1. **터치** — `Canvas1`(너비 부모에 맞춤, 높이 300픽셀, 배경 흰색)
   - `Canvas1.Touched(x, y)` → `lblTouch.Text` = "touch x,y" (소수점 없이)
   - `Canvas1.Dragged(...)` → 전역 변수 `dragCount` +1, `lblDrag.Text` = "drag N"
   - `Canvas1.Flung(x, y, speed, heading, xvel, yvel)` → `lblFling.Text` = "fling speed" (소수 둘째 자리)
   - 버튼 `btnResetTouch`: dragCount = 0, 세 라벨 초기화
2. **흔들기·센서** — `AccelerometerSensor1`(Sensitivity 기본값 Moderate)
   - `Shaking` → 전역 `shakeCount` +1, `lblShake.Text` = "shake N"
   - 버튼 `btnWeak` / `btnModerate` / `btnStrong` → Sensitivity를 1 / 2 / 3으로, `lblSens.Text` = "sens 1|2|3"
   - `AccelerationChanged(x, y, z)` → `lblAccel.Text` = "accel x,y,z" (소수 첫째 자리)
   - `OrientationSensor1.OrientationChanged(azimuth, pitch, roll)` → `lblOrient.Text` = "orient a,p,r" (정수)
   - `Screen1.ScreenOrientationChanged` → `lblScreenOrient.Text` = "screen " + Screen1.ScreenOrientation
3. **위치** — `LocationSensor1`: **TimeInterval = 1000, DistanceInterval = 0**
   - `LocationChanged(latitude, longitude, ...)` → `lblLoc.Text` = "loc lat,lon" (소수 넷째 자리)
4. **입력**
   - `Screen1.BackPressed` → 전역 `backCount` +1, `lblBack.Text` = "back N" (화면을 닫지 않음)
   - `txtPlain`(TextBox), `txtMulti`(TextBox, MultiLine 체크), `txtPassword`(PasswordTextBox), `txtNumber`(TextBox, NumbersOnly 체크)
   - `WebViewer1`(높이 120픽셀): 미디어에 `probe.html`을 올리고 HomeUrl = `file:///android_asset/probe.html`.
     `probe.html` 내용: `<input id="w" placeholder="web input"><textarea id="t"></textarea>` 한 줄이면 됩니다.
5. **소리·말** — `TextToSpeech1`(Language `ko`), `SoundRecorder1`, `Player1`
   - 버튼 `btnSpeak` → Speak "안녕하세요". `AfterSpeaking(result)` → `lblTts.Text` = "tts " + result
   - 버튼 `btnLangs` → `lblLangs.Text` = "langs " + TextToSpeech1.AvailableLanguages (TTS가 없으면 오류 2701이 약 12초 뒤에 남)
   - 버튼 `btnRecStart` / `btnRecStop` → SoundRecorder1.Start / Stop. `AfterSoundRecorded(sound)` → Player1.Source = sound, Player1.Start,
     `lblRec.Text` = "rec ok". (첫 Start에서 권한 대화상자가 뜨면 **허용 안 함**을 누르면 오류 908 확인용이 됩니다)
6. **오류 경로**
   - `SpeechRecognizer1`(UseLegacy **체크**) / `SpeechRecognizer2`(UseLegacy **해제**): 버튼 `btnSpeechLegacy` / `btnSpeechNew` → GetText.
     `AfterGettingText(result, partial)` → `lblSpeech.Text` = "speech " + result
   - `BarcodeScanner1`(UseExternalScanner 기본값 체크): 버튼 `btnScan` → DoScan (스캐너 앱이 없으면 1501)
   - 버튼 `btnBlockError` → `evaluate but ignore`(빈 리스트에서 `select list item` index 5) (블록 실행 오류)
   - `ActivityStarter1`(Action = `orcus.invalid.ACTION`): 버튼 `btnBadIntent` → StartActivity (오류 601)
   - 버튼 `btnAnr` → `for each number from 1 to 30000000` 안에서 전역 변수 +1 (10초 넘게 돎). 누른 뒤 화면을 여러 번 탭하면 ANR
7. 버튼 `btnScreen2` → open another screen `Screen2`

**Screen2** (ScreenOrientation = **landscape**)
- `lblScreen2.Text` = "Screen2 landscape", `Canvas2`(너비 부모에 맞춤, 높이 200픽셀), `Canvas2.Touched(x, y)` → `lblTouch2.Text` = "touch2 x,y"
- 버튼 `btnBack2` → close screen

라벨의 처음 값은 "touch -", "drag 0", "fling -", "sens 2", "shake 0", "back 0", "screen -", "accel -", "orient -", "loc -", "tts -",
"langs -", "rec -", "speech -", "touch2 -"입니다. `build_fixtures.py`가 `OrcusProbe.apk`는 `D:\OrcusDev\fixtures\`에,
`.aia`는 이 저장소의 `tests/fixtures/OrcusProbe.aia`에 둡니다.
