# 테스트 앱(픽스처)과 OrcusProbe 명세

APK는 크기와 라이선스 때문에 저장소에 넣지 않고 `D:\OrcusDev\fixtures\`에 둡니다. OrcusProbe의 `.aia`(프로젝트 파일)만
`tests/fixtures/OrcusProbe.aia`로 저장소에 넣습니다. 모두 App Inventor(ai2.appinventor.mit.edu)에서 [빌드] → [Android 앱 (.apk)]로 만듭니다.

## 언제 무엇이 필요한가

| 파일 이름(`D:\OrcusDev\fixtures\`) | 만드는 법 | 필요한 때 |
|---|---|---|
| `HelloPurr.apk` | 공식 튜토리얼 HelloPurr 그대로 | 스파이크 전(S5) |
| `TalkToMe.apk` | 공식 튜토리얼 TalkToMe 1·2편(말하기 + 흔들면 말하기) | 스파이크 전(S1·S5·S7) |
| `OrcusProbe.apk` + `OrcusProbe.aia` | 아래 명세 | **S3·S5 전** |
| `PaintPot.apk`, `BallBounce.apk`, `MoleMash.apk` | 공식 튜토리얼 그대로 | 3a 수용 시험 전 |
| `한글이름앱.apk` | HelloPurr의 앱 이름(AppName)만 "안녕 고양이"로 바꿔 빌드 | 3a 수용 시험 전 |
| `MinSdk36.apk` | HelloPurr의 Screen1 속성 [Android 최소 버전]을 36(Android 16)으로 | 3a 수용 시험 전 |
| `Landscape.apk` | HelloPurr의 Screen1 ScreenOrientation만 landscape로 | 3a 수용 시험 전 |
| (있다면) 2018년 이전에 빌드한 APK | 예전에 만든 파일 | 아무 때나. 없으면 '해당 없음' |

`MITAI2Companion` APK(파서 회귀용)는 `tools/fetch_fixtures.py`가 받습니다(Phase 2 후반).

## OrcusProbe 명세 (1쪽)

목적: 에뮬레이터가 App Inventor 이벤트를 제대로 일으키는지 라벨로 확인합니다. 라벨 값은 `uiautomator dump`로 읽으므로
**컴포넌트 이름과 라벨 문구를 아래와 같이** 지어 주세요. Screen1의 `ErrorOccurred` 이벤트는 **처리하지 마세요**(오류가 기본 방식으로 기록되어야 함).

**Screen1** (세로, Sizing=Responsive, AppName `OrcusProbe`, 패키지 기본값)

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
   - 버튼 `btnLangs` → `lblLangs.Text` = TextToSpeech1.AvailableLanguages (TTS가 없으면 오류 2701이 약 12초 뒤에 남)
   - 버튼 `btnRecStart` / `btnRecStop` → SoundRecorder1.Start / Stop. `AfterSoundRecorded(sound)` → Player1.Source = sound, Player1.Start,
     `lblRec.Text` = "rec ok". (첫 Start에서 권한 대화상자가 뜨면 **허용 안 함**을 누르면 오류 908 확인용이 됩니다)
6. **오류 경로**
   - `SpeechRecognizer1`(UseLegacy **체크**) / `SpeechRecognizer2`(UseLegacy **해제**): 버튼 `btnSpeechLegacy` / `btnSpeechNew` → GetText.
     `AfterGettingText(result, partial)` → `lblSpeech.Text` = "speech " + result
   - `BarcodeScanner1`(UseExternalScanner 기본값 체크): 버튼 `btnScan` → DoScan (스캐너 앱이 없으면 1501)
   - 버튼 `btnBlockError` → 빈 리스트에서 `select list item` index 5 (블록 실행 오류)
   - `ActivityStarter1`(Action = `orcus.invalid.ACTION`): 버튼 `btnBadIntent` → StartActivity (오류 601)
   - 버튼 `btnAnr` → `for each number from 1 to 30000000` 안에서 전역 변수 +1 (10초 넘게 돎). 누른 뒤 화면을 여러 번 탭하면 ANR
7. 버튼 `btnScreen2` → open another screen `Screen2`

**Screen2** (ScreenOrientation = **landscape**)
- `lblScreen2.Text` = "Screen2 landscape", `Canvas2`(너비 부모에 맞춤, 높이 200픽셀), `Canvas2.Touched(x, y)` → `lblTouch2.Text` = "touch2 x,y"
- 버튼 `btnBack2` → close screen

빌드 후 `OrcusProbe.apk`는 `D:\OrcusDev\fixtures\`에, 프로젝트를 [프로젝트] → [선택한 프로젝트 내보내기(.aia)]로 받은 파일은
이 저장소의 `tests/fixtures/OrcusProbe.aia`에 넣어 주세요.
