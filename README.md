# Orcus APK Emulator

<p align="center">
  <img src="assets/icon.png" width="128" height="128" alt="Orcus APK Emulator Logo">
</p>

> **Ultra-Lightweight, Instant-Launch Windows 11 Desktop Runner for MIT App Inventor APKs**  
> Powered by Headless Google Android Emulator & Low-Latency Token-Authenticated gRPC

[![Release](https://img.shields.io/badge/Release-v0.1.13-blue.svg)](https://github.com/OrcusExtreme/Orcus_APK_Emulator/releases/tag/v0.1.13)
[![Python](https://img.shields.io/badge/Python-3.14-green.svg)](pyproject.toml)
[![GUI](https://img.shields.io/badge/GUI-PySide6--Essentials%206.12-orange.svg)](pyproject.toml)
[![Hypervisor](https://img.shields.io/badge/Hypervisor-WHPX-purple.svg)](docs/ARCHITECTURE.md)
[![Tests](https://img.shields.io/badge/Tests-457%20Passed-brightgreen.svg)](tests/)
[![Typecheck](https://img.shields.io/badge/Typecheck-Pyright%20Clean-success.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](NOTICE)

---

## 📌 Overview

**Orcus APK Emulator** is a dedicated Windows 11 desktop runner designed specifically for testing educational Android applications built with **MIT App Inventor** (pure DEX bytecode, standard Android View/Canvas, sensor components, and media engines).

Unlike heavy, general-purpose commercial emulators (such as BlueStacks, Nox, or LDPlayer) that suffer from multi-gigabyte memory footprints, bloated user interfaces, bundled ads, and persistent background telemetry, Orcus APK Emulator executes an official **Google Android Emulator** instance in headless mode (`-no-window -gpu host`) atop the **Windows Hypervisor Platform (WHPX)**. It streams frames and injects inputs via a low-latency, token-protected **gRPC API**, providing an instantaneous, native-feeling development testing experience.

### 🎯 Key Highlights
* **Drag-and-Drop Instant Launch**: Drop any `.apk` file directly onto the window; the runtime stages the file, validates the manifest, and displays the running app in **under 10 seconds** (measured 2.8s total on official HelloPurr).
* **Golden Snapshot Boot (4.8–5.8s)**: Bypasses multi-minute cold boot sequences by launching from a pre-warmed, clean golden system snapshot on every session (measured 4.8–5.8s vs 10s budget).
* **Ultra-Low Latency GPU Streaming**: Headless `-gpu host` rendering over gRPC (NVIDIA GTX 1660 hardware acceleration), streaming 432×768 RGB888 at ~50 fps with only 0.23 host CPU cores and 6 ms frame age.
* **Lean Runtime Footprint**: Restricts running processes to only 4 daemons (Host GUI, Private ADB server, `emulator.exe`, and `qemu-system-x86_64-headless.exe`).
* **Zero Orphan Process Leaks**: Wraps the entire execution tree inside a Win32 Job Object configured with `KILL_ON_JOB_CLOSE`, guaranteeing immediate termination of all child emulator processes on exit or unexpected crash (< 0.55s).
* **Storage & Drive Isolation**: Keeps all high-volume SDK images, AVD disks, and caches on `D:\OrcusAPK` and `D:\OrcusDev` (pure ASCII paths), avoiding Google Drive file synchronization locks and C: drive exhaustion.

---

## 🏗️ System Architecture

```text
+-----------------------------------------------------------------------------------------------+
|  OrcusAPK.exe (Python 3.14 + PySide6, Win32 Job Object: KILL_ON_JOB_CLOSE)                    |
|                                                                                               |
|   [GUI Layer]                                                                                 |
|     - PySide6 QtWidgets 9:16 Mobile Viewport (1:1 Low-Latency Raster Blit)                    |
|     - Normalized Mouse -> Touch Pointer Event Translation & Bezel Buttons (Back/Home/Recents)|
|     - Windows IME Text Composition Bar -> Guest Clipboard Injection                          |
|     - [App Card] & [Diagnostic Logs] Collapsible Inspection Panels                            |
|        │ Non-blocking calls (SessionApi)                                                      |
|        ▼                                                                                      |
|   [Core Layer: orcus.contracts & orcus.common]                                                |
|     ├─ ApkService    : Classification, Staging, Manifest Parsing (apkInspector), Launch Watch |
|     ├─ EngineControl : State Machine, Ownership Lock, Epoch Tracking, Golden Snapshots        |
|     └─ DeviceSession : gRPC Client (RGB888 Screen Stream, Touch/Key Queues, Sensor Control)   |
+───────────────────────────────┬───────────────────────────────────────────┬───────────────────+
                                │                                           │ localhost:8640
                                ▼ [Child Process]                           ▼ [Child Process]
                     adb.exe -P 15037 server                      emulator.exe -no-window -gpu host
                     (Private Isolated ADB Daemon)                                  │
                                                                                    ▼ [Grandchild Process]
                                                                           qemu-system-x86_64-headless.exe
                                                                           (Android 16 / API 36, WHPX, Host GPU)
```

### Architectural Separation Rules
* **Strict Contract-Driven Isolation**: All inter-layer data types, state enumerations, and protocols are defined exclusively in `orcus.contracts` using standard library primitives (`@dataclass`, `Protocol`, `enum`).
* **Qt-Free Core**: Modules under `engine`, `device`, `apk`, and `setup` **never** import Qt/PySide6, allowing them to run independently in headless CLI and automated test harnesses.
* **GUI Decoupling**: The `gui` package communicates strictly through `contracts` and `common` abstractions, verified by AST import lint tests and strict Pyright checks.

---

## 🛠️ Technology Stack

| Component | Technology | Rationale & Trade-offs |
|---|---|---|
| **Language & Runtime** | **Python 3.14** (CPython 3.14.6) | Native Windows support via install-manager, pure Python AXML parsing, eliminates dual-language bridging overhead |
| **GUI Framework** | **PySide6-Essentials 6.12** (LGPL) | Lightweight 82 MB footprint (excluding WebEngine), direct 1:1 raster blitting, native IME event integration |
| **Virtualization Engine** | **Google Android Emulator 37.2.12** + **WHPX** | Official gRPC-capable virtualization engine fully compatible with Windows 11 Hyper-V / VBS isolation |
| **System Image** | **Android 16 (API 36) google_apis x86_64 (r7)** | Supports App Inventor target API 36 requirements, offline Korean TTS, Root privileges, and ARM64 translation |
| **Stream & Control IPC** | **Emulator gRPC API** (`streamScreenshot` / `streamInputEvent`) | Zero-encoding guest overhead, synchronized frame and touch stream, integrated sensor and clipboard RPCs |
| **APK Manifest Parser** | **apkInspector 1.3.7** | Pure Python (30 KB), zero C-extensions, fully resilient against UTF-16 string pools and obfuscated manifests |
| **Debug Bridge** | **Private ADB Server (Port 15037)** | Completely isolated private server; prevents port 5037 conflicts and accidental interference with physical devices |
| **Process Management** | **Win32 Job Object API** (`SetInformationJobObject`) | Kernel-enforced `KILL_ON_JOB_CLOSE` ensures zero emulator orphan processes on termination |
| **Storage Architecture** | **`D:\OrcusAPK`** (SDK/AVD) & **`D:\OrcusDev`** (venv/cache) | Pure ASCII paths isolated from Google Drive synchronization and Windows Defender real-time scanning |

---

## 📊 Current Progress & Status (v0.1.13 / Phase 4a Keep-State, Camera & Sensor Extensions Completed)

The project has achieved **100% completion of Phase 1 (Architecture Specification)**, **Phase 2 (Setup Automation, gRPC Subsystems, Empirical Spikes S0a–S9)**, **Phase 3a (Core Execution Runtime, Primary PySide6 GUI, Real-Device Acceptance, Security Audit & Zero-Pollution Hardening)**, **Phase 3b (Sensor Integration, Direct On-Screen Korean IME Composition, Settings Dialog, Non-Invasive Mouse Wheel Scrolling, Runtime Orientation Sync & Acceptance Matrix B3, B5, B6, B13, B14)**, and **Phase 4a (Keep-State A/B Slots, Camera Image Injection, GPU Fallback Ladder, Two-Finger Pinch, Light/Proximity/Battery Control, SMS/Call Inbound Simulation, Real-time Vibration Pill, Media Drag-and-Drop, Installed App Manager, and One-Click Diag Bundle)**, backed by **457 automated unit tests** and strict Pyright type validation.

```mermaid
flowchart LR
    A["Phase 1: Architecture & Review<br/>(Completed, 2026-10-30)"] --> B["Phase 2: Skeleton, Setup & Spikes<br/>(Completed: v0.1.4)"]
    B --> C["Phase 3a: Runtime, Security & Hardening<br/>(Completed: v0.1.8)"]
    C --> D["Phase 3b: Sensors & Direct IME<br/>(Completed: v0.1.12)"]
    D --> E["Phase 4a: Keep-State, Camera & Extensions<br/>(Completed: v0.1.13)"]
    E --> F["Phase 4b: Inno Setup, English UI & Release<br/>(Next Milestone)"]
    style A fill:#2d6a4f,stroke:#1b4332,color:#fff
    style B fill:#2d6a4f,stroke:#1b4332,color:#fff
    style C fill:#2d6a4f,stroke:#1b4332,color:#fff
    style D fill:#2d6a4f,stroke:#1b4332,color:#fff
    style E fill:#2d6a4f,stroke:#1b4332,color:#fff
    style F fill:#1d3557,stroke:#457b9d,color:#fff
```


### 🏆 Phase 4a Acceptance Verification (Real-Device Measurements)

All Phase 4a features were verified on the real headless Android 16 (API 36) emulator with hardware acceleration (`tests/device/test_acceptance_4a.py`, `tests/device/test_extensions_4a.py`, and 457 unit tests):

| Feature / Criteria | Acceptance Target | Empirical Measurement & Verification | Status |
|---|---|---|:---:|
| **Keep-State (A/B Slots)** | Multi-session app/data preservation; fast recovery from corrupted slot | Normal shutdown saves to A/B slot (`state/slots/a`, `state/slots/b`); quick shutdown bypasses save. Broken/corrupted slot falls back to clean golden snapshot in **14.3s** while preserving broken slot for analysis. Restores saved orientation, light, and battery on boot | **PASS** |
| **GPU Fallback Ladder** | Automated recovery from driver crashes or RDP sessions | Automatic ladder sequence: `host` → `host_hidden` → `software` with verification boot; status persisted in `state/gpu.json`. Temporary remote desktop (RDP) sessions do not overwrite local GPU choice. One-click `[GPU 가속 다시 시도]` in Settings | **PASS** |
| **Camera Injection** | Dynamic image feed without emulator restart | Headless camera enabled via `-camera-back imagefile:<path>` (golden snapshot rebuild key `camera`). Camera app displays image in real time; switching image file in `[센서 > 카메라]` updates preview immediately on re-opening camera | **PASS** |
| **Two-Finger Pinch** | Multi-touch zoom in WebViewer / Canvas | `Ctrl + Mouse Drag` simulates symmetric two-finger pinch. WebViewer page measured spread **3.67x** and pinch **0.27x** (multi-touch count = 2) | **PASS** |
| **Light & Proximity Sensors** | Physical model injection for AI2 sensor components | LightSensor tracks lux changes in **2.3–2.8s** (`LightChanged`); ProximitySensor fires `ProximityChanged` (0.0 cm / 1.0 cm) | **PASS** |
| **Battery Guard (§9.8)** | Prevent Android SystemUI crash from low battery | Unplugged battery level clamped to **21% minimum** (prevents `LowPowerWarningsController` crash and rescue party in Android 16); charging mode supports 0–100% | **PASS** |
| **SMS & Phone Inbound** | Simulated inbound carrier messaging and phone calls | `sendSms`: `MessageReceived` event received in **6.6–9.4s** ("010-1234-5678", 70-char Korean message); `sendPhone`: simulated incoming call ring & hangup | **PASS** |
| **Real-time Vibration Pill** | Visual feedback for physical phone vibration | VIBRATE permission apps poll `dumpsys vibrator_manager` every 1.5s; floating "진동" pill overlay surfaces on mobile screen within **0.1–1.6s** | **PASS** |
| **Media File Drag & Drop** | Instant photo, audio, and video ingestion | Dropping `.jpg`, `.png`, `.mp3`, `.mp4` stages via ASCII temp files and pushes to `/sdcard/Pictures|Music|Movies/` in **2.4s**; indexed by Android MediaStore automatically | **PASS** |
| **Installed App Manager** | Inspect, launch, and uninstall secondary packages | [앱] panel displays `pm list packages -3`; 1-click `[실행]` pulls APK and launches in **1.1s** (skipping re-installation for matching epoch); 1-click `[지우기]` uninstalls cleanly | **PASS** |
| **One-Click Diag Bundle** | Zero-leakage diagnostic zip for support | `orcus-cli diag` and GUI `[진단 묶음 저장]` package logs, status, doctor output, and system metadata into a sanitized `.zip` archive (masking usernames, PC names, IP, and emails) | **PASS** |

### 🏆 Phase 3b Acceptance Verification (Real-Device Measurements)

All Phase 3b acceptance criteria were executed and verified on the real headless Android 16 (API 36) emulator with hardware acceleration (`tests/device/test_acceptance_3b.py`, `tests/unit/test_shake.py`, `tests/unit/test_gui_3b.py`, and the 11-step automated smoke suite):

| Criteria | Acceptance Target | Empirical Measurement & Verification | Status |
|---|---|---|:---:|
| **B3** | Sensor Shake & TextToSpeech integration | Shake click: **10/10** across Weak/Moderate/Strong in portrait; Moderate **10/10** across reverse portrait, landscape, and reverse landscape. TalkToMe: OrcusProbe Korean TTS ("안녕하세요", `AfterSpeaking=true`), TalkToMe English TTS ("Stop shaking me!" 0.4s post-shake, `InternalTTS speak called`) | **PASS** |
| **Stepwise Rotation (§9.7)** | Zero false shake triggers during rotation | 15° stepwise rotation (0.1s step) across 6 turns produced **0 false shakes** (Strong sensitivity, 13s total). Instant 90° rotation triggered 1 false shake (confirming App Inventor accelerometer algorithm model) | **PASS** |
| **B5** | Multi-orientation touch precision & secondary screen sync | Canvas tap error **≤ 4px** across all 4 postures (portrait 0.0px, landscape 1.2px, reverse portrait 1.2px, reverse landscape 0.8px). Secondary landscape screen (`Screen2`): orientation mismatch banner surfaced in **2.7s** with 1-click `[화면 방향 맞추기]`; post-sync canvas tap error 0.0px | **PASS** |
| **B6** | Direct on-screen Korean IME composition & clipboard paste | **Direct on-screen IME typing**: Windows IME composes directly on mobile screen (`WA_InputMethodEnabled`). Finished syllables pasted via input queue; preedit syllables displayed in floating bubble overlay; Hanja candidate window anchored at `ImCursorRectangle`. **B6 Paste verified**: TextBox ("안녕하세요 Orcus"), MultiLine ("안녕하세요\nOrcus"), NumbersOnly ("123"). Floating composition bar overlay (Ctrl+T, [가]) preserved for multiline/long text | **PASS** |
| **B13** | GPS location injection, microphone privacy & tilt | Default Seoul City Hall injected in **7.8s**; Busan City Hall in **2.2s**; N Seoul Tower in **3.1s** (budget ≤ 10s); triple re-injection on `onResume`. Microphone privacy persisted per-package (`privacy.mic_allowed_packages`). Tilt pad tracks OrientationSensor pitch (-90° → -75°) and roll | **PASS** |
| **B14** | Settings dialog & clean-close data loss warning | Unified Settings dialog (`gui/settings_dialog.py`); clean close warning for GOLDEN mode apps with "Do not ask again" checkbox. GPU mode switch initiates golden snapshot rebuild confirmation | **PASS** |
| **Mouse Wheel** | Vertical page scroll without canvas touch drag interference | Form scrolled **256px** (2 notches down) via `cmd input mouse scroll ... --axis VSCROLL,n`; canvas drag count remained 0 when scrolling over Canvas (user verified) | **PASS** |
| **Smoke Test (v0.1.9+)** | 11-step end-to-end automated verification | All 11 steps **PASS**: shake 0.9s, 90° rotation 0.7s (display `ROTATION_90`), GPS 0.7s, reset 7.0s, clean stop 1.6s | **PASS** |
| **Phase 3a Regression** | Zero regressions across Phase 3a real-device suite | 18 of 18 tests **PASS**: B1 warm drop 1.8–2.6s, start+drop 16.3s, B2 PaintPot latency 77–80ms, frame age p95 6.6ms, pointer release guarantee, crash recovery | **PASS** |

### 🏆 Phase 3a Acceptance Verification (Real-Device Measurements)

All Phase 3a acceptance criteria were executed and verified on the real headless Android 16 (API 36) emulator with hardware acceleration (`tests/device/test_acceptance_3a.py`, `test_apk_real.py`, `test_engine_real.py`, packaged runtime suites, and manual §12.6 verification):

| Criteria | Acceptance Target | Empirical Measurement & Verification | Status |
|---|---|---|:---:|
| **B1** | Drop to running app ≤ 10s (warm), ≤ 20s (cold boot) | **1.8–4.9s** drop-to-running across sample apps (`HelloPurr`, `TalkToMe`, `PaintPot`, `BallBounce`, `MoleMash`, `ElementQuiz`); App Card in **0.03–0.08s**; start+drop in **14.1–14.4s** | **PASS** |
| **B2** | Low-latency canvas touch, drag line continuity, frame age ≤ 50ms | PaintPot: **0 broken line columns**, mouse-to-screen latency **88–100ms**, frame age p95 **7.7ms**, **44 fps** during active drag. BallBounce: mouse fling 1,000 → 3,000 px/s tracked to ball 91 → 284 px/s. OrcusProbe: 300 px drag produced **27 Dragged events** | **PASS** |
| **B4** | Hardware bezel key navigation | **Back** → `BackPressed` event; **Home** → Android launcher; **Recents** → system overview deck | **PASS** |
| **B7** | Win32 Job Object process cleanup, zero stuck pointer on abort | Process killed mid-drag: **0 SDK child processes** remaining after 3s; subsequent app launch starts clean with no residual stuck finger | **PASS** |
| **B8** | File classification, Korean path support, orientation pre-rotation, minSdk 36 | Appropriate Korean error dialogs for ARM-only, AAB, AIA, AIX, and corrupted files. Local Korean paths executed in **1.6s** ("고양이 쓰다듬기"). Fixed landscape launcher rotated before launch (`LANDSCAPE`, `ROTATION_90`, 1280×720). Verified **`minSdk 36`** (`HelloPurrMin36.apk`) runs in **4.4s** (`minSdk=36 targetSdk=36`). Google Drive streaming folder (`G:\내 드라이브`) Korean APK executed in **3.5s** | **PASS** |
| **B9** | CPU & memory footprint | Idle window host CPU **0.31%** (single core, 0.02% total system on 16 cores); active BallBounce animation **35–47 fps**; GUI working set **174–179 MB**. Total idle RAM calibrated to **~3.3 GB** (QEMU unshared commit ~3.5 GB + guest RAM 2,560 MB minimum + GUI ~175 MB) | **PASS** |
| **B11** | Multi-instance mutex, crash recovery, software GPU fallback | Second session rejected with `ENGINE_IN_USE` while first VM stays `READY`. Process kill recovers via `CRASH_RESTART` in **16.4–18.5s** with auto-reinstallation of last app. Software GPU cold boot in 131–147s with warning banner; host golden rebuild in 264–354s | **PASS** |
| **B12** | APK lifecycle & staging isolation | Clean staging under `D:\OrcusAPK\staging`, LRU eviction, and `adb install -r` bypass | **PASS** |
| **B15** | Fast logoff termination & single-instance handover | Packaged app responds to `WM_QUERYENDSESSION`/`WM_ENDSESSION` with clean termination in **2.83s** (well within Windows 5s budget), 0 SDK processes left. CLI hand-off to running window completes in **0.7s**. **Real Windows Logoff verified** after ~17m session (stops in **1.2s**, 0 SDK processes, 0 WER crash dumps, snapshot intact, discovery/netsim purged) | **PASS** |
| **Packaging Smoke** | Standalone PyInstaller distribution (`tools/build.py`) | Build time 44–66s, distribution folder **121.9 MB**. Selftests pass (`orcus-cli.exe` & `OrcusAPK.exe`). `--boot-check` passes WHPX host/host_hidden. `--gui-check` launches packaged window, reaches `READY` in **8.5–8.8s**, and terminates cleanly | **PASS** |

### 🔍 Key Engineering Findings & Stabilizations (Findings 7–26)

During real-device acceptance runs and stress scenarios, critical edge cases were discovered and resolved:

* **Finding 26 — Direct On-Screen Korean IME Composition (Pulled Forward from Phase 4)**: Following desktop user testing where typing in the bottom bar was functional but direct on-screen text entry was requested, direct Windows IME composition was enabled on `ScreenView` via `WA_InputMethodEnabled`. When the user switches to Korean via the `한/영` key on the phone canvas, Windows IME handles key strokes directly; completed syllables (`commitString`) are piped into the touch/key input queue via `paste_text` (preserving spacing and Enter key ordering), while composing syllables (`preeditString`) and Hanja candidates appear in an on-screen floating bubble overlay positioned above the guest navigation bar. Tapping the screen while composing auto-commits the in-flight syllable to the active field.
* **Finding 20 — App Inventor Accelerometer Algorithm & 15° Stepwise Rotation**: App Inventor's `AccelerometerSensor.java` maintains a 10-sample circular buffer and evaluates `|mean - sample|` across X, Y, Z axes against thresholds (Weak=5, Moderate=13, Strong=20) with a 400ms debounce. An instant 90° or 180° rotation triggers false shakes (e.g. Strong triggers on instant 90°). Orcus implements a pure waveform stepwise 15° rotation per 0.1s in `device/shake.py` and `device/controls.py`, keeping sample deviations below 3.4 m/s² and yielding exactly **0 false shakes** across 6 consecutive rotations.
* **Finding 21 — Non-Invasive Mouse Wheel Scrolling via `cmd input mouse scroll`**: While gRPC `injectWheel` failed to produce scroll events in S7, executing `cmd input mouse scroll x y --axis VSCROLL,n` via background worker thread produces 128px scroll per notch. When scrolling over an App Inventor Canvas, the canvas receives zero touch or drag events, allowing natural form scrolling without drawing unwanted lines.
* **Finding 22 — In-App Secondary Screen Orientation Mismatch Detection**: MIT App Inventor secondary screens (e.g., `Screen2`) can specify a fixed orientation different from `Screen1`. Orcus monitors guest activity orientation every 3s (1.2s post-launch); upon detecting a mismatch twice consecutively, it surfaces a non-blocking banner with a 1-click `[화면 방향 맞추기]` button, completing rotation in 2.7s.
* **Finding 23 — Triple GPS Re-Injection on App Resume (`onResume`)**: Android mock location providers only dispatch updates when new locations are submitted. If an app registers location listeners after launch or resumes from background, static initial coordinates are missed. Orcus automatically re-transmits the active GPS coordinates 3 times (1s interval) on app launch and `onResume`.
* **Finding 24 — Named Pipe Impersonation Defense via SID Authentication**: To prevent local named pipe spoofing on multi-user systems, `gui/instance.py` inspects the server process ID token SID via `win32.pipe_server_sid` before forwarding file arguments, ensuring the recipient belongs strictly to the identical Windows user account.
* **Finding 25 — Fast Typing Clipboard Queue Coalescing & Order Preservation**: Rapid typing or consecutive paste events queued in `device/input.py` are coalesced into a single atomic clipboard injection with a 100ms throttle before keypress, guaranteeing zero character reordering. Input stream generation IDs ensure that gRPC stream reconnects discard stale iterator pulls without dropping user keystrokes.

* **Finding 20 — App Inventor Accelerometer Algorithm & 15° Stepwise Rotation**: App Inventor's `AccelerometerSensor.java` maintains a 10-sample circular buffer and evaluates `|mean - sample|` across X, Y, Z axes against thresholds (Weak=5, Moderate=13, Strong=20) with a 400ms debounce. An instant 90° or 180° rotation triggers false shakes (e.g. Strong triggers on instant 90°). Orcus implements a pure waveform stepwise 15° rotation per 0.1s in `device/shake.py` and `device/controls.py`, keeping sample deviations below 3.4 m/s² and yielding exactly **0 false shakes** across 6 consecutive rotations.
* **Finding 21 — Non-Invasive Mouse Wheel Scrolling via `cmd input mouse scroll`**: While gRPC `injectWheel` failed to produce scroll events in S7, executing `cmd input mouse scroll x y --axis VSCROLL,n` via background worker thread produces 128px scroll per notch. When scrolling over an App Inventor Canvas, the canvas receives zero touch or drag events, allowing natural form scrolling without drawing unwanted lines.
* **Finding 22 — In-App Secondary Screen Orientation Mismatch Detection**: MIT App Inventor secondary screens (e.g., `Screen2`) can specify a fixed orientation different from `Screen1`. Orcus monitors guest activity orientation every 3s (1.2s post-launch); upon detecting a mismatch twice consecutively, it surfaces a non-blocking banner with a 1-click `[화면 방향 맞추기]` button, completing rotation in 2.7s.
* **Finding 23 — Triple GPS Re-Injection on App Resume (`onResume`)**: Android mock location providers only dispatch updates when new locations are submitted. If an app registers location listeners after launch or resumes from background, static initial coordinates are missed. Orcus automatically re-transmits the active GPS coordinates 3 times (1s interval) on app launch and `onResume`.
* **Finding 24 — Named Pipe Impersonation Defense via SID Authentication**: To prevent local named pipe spoofing on multi-user systems, `gui/instance.py` inspects the server process ID token SID via `win32.pipe_server_sid` before forwarding file arguments, ensuring the recipient belongs strictly to the identical Windows user account.
* **Finding 25 — Fast Typing Clipboard Queue Coalescing & Order Preservation**: Rapid typing or consecutive paste events queued in `device/input.py` are coalesced into a single atomic clipboard injection with a 100ms throttle before keypress, guaranteeing zero character reordering. Input stream generation IDs ensure that gRPC stream reconnects discard stale iterator pulls without dropping user keystrokes.

* **Finding 7 — Golden Snapshot Guest Framework Preservation**: Saving golden snapshots while the guest is compiling dex in the background (load average ~12) caused Android's `system_server` watchdog to terminate on subsequent loads (`cmd package` Broken pipe, `dumpsys power` DEAD_OBJECT).  
  *Fix*: Added guest CPU idle polling via `/proc/stat` (`cpu_busy` < 0.35 twice consecutively, up to 180s) and guest framework verification (`framework_problem` verifying `system_server`, `cmd package`, and `dumpsys power`) before snapshot creation and during session initialization.
* **Finding 8 — Autonomous ADB Server Watchdog (`engine-adbwait`)**: If the private ADB server (port 15037) exited unexpectedly during `READY`, the emulator connection was lost.  
  *Fix*: Added dedicated `engine-adbwait` thread supervisor in `EngineManager` with worker auto-recovery job, recovering `emulator-5640` in **0.5–0.6s**.
* **Finding 9 — Stuck Touch Prevention on Stream Restart**: An interrupted gRPC input stream could leave a finger in a pressed state inside the guest.  
  *Fix*: Enforced unconditional pointer lift (`buttons=0`) at the very beginning of every new input stream.
* **Finding 10 — PyInstaller Packaging Integrity**: Added missing `PySide6.QtNetwork` (required for single-instance IPC) and `orcus.gui.app` to packaging hidden imports; improved SDK process detection via image directory filter (`D:\OrcusAPK\sdk`) to ignore desktop Google Drive crashpad daemons.
* **Finding 11 — BallBounce Gesture Calibration**: Adjusted fling test gesture duration (0.25s and 0.08s) to accurately trigger Android fling physics.
* **Finding 12 — App Inventor `.aia` Manifest AST Tuning & minSdk 36 Verification**: Injected `AndroidMinSdk` attribute directly into Screen1 `.scm` AST specifications; verified on real device with rebuilt `HelloPurrMin36.apk` where `minSdk=36 targetSdk=36` installed and executed cleanly on the API 36 emulator.
* **Finding 13 — Activity Launch Telemetry**: Added diagnostic warning logs capturing `am start -W` raw output on slow launches (> 10s) and command retries.
* **Finding 14 — QEMU Shutdown Crash Dump Elimination (WER `combase.dll` 0xc0000602)**: Normal shutdown via `setVmState(SHUTDOWN)` caused QEMU to intermittently crash on exit in `combase.dll`, leaving crash dumps in Windows Error Reporting (`C:\ProgramData\WER`).  
  *Fix*: Restructured shutdown sequence to "pause VM (flushes guest disk) -> terminate QEMU process -> delete discovery files". Clean stop time reduced to **0.7–0.9s** with **zero WER crash reports**.
* **Finding 15 — Zero-Registry Footprint File Dialogs (`orcus.gui.dialogs`)**: Standard `QFileDialog` invocations permanently pollute `HKCU\Software\QtProject\OrganizationDefaults\FileDialog` with recent folders and history. Implemented `keeping()` registry context manager in `gui/dialogs.py` to restore original registry state and purge any created keys upon dialog close, enforcing ARCHITECTURE §11.5.
* **Finding 16 — Snapshot Load Counter Semantics**: The emulator updates `snapshot.pb` field 11 (load counter) on each successful snapshot load; the underlying snapshot binary images (`ram.bin`) remain completely unchanged.
* **Finding 17 — Drive C: Whitelist Additions**: Added `%LOCALAPPDATA%\Temp\netsim.ini*` (temporary socket config created during netsimd execution) and PowerShell startup profile cache to ARCHITECTURE §11.5 whitelist.
* **Finding 18 — Headless RDP Disconnect GPU Snapshot Deletion Safeguard**: Launching the emulator in a disconnected Remote Desktop session causes `EnumDisplayDevices` to return 0, prompting the emulator to detect "host GPU has changed" and destroy the golden snapshot.  
  *Fix*: Added PREFLIGHT display device check via Win32 `EnumDisplayDevices`; if 0 displays are detected and a GPU snapshot exists, launch once in `COLD` mode with warning banner `ENGINE_NO_DISPLAY` ("이번만 느린 시작"), avoiding golden snapshot deletion and the 5-minute rebuild cycle.
* **Finding 19 — Provisioning Status Message Key Persistence**: Ensured progress updates during provisioning retain the active message key instead of reverting to default text.
* **Readonly Status Synchronization**: `EngineManager` now writes live state, message key, epoch, and mode to `engine_owner.json` before signaling waiters; `readonly.py` displays live owner status (e.g. "휴대폰 마무리 준비 중…") rather than just gRPC booted state.

### 🛡️ Phase 3a Security & Robustness Audit (41 Fixes)

A comprehensive adversarial code and security audit covering setup, APK pipeline, engine, and GUI IPC resolved 41 potential vulnerabilities and edge cases:

* **Folder Permission Lock (`common/acl.py`)**: Locked `D:\OrcusAPK` and `D:\OrcusDev` permissions strictly to the current user, SYSTEM, and Administrators (stripping inherited `Authenticated Users: Modify` from the `D:` drive root to prevent DLL/binary substitution).
* **Zip Bomb & Malicious Archive Defenses**: Replaced unbounded unzipping with size-capped streaming reads for manifests, assets, and icons; restricted compression methods strictly to `STORED` (0) and `DEFLATED` (8), rejecting BZIP2/LZMA attacks; enforced a maximum limit of 50,000 files per archive.
* **Discovery PID Reuse Protection**: Orphan process cleanup validates that target PIDs correspond specifically to `qemu-system-x86_64-headless.exe` located under `D:\OrcusAPK\sdk`, preventing termination of reused PIDs belonging to other system applications.
* **HTML Injection Mitigation**: App labels, error messages, and crash banners are rendered strictly as plain text, eliminating SMB `<img>` and `file://` hyperlink injection risks.
* **Executable Path Hardening**: System commands (`explorer.exe`, `netstat`) execute via absolute paths from `win32.system_dir()`. PowerShell scripts receive paths via environment variables rather than string interpolation.
* **IPC Socket Validation**: Received file paths must exist on local drives (rejecting remote UNC paths, 64 KiB length cap); clipboard text injection capped at 64 KiB.
* **Resilient State Persistence (`common/jsonio.py`)**: Malformed or ANSI-encoded JSON files are safely quarantined to `.corrupt` and replaced with defaults; state writes utilize atomic rename and disk sync (`fsync`).
* **Display DPI Resilience**: Window scaling changes across multi-monitor setups with different DPIs (100%, 125%, 150%) maintain correct 1:1 viewport aspect ratios and accurate touch event translation.

---

### ✅ Implemented Capabilities (v0.1.9)

#### 1. Architecture Specifications ([docs/](docs/))
* **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Updated with §5.1 shutdown sequence (WER crash elimination), §5.5/§6.5 framework validation, `engine-adbwait` supervisor thread, 60s post-load health verification, §9.3 non-invasive mouse wheel scrolling (`cmd input mouse scroll`), §9.7 stepwise 15° rotation, §11.4 ACL permissions, and §11.5 zero-registry file dialog specifications.
* **[docs/spikes.md](docs/spikes.md)**: Empirical measurement logs covering all Phase 2 spikes (S0a–S9), Phase 3a acceptance runs, and Phase 3b sensor / wheel measurements.
* **[docs/fixtures.md](docs/fixtures.md)**: Full fixture specifications for `HelloPurr`, `TalkToMe`, `OrcusProbe`, `PaintPot`, `BallBounce`, `MoleMash`, `HelloPurrKo`, `HelloPurrMin36`, and `HelloPurrLand`.
* **[plan.md](plan.md)**: Master phase plan with complete B1–B15 real-device acceptance results, empirical footprint logs, and Phase 3b acceptance matrix records.

#### 2. Clean Architecture Contracts (`src/orcus/contracts/`)
* [`apk.py`](src/orcus/contracts/apk.py): APK classification (`APK`, `AAB`, `AIA`, `AIX`, `UNKNOWN`), manifest extraction dataclasses, pre-flight check models, and execution contracts.
* [`device.py`](src/orcus/contracts/device.py): Frame stream protocol (RGB888 buffers), mouse/touch/key event contracts, scroll sink (`ScrollSink`), sensor injection, posture tracking, and logcat filtering.
* [`engine.py`](src/orcus/contracts/engine.py): Emulator state machine (`STOPPED`, `PREPARING`, `READY`, `STOPPING`, `FAILED`), ownership locks, port allocation, snapshot tokens, and `EngineView` interface.
* [`errors.py`](src/orcus/contracts/errors.py): Unified `ErrorCode` definitions and `OrcusError` exception hierarchy.
* [`session.py`](src/orcus/contracts/session.py): Non-blocking Session API protocol coordinating GUI, APK engine, and emulator controllers.
* [`setup.py`](src/orcus/contracts/setup.py): Step-by-step setup definitions, progress callbacks, and system doctor diagnostic reports.

#### 3. Core Engine Management & Supervision (`src/orcus/engine/`)
* [`manager.py`](src/orcus/engine/manager.py): Robust `EngineManager` implementing the core state machine, `engine-adbwait` server watcher with auto-restart, framework problem validation, headless display fallback (`ENGINE_NO_DISPLAY`), quiet CPU pre-save checks, and safe QEMU shutdown.
* [`adb.py`](src/orcus/engine/adb.py): Private isolated ADB daemon supervisor (port 15037), `AdbServer.wait()` exit detector, slow command telemetry (`SLOW_CMD_S`), and epoch-bound `EpochShell`.
* [`provision.py`](src/orcus/engine/provision.py): Guest configuration pipeline, `framework_problem` validator (`system_server`, `cmd package`, `dumpsys power`), `cpu_busy` `/proc/stat` sampler, and mock location injection.
* [`snapshots.py`](src/orcus/engine/snapshots.py): Golden snapshot validation (`PROVISION_V=3`), automatic golden rebuild safeguards, and sub-6s instant boot restoration.
* [`gpu.py`](src/orcus/engine/gpu.py) / [`exitlog.py`](src/orcus/engine/exitlog.py) / [`readonly.py`](src/orcus/engine/readonly.py): Hardware GPU fallback detection, crash log parsers, and external read-only discovery inspection synchronized with `engine_owner.json`.

#### 4. APK Lifecycle & Inspection Pipeline (`src/orcus/apk/`)
* [`intake.py`](src/orcus/apk/intake.py): Safe drag-and-drop file ingestion, ASCII staging path isolation (`D:\OrcusAPK\staging`), LRU cache eviction, and zip bomb mitigations (50k entry limit, STORED/DEFLATED compression check).
* [`inspect.py`](src/orcus/apk/inspect.py): High-performance pure-Python manifest parser (using `apkInspector`), handling UTF-16 string pools, orientation numbers, component discovery, and size-capped streaming reads for icons and assets.
* [`compat.py`](src/orcus/apk/compat.py) / [`eras.py`](src/orcus/apk/eras.py): Pre-flight compatibility diagnostics detecting target SDK mismatches, missing speech recognizers, and permission anomalies.
* [`install.py`](src/orcus/apk/install.py): Intelligent `adb install -r` coordinator with bypass flags for legacy packages, exact error code extraction (`Failure [CODE]`), and zero-cost in-epoch installation skips.
* [`launch.py`](src/orcus/apk/launch.py): Pre-launch automatic screen orientation rotation (`LANDSCAPE`, `REVERSE_LANDSCAPE`), `am start -W` activity invocation, and slow launch telemetry.
* [`watcher.py`](src/orcus/apk/watcher.py): Real-time crash, ANR, and App Inventor component error detector (harvesting error 1501, 601, 908, 2701, 3806, and SIGTRAP).
* [`service.py`](src/orcus/apk/service.py): Dedicated `ApkService` thread managing the asynchronous APK lifecycle pipeline, runtime secondary screen orientation mismatch detection (emitting `APP_ORIENTATION_MISMATCH` with 1-click sync), per-app microphone privacy persistence (`privacy.mic_allowed_packages`), and cold boot fresh app notices.

#### 5. Device gRPC Streaming & Hardware Adapters (`src/orcus/device/`)
* [`rotation.py`](src/orcus/device/rotation.py): 4-orientation mathematical transform matrix mapping mouse inputs to guest display coordinates.
* [`frames.py`](src/orcus/device/frames.py): High-throughput RGB888/RGBA8888 frame buffer receiver and viewport sizing adapter.
* [`input.py`](src/orcus/device/input.py): Touch pointer event normalizer with stream generation ID tracking (guaranteeing finger release and discarding stale iterator pulls on stream reconnect), mouse wheel scroll sink (`cmd input mouse scroll`), and clipboard paste coalescing (100ms throttle).
* [`shake.py`](src/orcus/device/shake.py): Pure waveform generator for shake acceleration pulses (calibrated against MIT App Inventor `AccelerometerSensor.java` 10-sample buffer thresholds) and 15° stepwise rotation (0.1s step) avoiding false shake triggers.
* [`controls.py`](src/orcus/device/controls.py): Sensor and device controls supporting 3-level shake sensitivity pulses, 15° stepwise rotation, 20° tilt pad, mic mute/unmute, and triple GPS coordinate re-injection on app launch and `onResume`.
* [`logcat.py`](src/orcus/device/logcat.py): Streamlined Logcat monitor utilizing hybrid ADB/gRPC streams to bypass emulator goldfish logcat disconnections.
* [`session.py`](src/orcus/device/session.py): Integrated `DeviceSession` managing all gRPC stream lifetimes.

#### 6. PySide6 Native Desktop GUI Client (`src/orcus/gui/`)
* [`dialogs.py`](src/orcus/gui/dialogs.py): Registry-preserving `open_file` and `save_file` dialog wrappers enforcing ARCHITECTURE §11.5 zero-pollution policy (reverts or purges `HKCU\Software\QtProject\OrganizationDefaults\FileDialog` on dialog close).
* [`settings_dialog.py`](src/orcus/gui/settings_dialog.py): Unified Settings dialog (GPU mode selection with golden snapshot rebuild confirmation, clean-close data loss warning toggle, log level) replacing `gpu_dialog.py`.
* [`sensors.py`](src/orcus/gui/sensors.py): Collapsible [Sensors] drawer (shake sensitivity buttons with ~0.5s auto-repeat, 15° gradual rotation, 20° tilt pad with double-click level reset, GPS presets with coordinate hint, and microphone toggle).
* [`hangul.py`](src/orcus/gui/hangul.py): Korean IME composition floating input bar (Enter to paste, Shift+Enter for multiline newline, Esc dismiss, with guest clipboard warning tooltip) for long text and multiline editing.
* [`window.py`](src/orcus/gui/window.py): Native desktop window housing the 9:16 mobile frame (432×768 on primary 1080p displays), system tray, logoff fast-shutdown handlers, multi-monitor DPI scaling, clean-close confirmation dialog, and zero-registry APK open / screenshot save dialogs.
* [`phone.py`](src/orcus/gui/phone.py) / [`screen.py`](src/orcus/gui/screen.py) / [`frame_pump.py`](src/orcus/gui/frame_pump.py): 1:1 direct low-latency raster blit rendering viewport with direct on-screen Korean IME input (`WA_InputMethodEnabled`, `commitString` paste queue, bottom floating preedit bubble, Hanja candidate window positioning), touch input handler, bezel buttons (Back, Home, Recents, Rotate CCW F7, Rotate CW F8, Shake F6, Korean IME [가] Ctrl+T) with drag-to-cancel support, and non-invasive mouse wheel scrolling without canvas distortion.
* [`keymap.py`](src/orcus/gui/keymap.py): Keyboard navigation mapping (Esc/Right-click = Back, Middle-click = Home, Ctrl+V = Korean text injection, F6 = Shake, F7/F8 = Rotation, Ctrl+T/[가] = Korean IME multiline bar, and native Windows IME `한/영` toggling on phone screen).
* [`panels.py`](src/orcus/gui/panels.py): Collapsible [App Card] (displaying package, launcher, SDK, permissions), [Sensors] drawer, and [Diagnostic Logs] inspection drawers with plain-text rendering and clean text export dialogs.
* [`overlays.py`](src/orcus/gui/overlays.py): Rich state overlays, non-blocking toast notifications, orientation mismatch warning banners (`[화면 방향 맞추기]`), and engine failure recovery cards.
* [`instance.py`](src/orcus/gui/instance.py): Single-instance enforcement via Win32 Named Mutex and local QtNetwork IPC socket forwarding with local-path validation, named pipe SID authentication (`win32.pipe_server_sid`), and graceful shutdown mutex wait (up to 20s).

#### 7. Session Composition & CLI Runner (`src/orcus/app/`, `src/orcus/cli.py`)
* [`session.py`](src/orcus/app/session.py): Unifies `EngineManager`, `ApkService`, and `DeviceSession` into the thread-safe `SessionApi` contract.
* [`smoke.py`](src/orcus/app/smoke.py): 11-step end-to-end automated smoke testing harness validating the entire launch-tap-shake-rotate-GPS-reset lifecycle.
* **CLI Commands**:
  - `orcus-cli run <apk>`: Instantly loads golden snapshot, installs, and launches target APK from console.
  - `orcus-cli smoke --apk <apk>`: Executes full automated 11-step Phase 3b verification suite.
  - `orcus-cli engine status|shot`: Queries live emulator status and captures headless screenshots.

#### 8. Automated Setup & Verification Engine (`src/orcus/setup/`, `src/orcus/common/`)
* Robust Win32 Job Object wrappers (`winjob.py`, `win32.py`) enforcing `KILL_ON_JOB_CLOSE`.
* [`acl.py`](src/orcus/common/acl.py): NTFS access control lock (`lock_to_user`) securing `D:\OrcusAPK` and `D:\OrcusDev` exclusively to the current user, SYSTEM, and Administrators.
* [`jsonio.py`](src/orcus/common/jsonio.py): Atomic, fsync-enforced JSON serializer with automatic corrupted-file isolation (`.corrupt`).
* Multi-step setup pipeline (`steps.py`, `download.py`, `extract.py`, `license.py`, `whpx.py`).
* Resilient archive decompression with Windows Defender lock retry logic and `.partial` markers.

#### 9. Developer Toolchain & Testing Infrastructure (`tools/`, `tests/`)
* **AI2 Project Synthesis Engine (`tools/ai2/`)**: Visual `.bky` XML and `.yail` Scheme code generator, producing valid `.aia` project archives (`HelloPurr`, `TalkToMe`, `OrcusProbe`, `PaintPot`, `BallBounce`, `MoleMash`, `HelloPurrKo`, `HelloPurrMin36`, `HelloPurrLand`).
* [`bootstrap.py`](tools/bootstrap.py) / [`dev.py`](tools/dev.py) / [`build.py`](tools/build.py): Hash-pinned environment manager, test runner, and PyInstaller onedir distribution builder with `--gui-check` validation.
* **Automated Test Suite**: **407 pytest unit tests** passing with 100% success rate (including `test_shake.py`, `test_gui_3b.py`, `test_gui_dialogs.py`, and security audit suites). Zero Pyright type check diagnostics.
* **Real-Device Acceptance Test Suites**:
  - `tests/device/test_acceptance_3a.py`: End-to-end device test harness validating criteria B1, B2, B4, B7, B8, B9, B11, B12, and B15.
  - `tests/device/test_acceptance_3b.py`: Real-device acceptance suite validating criteria B3, B5, B6, B13, B14, and non-invasive mouse wheel scrolling.

---

## 📂 Project Directory Structure

```text
Orcus_APK_Emulator/
├── assets/                     # Project branding & multi-resolution application icons (.ico / .png)
├── docs/                       # Architecture specifications & research documents
│   ├── ARCHITECTURE.md         # Full technical architecture specification (v3)
│   ├── fixtures.md             # Test APK fixture specifications (HelloPurr, TalkToMe, OrcusProbe)
│   ├── spikes.md               # Spike measurement protocols and empirical findings (S0a–S9)
│   └── research/               # Phase 1 domain research briefs
├── src/
│   └── orcus/                  # Core package
│       ├── __init__.py
│       ├── __main__.py         # Application entry point (GUI launcher)
│       ├── cli.py              # orcus-cli setup, doctor, selftest, run, and smoke commands
│       ├── apk/                # APK staging, inspection, installation, launch, and error watcher
│       │   ├── compat.py       # Pre-flight compatibility diagnostics
│       │   ├── eras.py         # Android API era detection
│       │   ├── inspect.py      # apkInspector manifest metadata extraction
│       │   ├── install.py      # ADB package installer with fast in-epoch bypass
│       │   ├── intake.py       # Safe path staging and LRU cache
│       │   ├── launch.py       # Activity launcher and orientation synchronizer
│       │   ├── service.py      # Asynchronous ApkService pipeline
│       │   └── watcher.py      # Real-time logcat error, crash, and ANR telemetry
│       ├── app/                # Session composition and smoke validation
│       │   ├── compose.py      # Application dependency wiring
│       │   ├── session.py      # Unified SessionApi and HeadlessSession implementation
│       │   └── smoke.py        # 11-step end-to-end automated verification harness
│       ├── common/             # OS integration, Win32 Jobs, ACL security, atomic JSON, paths, logging
│       │   ├── acl.py          # Folder ACL lock to user, SYSTEM & Administrators (ARCHITECTURE §11.4)
│       │   └── jsonio.py       # Atomic, fsync-enforced JSON with .corrupt isolation
│       ├── contracts/          # Strict layer protocols and data models (Qt-independent)
│       ├── device/             # gRPC streaming adapter, touch input, controls, logcat
│       │   ├── controls.py     # Motion sensors (shake, tilt), mic mute, and GPS injection
│       │   ├── frames.py       # RGB888 frame buffer queue and format sizing
│       │   ├── input.py        # Touch pointer event normalizer, scroll sink, and clipboard paste
│       │   ├── logcat.py       # Hybrid ADB/gRPC live logcat consumer
│       │   ├── rotation.py     # 4-orientation coordinate transformation
│       │   ├── session.py      # DeviceSession stream coordinator
│       │   └── shake.py        # Pure shake waveform and 15° stepwise rotation generator
│       ├── emugrpc/            # Vendored gRPC stubs, token channel factory, and FakeEmulatorServer
│       ├── engine/             # Headless emulator process, ADB daemon, and golden snapshot controller
│       │   ├── adb.py          # Private isolated ADB daemon (port 15037) supervisor
│       │   ├── bootcheck.py    # WHPX headless boot readiness diagnostics
│       │   ├── exitlog.py      # Process exit code and emulator crash log analysis
│       │   ├── gpu.py          # Host GPU compatibility detection
│       │   ├── manager.py      # EngineManager supervisor state machine
│       │   ├── provision.py    # Guest provisioning pipeline and mock location provider
│       │   ├── readonly.py     # Read-only external VM discovery reader
│       │   ├── seams.py        # Subprocess and dependency injection seams
│       │   ├── selftest.py     # Component integrity self-test
│       │   └── snapshots.py    # Golden snapshot restore and integrity validator
│       ├── gui/                # PySide6 QtWidgets main window and mobile viewport
│       │   ├── app.py          # Native GUI application runner
│       │   ├── bridge.py       # Thread-safe SessionApi event bridge
│       │   ├── dialogs.py      # Registry-neutral QFileDialog keeping wrappers (ARCHITECTURE §11.5)
│       │   ├── frame_pump.py   # Low-latency frame pump
│       │   ├── hangul.py       # Korean IME text composition input bar overlay
│       │   ├── instance.py     # Single-instance mutex, SID check, and IPC forwarder
│       │   ├── keymap.py       # Desktop keyboard shortcuts and bezel key bindings
│       │   ├── overlays.py     # Progress overlays, error banners, and recovery cards
│       │   ├── panels.py       # Collapsible [App Card] and [Diagnostic Logs] panels
│       │   ├── phone.py        # 9:16 mobile frame container with bezel controls
│       │   ├── screen.py       # Direct raster blit rendering viewport
│       │   ├── sensors.py      # Motion, rotation, tilt, GPS & microphone drawer
│       │   ├── settings_dialog.py # Unified settings dialog (GPU, snapshots, warnings)
│       │   └── window.py       # Top-level application window
│       ├── resources/          # Embedded runtime application icons (icon.ico, icon.png)
│       └── setup/              # Resumable downloader, safe extractor, WHPX configuration
├── tests/
│   ├── conftest.py             # Test fixtures, mock servers, and shared assertions
│   ├── fixtures/               # Generated test project archives (HelloPurr, TalkToMe, OrcusProbe)
│   ├── unit/                   # 457 unit test suites covering contracts, engine, apk, device, gui, app
│   └── device/                 # Real emulator device integration test suites (Phase 3a & 3b)
├── tools/                      # Developer toolchain (D: drive cache isolation)
│   ├── ai2/                    # MIT App Inventor AST block DSL and .aia project generator
│   ├── bootstrap.py            # Automated venv creation & dependency installation
│   ├── dev.py                  # Test / typecheck / CLI execution harness
│   ├── devenv.py               # Development environment path management
│   ├── lock.py                 # Dependency hash-lock generation
│   ├── build.py                # Standalone PyInstaller onedir distribution builder
│   ├── gen_protos.py           # Protobuf/gRPC stub compiler
│   ├── fetch_fixtures.py       # Official App Inventor test APK downloader
│   └── spikes/                 # Empirical measurement harnesses (S0a–S9, fx_*.py)
├── NOTICE                      # Upstream license notices and attributions
├── plan.md                     # Master project plan and acceptance criteria (A1–A9, B1–B15)
├── pyproject.toml              # Project metadata, build configuration, and pyright settings
├── requirements.in             # Runtime dependency specifications
├── requirements.lock           # Exact hash-pinned runtime lockfile
├── requirements-dev.in         # Development tool dependency specifications
└── requirements-dev.lock       # Exact hash-pinned development tool lockfile
```

---

## 🚀 Getting Started (Developer Setup)

### System Prerequisites
* **Operating System**: Windows 11 (64-bit)
* **Processor**: x86_64 CPU with Hardware Virtualization (Intel VT-x or AMD-V)
* **Hypervisor**: Windows Hypervisor Platform (WHPX) support
* **Storage**: Minimum 20 GB free space on drive `D:` (for SDK tools, system images, and AVD storage)
* **Python**: Python 3.14 (CPython 3.14.6 recommended)

### 1. Repository Setup & Dependencies
```powershell
# 1. Clone the repository
git clone https://github.com/OrcusExtreme/Orcus_APK_Emulator.git
cd Orcus_APK_Emulator

# 2. Bootstrap virtual environment and dependencies (D:\OrcusDev\venv)
py -3.14 tools/bootstrap.py

# 3. Verify static typing with Pyright
py -3.14 tools/dev.py typecheck

# 4. Run the automated unit test suite (457 tests)
py -3.14 tools/dev.py check
```

### 2. Launching the Application
```powershell
# Launch the native PySide6 Desktop GUI (drag-and-drop any .apk onto the window)
py -3.14 -m orcus

# Or launch directly with an APK file:
py -3.14 -m orcus D:\OrcusDev\fixtures\HelloPurr.apk

# Run the 11-step automated smoke test suite:
py -3.14 -m orcus.cli smoke --apk D:\OrcusDev\fixtures\HelloPurr.apk
```

---

## 🗺️ Roadmap & Milestones

- [x] **Phase 1: Architecture & Technical Foundations** (Completed, 2026-10-30)
- [x] **Phase 2: Skeleton, Setup Automation & Spikes** (Completed: v0.1.4)
  - [x] Project skeleton, contracts, common utilities, and setup pipeline
  - [x] Emulator gRPC protobuf compilation (`orcus.emugrpc`) and FakeEmulatorServer mock
  - [x] App Inventor fixture generator (`tools/ai2/`, HelloPurr, TalkToMe, OrcusProbe)
  - [x] Spike harnesses (`tools/spikes/`) covering S0a–S9 (GPU latency, golden boot, audio, sensors, error telemetry)
- [x] **Phase 3a: Core Execution Runtime & Primary GUI** (Completed: v0.1.5)
  - [x] `engine`: State machine, private ADB daemon watchdog, golden snapshot restore
  - [x] `apk`: Drag-and-drop staging pipeline, `adb install -r`, `am start`, logcat monitors
  - [x] `device`: Low-latency frame streaming, touch pointer normalization, Korean clipboard paste
  - [x] `gui`: PySide6 9:16 mobile frame, raster frame blit, [App Card] and [Logs] panels
- [x] **Phase 3a Acceptance, Security & Stability Hardening** (Completed: v0.1.8)
  - [x] Real-device acceptance suite (`tests/device/test_acceptance_3a.py`) validating criteria B1, B2, B4, B7, B8, B9, B11, B12, B15
  - [x] Real Windows logoff fast shutdown verified (1.2s, 0 SDK processes, 0 WER crash dumps)
  - [x] QEMU termination crash dump elimination in `combase.dll` (Finding 14, ARCHITECTURE §5.1)
  - [x] Headless RDP disconnect GPU snapshot deletion safeguard (Finding 18, `ENGINE_NO_DISPLAY`)
  - [x] Verified `minSdk=36` APK installation and execution on API 36 (`HelloPurrMin36.apk`)
  - [x] Verified Google Drive streaming folder Korean path APK execution (`G:\내 드라이브`, 3.5s)
  - [x] Zero-registry pollution file dialog architecture (ARCHITECTURE §11.5, `orcus.gui.dialogs`)
  - [x] 41 security & robustness fixes (NTFS ACL lock, zip bomb protection, PID validation, HTML sanitization, atomic state JSON)
  - [x] Guest framework preservation & CPU idle check before golden snapshot save (Finding 7)
  - [x] Private ADB daemon autonomous recovery supervisor (`engine-adbwait`, Finding 8)
  - [x] Guaranteed pointer release on gRPC input stream restart (Finding 9)
  - [x] PyInstaller onedir distribution packaging with `--gui-check` validation (Finding 10)
  - [x] 350 automated unit tests passing with 100% success rate & 0 Pyright issues
- [x] **Phase 3b: Sensor Integration, IME, Settings & Android Fusion Branding** (Completed: v0.1.12)
  - [x] Direct on-screen Korean IME composition (`WA_InputMethodEnabled`, `commitString` paste queue, floating preedit bubble, Hanja candidate window, native `한/영` key switching)
  - [x] Multiline Korean IME text composition input bar overlay (Ctrl+T, [가] side button)
  - [x] [Sensors] panel: Shake sensitivity presets (Weak/Moderate/Strong), gradual 15° rotation, 20° tilt pad, GPS presets & injection, microphone toggle
  - [x] Bezel side buttons: Rotate CCW (F7), Rotate CW (F8), Shake (F6 with ~0.5s auto-repeat), and [가] (Ctrl+T) with drag-to-cancel
  - [x] Unified Settings dialog (GPU mode, golden snapshot rebuild, clean-close data loss warning, log level)
  - [x] Mouse wheel vertical scrolling via guest VSCROLL without canvas touch drag interference
  - [x] Runtime secondary screen orientation mismatch detection (Screen2 landscape banner at 2.7s + 1-click sync)
  - [x] Real-device acceptance test suite (`tests/device/test_acceptance_3b.py`) validating criteria B3, B5, B6, B13, B14
  - [x] Official application icon & multi-resolution Windows .ico integration: Orcus geometric monogram fused with iconic Android Bugdroid robot core (assets/, src/orcus/resources/, AppUserModelID taskbar grouping, PyInstaller spec)
  - [x] 408 automated unit tests passing with 100% success rate & 0 Pyright issues
- [x] **Phase 4a: Keep-State, Camera, GPU Ladder & Sensor Extensions** (Completed: v0.1.13)
  - [x] Multi-session state preservation (Keep-State A/B slots with clean golden fallback on corrupted slots in 14.3s)
  - [x] Dynamic headless camera image injection (`-camera-back imagefile`, golden snapshot key `camera`)
  - [x] Automated GPU fallback ladder (`host` → `host_hidden` → `software` with verification boot, `state/gpu.json`)
  - [x] Two-finger pinch gesture simulation (`Ctrl + Mouse Drag` in WebViewer / Canvas, spread 3.67x / pinch 0.27x)
  - [x] Light & Proximity sensor injection (LightChanged 2.3–2.8s, ProximityChanged 0.0/1.0 cm)
  - [x] Android 16 low-battery crash guard (clamped to 21% minimum on non-charging, 0–100% charging)
  - [x] Inbound SMS & Phone call simulation (`sendSms` in 6.6–9.4s, `sendPhone` ring & hangup)
  - [x] Real-time visual vibration pill overlay (`dumpsys vibrator_manager` poller in 0.1–1.6s)
  - [x] Media file drag-and-drop ingestion (`.jpg`, `.png`, `.mp3`, `.mp4` to `/sdcard/` in 2.4s)
  - [x] Installed app package manager in [앱] tab (`pm list packages -3`, 1-click launch in 1.1s, uninstall)
  - [x] One-click diagnostic bundle packager (`orcus-cli diag` and GUI zip export with PII masking)
  - [x] 457 automated unit tests passing with 100% success rate & 0 Pyright issues
- [ ] **Phase 4b: Packaging, English UI & Distribution** (Next Milestone)
  - [ ] Inno Setup standalone user installer (zero admin privileges required)
  - [ ] Bilingual localization (Korean default + English UI switch)
  - [ ] Standalone PyInstaller onedir distribution smoke test & final release packaging

---

## 📄 License & Attribution

* **Proprietary Code**: Core Orcus APK Emulator application code and architecture documents are maintained for proprietary educational and research purposes.
* **Open Source Dependencies**: Third-party libraries (`PySide6-Essentials`, `grpcio`, `protobuf`, `apkInspector`) are used under their respective open-source licenses (LGPLv3, Apache 2.0, MIT).
* **Android SDK**: Google Android Emulator and system image components are subject to the Google Android Software Development Kit License Agreement and are downloaded directly to the local host machine without redistribution.
