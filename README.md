# Orcus APK Emulator

> **Ultra-Lightweight, Instant-Launch Windows 11 Desktop Runner for MIT App Inventor APKs**  
> Powered by Headless Google Android Emulator & Low-Latency Token-Authenticated gRPC

[![Release](https://img.shields.io/badge/Release-v0.1.3-blue.svg)](https://github.com/OrcusExtreme/Orcus_APK_Emulator/releases/tag/v0.1.3)
[![Python](https://img.shields.io/badge/Python-3.14-green.svg)](pyproject.toml)
[![GUI](https://img.shields.io/badge/GUI-PySide6--Essentials%206.12-orange.svg)](pyproject.toml)
[![Hypervisor](https://img.shields.io/badge/Hypervisor-WHPX-purple.svg)](docs/ARCHITECTURE.md)
[![Tests](https://img.shields.io/badge/Tests-120%20Passed-brightgreen.svg)](tests/)
[![Typecheck](https://img.shields.io/badge/Typecheck-Pyright%20Clean-success.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](NOTICE)

---

## 📌 Overview

**Orcus APK Emulator** is a dedicated Windows 11 desktop runner designed specifically for testing educational Android applications built with **MIT App Inventor** (pure DEX bytecode, standard Android View/Canvas, sensor components, and media engines).

Unlike heavy, general-purpose commercial emulators (such as BlueStacks, Nox, or LDPlayer) that suffer from multi-gigabyte memory footprints, bloated user interfaces, bundled ads, and persistent background telemetry, Orcus APK Emulator executes an official **Google Android Emulator** instance in headless mode (`-no-window -gpu host`) atop the **Windows Hypervisor Platform (WHPX)**. It streams frames and injects inputs via a low-latency, token-protected **gRPC API**, providing an instantaneous, native-feeling development testing experience.

### 🎯 Key Highlights
* **Drag-and-Drop Instant Launch**: Drop any `.apk` file directly onto the window; the runtime stages the file, validates the manifest, and displays the running app in **under 10 seconds**.
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

## 📊 Current Progress & Status (v0.1.3 / Phase 2 Completed)

The project has achieved **100% completion of Phase 1 (Architecture Specification)** and **Phase 2 (Setup Automation, gRPC Subsystems, Empirical Spikes S0a–S9, and Packaging Smoke Tests)**, backed by **120 automated unit tests** and strict Pyright type validation.

```mermaid
flowchart LR
    A["Phase 1: Architecture & Review<br/>(Completed, 2026-10-30)"] --> B["Phase 2: Skeleton, Setup & Spikes<br/>(Completed: v0.1.3)"]
    B --> C["Phase 3a: Core Engine, APK & GUI<br/>(Next Milestone)"]
    C --> D["Phase 3b: Sensors, IME & Settings<br/>(Planned)"]
    D --> E["Phase 4: Inno Setup & Release<br/>(Planned)"]
    style A fill:#2d6a4f,stroke:#1b4332,color:#fff
    style B fill:#2d6a4f,stroke:#1b4332,color:#fff
    style C fill:#1d3557,stroke:#457b9d,color:#fff
    style D fill:#4a4e69,stroke:#22223b,color:#fff
    style E fill:#4a4e69,stroke:#22223b,color:#fff
```

### ✅ Implemented Capabilities (v0.1.3)

#### 1. Architecture Specifications ([docs/](docs/))
* **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Comprehensive v3 technical specification addressing 66 adversarial review findings and 52 verification items.
* **[docs/spikes.md](docs/spikes.md)**: Empirical measurement logs covering all Phase 2 spikes (S0a, S0b, S1, S2, S4, S5, S6, S7, S8, S9, A5, A6, A8).
* **[docs/research/phase1-research-brief.md](docs/research/phase1-research-brief.md)**: Fact-checking summary covering 6 technical domains (WHPX hypervisor, gRPC streaming, ADB isolation, APK parsing, App Inventor quirks, packaging).
* **[plan.md](plan.md)**: Master phase plan and acceptance test matrix (Criteria A1–A9, B1–B15).

#### 2. Clean Architecture Contracts (`src/orcus/contracts/`)
* [`apk.py`](src/orcus/contracts/apk.py): APK classification (`APK`, `AAB`, `AIA`, `AIX`, `UNKNOWN`), manifest extraction dataclasses, pre-flight check models, and execution contracts.
* [`device.py`](src/orcus/contracts/device.py): Frame stream protocol (RGB888 buffers), mouse/touch/key event contracts, sensor injection, and logcat filtering.
* [`engine.py`](src/orcus/contracts/engine.py): Emulator state machine (`STOPPED`, `PREPARING`, `READY`, `STOPPING`, `FAILED`), ownership locks, port allocation, and snapshot tokens.
* [`errors.py`](src/orcus/contracts/errors.py): Unified `ErrorCode` definitions and `OrcusError` exception hierarchy.
* [`session.py`](src/orcus/contracts/session.py): Non-blocking Session API protocol coordinating GUI, APK engine, and emulator controllers.
* [`setup.py`](src/orcus/contracts/setup.py): Step-by-step setup definitions, progress callbacks, and system doctor diagnostic reports.

#### 3. Windows-Hardened Common Infrastructure (`src/orcus/common/`)
* [`winjob.py`](src/orcus/common/winjob.py) / [`win32.py`](src/orcus/common/win32.py): Robust Win32 Job Object wrappers enforcing `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` with token elevation inspection.
* [`paths.py`](src/orcus/common/paths.py): Safe path resolution and validation ensuring all runtime assets reside in ASCII-only paths (`D:\OrcusAPK`, `D:\OrcusDev`).
* [`childenv.py`](src/orcus/common/childenv.py): Strict environment variable allowlist shielding child processes from polluted host configurations.
* [`ports.py`](src/orcus/common/ports.py): Ephemeral port conflict resolution for private ADB (15037) and gRPC console endpoints.
* [`accel.py`](src/orcus/common/accel.py): WHPX state detection (`InstallState`, `RebootPending`) and `emulator-check accel` diagnostic routines.
* [`messages_ko.py`](src/orcus/common/messages_ko.py): User-friendly localized message catalog mapping all internal error codes to clear UI actions.
* Additional utilities: [`config.py`](src/orcus/common/config.py), [`log.py`](src/orcus/common/log.py), [`jsonio.py`](src/orcus/common/jsonio.py), [`clock.py`](src/orcus/common/clock.py), [`pins.py`](src/orcus/common/pins.py), [`hostinfo.py`](src/orcus/common/hostinfo.py).

#### 4. Automated Setup & Verification Engine (`src/orcus/setup/`)
* [`repo_index.py`](src/orcus/setup/repo_index.py): Parses Google official Android SDK repository XML feeds to resolve archive URLs and SHA-1 checksums.
* [`download.py`](src/orcus/setup/download.py): Resumable chunked file downloader with streaming SHA-1 verification.
* [`extract.py`](src/orcus/setup/extract.py): Safe in-place archive decompression resilient against Windows Defender file-locking delays, with `.partial` completion markers.
* [`license.py`](src/orcus/setup/license.py): Enforces Google `android-sdk-license` interactive acceptance in real console sessions (bypassing headless auto-consent).
* [`whpx.py`](src/orcus/setup/whpx.py): Elevated DISM invocation for enabling the Windows Hypervisor Platform feature with reboot state handling.
* [`steps.py`](src/orcus/setup/steps.py): Multi-step pipeline coordinator managing the complete 1–8 setup workflow.
* [`doctor.py`](src/orcus/setup/doctor.py): Read-only diagnostic verification engine reporting hardware virtualization, disk space, and dependency readiness.

#### 5. Emulator gRPC Infrastructure & Test Mocking (`src/orcus/emugrpc/`)
* **Vendored Protobuf Definitions**: Vendored official Google Android Emulator proto contracts (`emulator_controller.proto`, `snapshot.proto`, `snapshot_service.proto`, build 16428233, Apache-2.0).
* **Compiled Stubs & Manifest Verification**: Precompiled Python protobuf and gRPC stubs with strict `.pyi` type hints and SHA-256 header manifest validation (`MANIFEST.json`).
* [`channel.py`](src/orcus/emugrpc/channel.py): Security-hardened local gRPC channel factory configured with token interceptors (`emulator_console_auth_token`) and custom message limits (32 MB).
* [`fake.py`](src/orcus/emugrpc/fake.py): High-fidelity `FakeEmulatorServer` simulating frame streaming, snapshot persistence/restoration, guest clipboard, and multi-touch events for rapid, headless unit testing.

#### 6. Core Diagnostics & Self-Test Suite (`src/orcus/engine/`)
* [`bootcheck.py`](src/orcus/engine/bootcheck.py): Verifies headless WHPX boot readiness (`doctor --boot-check`, testing both `host` and `host_hidden` GPU modes).
* [`selftest.py`](src/orcus/engine/selftest.py): End-to-end component integrity checker validating dependency pins, gRPC stubs, and UTF-8 clipboard roundtrips (`orcus-cli selftest`, `OrcusAPK.exe --selftest`).

#### 7. Empirical Spike Measurements & Performance Validation (`docs/spikes.md`, `tools/spikes/`)
* **S0b (WHPX Headless Boot)**: Successfully booted under WHPX in headless mode (initial cold boot 103s, subsequent warm-cold boot 27–31s).
* **S2 (Host GPU Screen Streaming)**: Verified `-gpu host -no-window` renders directly with host NVIDIA GeForce GTX 1660. Streams 432×768 RGB888 at ~50 fps with only 0.23 host CPU cores, 6 ms frame age, and 46 ms touch-drag-to-frame latency.
* **S4 (Golden Snapshot Instant Boot)**: Achieved **4.8–5.8s boot time** (surpassing the < 10s budget). Designed a 2-phase provisioning routine that eliminates post-restore guest freezing and maintains sub-second clock drift.
* **S9 (Private ADB Daemon)**: Verified port 15037 ADB daemon isolation, 0.06s re-registration, shared key authentication, and zero cross-talk with port 5037 or physical devices.
* **S5 / S8 / S7 (APK Lifecycle & Input)**: App Inventor Companion installation took 5.3s, launch took 1.45s (total drop-to-launch within 10s). Guest `Paste` key injection established for 100% reliable Korean text entry (avoiding Gboard composition anomalies). 3-button navigation overlay and bezel key emulation validated.
* **A5 (Teardown Guarantees)**: Kernel Win32 Job Object terminates all child/grandchild emulator daemons in under **0.55s** upon host process kill.
* **A8 (Packaging Smoke Test)**: Packaged standalone PyInstaller onedir distribution (119 MB) featuring dual executables (`OrcusAPK.exe` & `orcus-cli.exe`), cleanly passing `--selftest` and `--boot-check`.

#### 8. Developer Toolchain & Testing Infrastructure (`tools/`, `tests/`)
* [`bootstrap.py`](tools/bootstrap.py): Fully automated environment bootstrapper setting up `D:\OrcusDev\venv` using strict hash-pinned requirements.
* [`dev.py`](tools/dev.py): Unified development runner directing all build and test caches to D: drive (`check`, `test`, `typecheck`, `cli`).
* [`build.py`](tools/build.py): PyInstaller onedir automated builder with hidden-import resolution and post-build verification.
* [`gen_protos.py`](tools/gen_protos.py): Automated protobuf and gRPC stub generator with drive-isolated caching.
* [`fetch_fixtures.py`](tools/fetch_fixtures.py): Automated fetcher for official MIT App Inventor test APK fixtures.
* **Spike Harnesses (`tools/spikes/`)**: Comprehensive measurement and diagnostic harnesses (`harness.py`, `viewer.py`, `s4_golden.py`, `s9_adb.py`, etc.).
* **Automated Test Suite**: **120 pytest unit tests** passing with 100% success rate. Zero Pyright type check diagnostics.

---

## 📂 Project Directory Structure

```text
Orcus_APK_Emulator/
├── docs/                       # Architecture specifications & research documents
│   ├── ARCHITECTURE.md         # Full technical architecture specification (v3)
│   ├── fixtures.md             # Test APK fixture specifications (HelloPurr, OrcusProbe)
│   ├── spikes.md               # Spike measurement protocols and empirical findings (S0a–S9)
│   └── research/               # Phase 1 domain research briefs
├── src/
│   └── orcus/                  # Core package
│       ├── __init__.py
│       ├── __main__.py         # Application entry point
│       ├── cli.py              # orcus-cli setup, doctor, and selftest commands
│       ├── apk/                # APK staging, inspection, and lifecycle monitoring
│       ├── app/                # Session composition and application lifecycle
│       ├── common/             # OS integration, Win32 Jobs, paths, ports, logging
│       ├── contracts/          # Strict layer protocols and data models (Qt-independent)
│       ├── device/             # gRPC streaming adapter, input queue, logcat
│       ├── emugrpc/            # Vendored gRPC stubs, token channel factory, and FakeEmulatorServer
│       │   ├── protos/         # Upstream Google Android Emulator proto definitions (16428233)
│       │   ├── generated/      # Generated Python protobuf/gRPC modules with .pyi stubs
│       │   ├── channel.py      # Authenticated gRPC channel builder
│       │   └── fake.py         # In-memory emulator gRPC mock server
│       ├── engine/             # Headless emulator process, boot-check, and snapshot controller
│       │   ├── bootcheck.py    # WHPX headless boot readiness diagnostics
│       │   └── selftest.py     # Component integrity self-test
│       ├── gui/                # PySide6 QtWidgets main window and mobile viewport
│       └── setup/              # Resumable downloader, safe extractor, WHPX configuration
├── tests/
│   ├── conftest.py             # Test fixtures, mock servers, and shared assertions
│   └── unit/                   # 120 unit test suites covering common, contracts, setup, emugrpc, engine
├── tools/                      # Developer toolchain (D: drive cache isolation)
│   ├── bootstrap.py            # Automated venv creation & dependency installation
│   ├── dev.py                  # Test / typecheck / CLI execution harness
│   ├── devenv.py               # Development environment path management
│   ├── lock.py                 # Dependency hash-lock generation
│   ├── build.py                # Standalone PyInstaller onedir distribution builder
│   ├── gen_protos.py           # Protobuf/gRPC stub compiler
│   ├── fetch_fixtures.py       # Official App Inventor test APK downloader
│   └── spikes/                 # Empirical measurement harnesses (S0a–S9)
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

# 4. Run the automated unit test suite (120 tests)
py -3.14 tools/dev.py check
```

### 2. Environment Diagnostics & SDK Installation (`orcus-cli`)
Because the Google Android SDK license agreement requires explicit interactive user consent, execute the setup command in your interactive terminal:

```powershell
# Run the diagnostic health check (read-only, does not modify system state)
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli doctor

# Run the 8-step setup pipeline (downloads ~2.3 GB SDK assets to D:\OrcusAPK)
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli setup

# Run the self-test verifying gRPC and runtime dependencies
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli selftest
```

The setup pipeline will:
1. Validate system prerequisites and disk space on drive `D:`.
2. Present the Google SDK license agreement for interactive acceptance.
3. Verify and enable the Windows Hypervisor Platform (WHPX) via UAC elevation if needed.
4. Download the verified Google Emulator, Platform-Tools, and Android 16 system image with SHA-1 checks.
5. Extract all archives safely and configure the `orcus36` AVD.
6. Generate isolated private ADB authorization keys.

---

## 🗺️ Roadmap & Milestones

- [x] **Phase 1: Architecture & Technical Foundations** (Completed, 2026-10-30)
- [x] **Phase 2: Skeleton, Setup Automation & Spikes** (Completed: v0.1.3)
  - [x] Project skeleton, contracts, common utilities, and setup pipeline
  - [x] 120 automated unit tests and strict Pyright compliance
  - [x] Emulator gRPC protobuf compilation (`orcus.emugrpc`) and FakeEmulatorServer mock
  - [x] Spike harnesses (`tools/spikes/`) covering S0a–S9 (GPU latency, golden boot, audio, sensors)
  - [x] Standalone PyInstaller onedir smoke test (A8, 119 MB distribution)
- [ ] **Phase 3a: Core Execution Runtime & Primary GUI** (Next Milestone)
  - [ ] `engine`: State machine, private ADB daemon watchdog, golden snapshot restore
  - [ ] `apk`: Drag-and-drop staging pipeline, `adb install -r`, `am start`, logcat monitors
  - [ ] `gui`: PySide6 9:16 mobile frame, raster frame blit, [App Card] and [Logs] panels
- [ ] **Phase 3b: Sensor Integration, IME & Settings**
  - [ ] [Sensors] panel: Shake sensitivity presets, rotation, 20° tilt pad, GPS coordinates
  - [ ] Korean IME text composition input bar with guest clipboard synchronization
- [ ] **Phase 4: Packaging & Distribution**
  - [ ] Inno Setup standalone user installer (zero admin privileges required)
  - [ ] Optional multi-session state preservation (Keep-state A/B slots)

---

## 📄 License & Attribution

* **Proprietary Code**: Core Orcus APK Emulator application code and architecture documents are maintained for proprietary educational and research purposes.
* **Open Source Dependencies**: Third-party libraries (`PySide6-Essentials`, `grpcio`, `protobuf`, `apkInspector`) are used under their respective open-source licenses (LGPLv3, Apache 2.0, MIT).
* **Android SDK**: Google Android Emulator and system image components are subject to the Google Android Software Development Kit License Agreement and are downloaded directly to the local host machine without redistribution.
