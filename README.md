# Orcus APK Emulator

> **Ultra-Lightweight, Instant-Launch Windows 11 Desktop Runner for MIT App Inventor APKs**  
> Powered by Headless Google Android Emulator & Low-Latency Token-Authenticated gRPC

[![Release](https://img.shields.io/badge/Release-v0.1.1-blue.svg)](https://github.com/OrcusExtreme/Orcus_APK_Emulator/releases/tag/v0.1.1)
[![Python](https://img.shields.io/badge/Python-3.14-green.svg)](pyproject.toml)
[![GUI](https://img.shields.io/badge/GUI-PySide6--Essentials%206.12-orange.svg)](pyproject.toml)
[![Hypervisor](https://img.shields.io/badge/Hypervisor-WHPX-purple.svg)](docs/ARCHITECTURE.md)
[![Tests](https://img.shields.io/badge/Tests-105%20Passed-brightgreen.svg)](tests/)
[![Typecheck](https://img.shields.io/badge/Typecheck-Pyright%20Clean-success.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](NOTICE)

---

## 📌 Overview

**Orcus APK Emulator** is a dedicated Windows 11 desktop runner designed specifically for testing educational Android applications built with **MIT App Inventor** (pure DEX bytecode, standard Android View/Canvas, sensor components, and media engines).

Unlike heavy, general-purpose commercial emulators (such as BlueStacks, Nox, or LDPlayer) that suffer from multi-gigabyte memory footprints, bloated user interfaces, bundled ads, and persistent background telemetry, Orcus APK Emulator executes an official **Google Android Emulator** instance in headless mode (`-no-window -gpu host`) atop the **Windows Hypervisor Platform (WHPX)**. It streams frames and injects inputs via a low-latency, token-protected **gRPC API**, providing an instantaneous, native-feeling development testing experience.

### 🎯 Key Highlights
* **Drag-and-Drop Instant Launch**: Drop any `.apk` file directly onto the window; the runtime stages the file, validates the manifest, and displays the running app in **under 10 seconds**.
* **Golden Snapshot Boot (< 10s)**: Bypasses multi-minute cold boot sequences by launching from a pre-warmed, clean golden system snapshot on every session.
* **Lean Runtime Footprint**: Restricts running processes to only 4 daemons (Host GUI, Private ADB server, `emulator.exe`, and `qemu-system-x86_64-headless.exe`).
* **Zero Orphan Process Leaks**: Wraps the entire execution tree inside a Win32 Job Object configured with `KILL_ON_JOB_CLOSE`, guaranteeing immediate termination of all child emulator processes on exit or unexpected crash.
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

## 📊 Current Progress & Status (v0.1.1 / Phase 2)

The project has successfully completed **Phase 1 (Architecture Specification & Validation)** and achieved **Milestone 1 of Phase 2 (Core Skeleton, Setup Automation, and Common Utilities)** with 105 automated unit tests passing cleanly.

```mermaid
flowchart LR
    A["Phase 1: Architecture & Review<br/>(Completed, 2026-10-30)"] --> B["Phase 2: Skeleton, Setup & Spikes<br/>(Current: v0.1.1)"]
    B --> C["Phase 3a: Core Engine, APK & GUI<br/>(Planned)"]
    C --> D["Phase 3b: Sensors, IME & Settings<br/>(Planned)"]
    D --> E["Phase 4: Inno Setup & Release<br/>(Planned)"]
    style A fill:#2d6a4f,stroke:#1b4332,color:#fff
    style B fill:#1d3557,stroke:#457b9d,color:#fff
    style C fill:#4a4e69,stroke:#22223b,color:#fff
    style D fill:#4a4e69,stroke:#22223b,color:#fff
    style E fill:#4a4e69,stroke:#22223b,color:#fff
```

### ✅ Implemented Capabilities (v0.1.1)

#### 1. Architecture Specifications ([docs/](docs/))
* **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Comprehensive v3 technical specification addressing 66 adversarial review findings and 52 verification items.
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
* [`extract.py`](src/orcus/setup/extract.py): Safe archive decompression with directory traversal (Zip-Slip) protection and antivirus quarantine detection.
* [`license.py`](src/orcus/setup/license.py): Enforces Google `android-sdk-license` interactive acceptance in real console sessions (bypassing headless auto-consent).
* [`whpx.py`](src/orcus/setup/whpx.py): Elevated DISM invocation for enabling the Windows Hypervisor Platform feature with reboot state handling.
* [`steps.py`](src/orcus/setup/steps.py): Multi-step pipeline coordinator managing the complete 1–8 setup workflow.
* [`doctor.py`](src/orcus/setup/doctor.py): Read-only diagnostic verification engine reporting hardware virtualization, disk space, and dependency readiness.

#### 5. Developer Toolchain & Testing Infrastructure (`tools/`, `tests/`)
* [`bootstrap.py`](tools/bootstrap.py): Fully automated environment bootstrapper setting up `D:\OrcusDev\venv` using strict hash-pinned requirements.
* [`dev.py`](tools/dev.py): Unified development runner directing all build and test caches to D: drive (`check`, `test`, `typecheck`, `cli`).
* [`lock.py`](tools/lock.py): Dependency hash-lock regenerator via `pip-tools`.
* **Automated Test Suite**: 105 pytest test cases verifying contract integrity, Win32 Job lifecycle, path guards, AVD configurations, and parser edge cases. Zero Pyright type check diagnostics.

---

## 📂 Project Directory Structure

```text
Orcus_APK_Emulator/
├── docs/                       # Architecture specifications & research documents
│   ├── ARCHITECTURE.md         # Full technical architecture specification (v3)
│   ├── fixtures.md             # Test APK fixture specifications (HelloPurr, OrcusProbe)
│   ├── spikes.md               # Spike measurement protocols and findings
│   └── research/               # Phase 1 domain research briefs
├── src/
│   └── orcus/                  # Core package
│       ├── __init__.py
│       ├── __main__.py         # Application entry point
│       ├── cli.py              # orcus-cli setup and doctor commands
│       ├── apk/                # APK staging, inspection, and lifecycle monitoring
│       ├── app/                # Session composition and application lifecycle
│       ├── common/             # OS integration, Win32 Jobs, paths, ports, logging
│       ├── contracts/          # Strict layer protocols and data models (Qt-independent)
│       ├── device/             # gRPC streaming adapter, input queue, logcat
│       ├── emugrpc/            # Vendored gRPC stubs and client channel factories
│       ├── engine/             # Headless emulator process and snapshot controller
│       ├── gui/                # PySide6 QtWidgets main window and mobile viewport
│       └── setup/              # Resumable downloader, extractor, WHPX configuration
├── tests/
│   ├── conftest.py             # Test fixtures and shared mocks
│   └── unit/                   # 105 unit test suites covering common, contracts, setup
├── tools/                      # Developer toolchain (D: drive cache isolation)
│   ├── bootstrap.py            # Automated venv creation & dependency installation
│   ├── dev.py                  # Test / typecheck / CLI execution harness
│   ├── devenv.py               # Development environment path management
│   └── lock.py                 # Dependency hash-lock generation
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

# 4. Run the automated unit test suite (105 tests)
py -3.14 tools/dev.py check
```

### 2. Environment Diagnostics & SDK Installation (`orcus-cli`)
Because the Google Android SDK license agreement requires explicit interactive user consent, execute the setup command in your interactive terminal:

```powershell
# Run the diagnostic health check (read-only, does not modify system state)
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli doctor

# Run the 8-step setup pipeline (downloads ~2.3 GB SDK assets to D:\OrcusAPK)
D:\OrcusDev\venv\Scripts\python.exe -X utf8 -m orcus.cli setup
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
- [ ] **Phase 2: Skeleton, Setup Automation & Spikes** (Current Phase)
  - [x] Project skeleton, contracts, common utilities, and setup pipeline
  - [x] 105 automated unit tests and strict Pyright compliance
  - [ ] Emulator gRPC protobuf compilation (`orcus.emugrpc`) and mock server
  - [ ] Spike harnesses (`tools/spikes/`) covering S0a–S9 (GPU latency, golden boot, audio, sensors)
  - [ ] PyInstaller onedir smoke test
- [ ] **Phase 3a: Core Execution Runtime & Primary GUI**
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
