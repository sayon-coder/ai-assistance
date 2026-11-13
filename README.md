# NOVA — Windows desktop assistant (v2.0.0)

NOVA is a local-first Windows 11/10 assistant with an animated face companion, a
configuration window, a system-tray controller, editable Study/Entertainment
modes, fully offline wake-word and command recognition, Windows speech output,
and local Codex/OpenCode task monitors.

**Everything runs on your PC.** No paid APIs, no cloud speech, no account, no
subscriptions. Microphone audio and local files never leave this machine.

## Quick start — which file do I click?

| You want to… | Click this |
|---|---|
| Install NOVA for the first time (from `NOVA_Assistant_v2.0.0.zip`) | **`Install NOVA.bat`** |
| Start NOVA every day | **`Launch NOVA.bat`** |
| Run NOVA with no Python install at all (from `NOVA_Portable_v2.0.0.zip`) | **`NOVA.exe`** inside the `NOVA` folder |
| Remove NOVA completely | **`Uninstall NOVA.bat`** |

1. Extract the whole `NOVA_Assistant` folder somewhere stable, e.g.
   `C:\Users\<you>\Apps\NOVA_Assistant`.
2. Install Python **3.11 or newer (64-bit)** from python.org (keep the default
   "py launcher" option ticked).
3. Double-click **`Install NOVA.bat`** — it creates the virtual environment,
   installs dependencies and downloads the free offline speech model.
4. Double-click **`Launch NOVA.bat`** — the control center opens on first run
   with a short setup wizard (microphone, voice, model checks).

**No Python? Use the portable build.** Extract `NOVA_Portable_v2.0.0.zip`
anywhere and double-click **`NOVA.exe`** — it is a self-contained build
(PySide6, Vosk, speech model included) and needs no installation at all.
Built and verified with PyInstaller from this same source.

## What is included (all verified by the automated test suite and on-machine runs)

- **Animated face companion:** the supplied circular face (white-to-blue
  texture, two black oval eyes, transparent corners) shown at 60–70 px by
  default, just below the top edge near the clock. Drag to reposition — the
  offset persists across restarts. Clicks pass through everywhere except on the
  circle itself. Idle / listening / working / completed / error / speaking
  states, ~30 FPS, animation pauses when hidden or the screen is off.
- **Control center (8 sections):** Dashboard, Study Mode, Entertainment Mode,
  Voice Settings, Appearance, Application Monitoring, Startup Settings, About.
  The three top-right controls are distinct:
  - `—` Minimise to the taskbar (NOVA keeps running, face stays visible).
  - `↓` Hide in tray (window and face hide; voice and monitors keep running).
  - `×` Close — a full, clean shutdown.
  Closing the window never removes your startup setting.
- **Tray menu:** open the control center, show/hide the face, mute speech,
  activate modes, enable/disable startup, exit.
- **Voice (100% offline):** Vosk wake-word detection for **“NOVA”** plus Vosk
  offline command recognition and Windows SAPI speech output. Saying “NOVA”
  gets “Yes, sir?”, then speak a command. Speech mute is independent of
  monitoring. A voice test button and per-feature switches are in Voice
  Settings. No Picovoice, no Porcupine, no cloud STT/TTS — ever.
- **Modes:** Study and Entertainment are voice-triggered and fully editable in
  the UI (greeting, URLs, applications — no hardcoded paths; default browser
  used). Greeting example: “Welcome back, sir. Your study environment is ready.”
- **OpenCode monitoring:** connects to the documented local
  `GET /api/event` Server-Sent Events route on `127.0.0.1` only, using the
  Basic-auth password OpenCode Desktop itself stores in
  `~/.config/opencode/service.json`, and discovers the dynamic desktop port
  from `%APPDATA%\ai.opencode.desktop\logs`. Real terminal events
  (`session.execution.succeeded/failed/interrupted/stopped`) plus busy→idle
  transitions are distinguished; dropped connections never fabricate a result.
- **Codex monitoring:** parses the real `~/.codex/sessions/**/*.jsonl.zst`
  rollout logs (zstd), distinguishing working / success / failure /
  interrupted, with per-monitor switches. Monitoring continues while the face
  is hidden.
- **Startup:** per-user Windows Startup shortcut (no admin), one toggle in the
  UI, quiet background launch (`--startup`).
- **Single instance:** launching NOVA while it is already running reopens the
  control center of the running copy.

## Important limitation — Codex Desktop live monitoring

Codex Desktop has no supported local event interface that attaches to
conversations already open in its UI. NOVA therefore reads Codex's own session
rollout logs instead (verified against real logs on this machine). NOVA never
invents events and never infers completion from a process exiting. If the
log-based integration cannot run, the Monitoring section says so plainly with
the real blocker.

## Voice commands (starter set)

- “Activate my study mode” / “Get ready for my entertainment mode”
- “Mute” / “Unmute”
- “What mode?”
- “Open settings”
- “Hide NOVA” / “Show NOVA”
- “Exit NOVA”

## Uninstall

Double-click **`Uninstall NOVA.bat`** (or use the button in Startup Settings).
It always removes the Windows startup entry and stops NOVA, then asks whether
to delete the downloaded speech model, settings/logs and the Python
environment. Finally delete the folder yourself.

## Face placement and animation

The original supplied GIF is kept at `assets/user_face_original.gif`; the
runtime uses `assets/nova_face.gif` (circular face only, transparent
surroundings). Adjust size and position in Appearance or by dragging. The
overlay is click-through outside the circle so it never blocks clicks.

## Engineering notes

- Tested on this machine: Python 3.14.5, PySide6 6.12, vosk 0.3.45 — full test
  suite (49 tests) passes, real wake-word detections, real OpenCode stream
  connection, real Codex log parsing, install/launch/uninstall round-trips.
- OpenCode's `/api/event` route is documented as experimental; reconnection
  cannot recover events missed while disconnected.
- All monitor connections are restricted to `127.0.0.1` / localhost.
- Run NOVA as a normal user, never as Administrator.
- Settings live in `config/settings.json`, rotating logs in `logs/`, the
  speech model in `models/` (Apache License 2.0, downloaded from
  https://alphacephei.com/vosk/models).

## Folder layout

```text
NOVA_Assistant/
  Install NOVA.bat        # one-click installer
  Launch NOVA.bat         # daily launcher
  Uninstall NOVA.bat      # one-click uninstaller
  assets/                 # original GIF, circular face GIF, tray icon
  config/                 # settings and mode definitions
  logs/                   # rotating local log
  models/                 # offline Vosk model (auto-downloaded)
  nova/                   # UI, face overlay, voice, modes, monitors
  scripts/                # startup shortcut install/uninstall
  tests/                  # 49 automated tests (pytest)
  USER_GUIDE.md           # full user guide + feature status lists
  setup.ps1               # venv + dependency installation (used by installer)
  main.py                 # entry point
```
