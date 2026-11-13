# NOVA Assistant — User Guide (v2.0.0)

NOVA is a small desktop companion for Windows 10/11: a floating circular face,
a voice you can talk to, and local monitors that tell you when your coding
tasks finish. Everything is offline. Nothing you say or do leaves this
computer.

---

## 1. Install (one time)

1. Extract the complete `NOVA_Assistant` folder to a permanent location.
2. Install Python 3.11 or newer (64-bit) from https://www.python.org/downloads/
   — leave the "py launcher" option ticked.
3. **Double-click `Install NOVA.bat`.**
   It installs the dependencies into a private `.venv` folder and downloads the
   free offline speech model (40 MB, Apache 2.0, no account).
4. Done. The window tells you the next step.

**You do not need:** any API key, paid service, Picovoice account, cloud
speech, or administrator rights.

### Alternative — portable (no Python required)

Extract `NOVA_Portable_v2.0.0.zip` anywhere and **double-click `NOVA.exe`**.
That folder is self-contained: the app, the interface libraries and the speech
model are all inside. Settings and logs are created next to the exe (or in
`%LOCALAPPDATA%\NOVA` if the folder is read-only).

## 2. Launch

**Double-click `Launch NOVA.bat`.**

- First launch opens the **NOVA Control Center** and a short setup wizard that
  checks your microphone, speech voice and speech model.
- If NOVA is already running, launching again simply brings its window back.
- `run_nova.bat` also works and does the same thing.

### The three window buttons (top right of the Control Center)

| Button | Meaning |
|---|---|
| `—` Minimise | Goes to the taskbar. NOVA keeps running, face stays visible. |
| `↓` Hide in tray | Window and face disappear. Voice + monitors keep running; reopen from the tray icon. |
| `×` Close | Stops NOVA completely (face, microphone, speech, monitors). |

Closing the window **never** disables "Start with Windows".

### Start with Windows

Startup Settings → toggle "Start with Windows". This creates a shortcut in
your personal Startup folder (no admin). In that mode NOVA starts silently —
face and tray only, no window.

## 3. The face

- Sits just below the top edge, centred under the clock by default, 66 px.
- **Drag** it anywhere; the position is remembered after restart.
- Clicks pass through everywhere **except** the circle itself (click the face
  to reopen the Control Center).
- Size, screen and show/hide are in Appearance. You can also hide it from the
  tray menu — monitoring and voice continue.
- It shows states: idle, listening, working, completed, error, speaking.
  Animation pauses when hidden or the screen is off (saves CPU).

## 4. Talking to NOVA

Say **“NOVA”** → NOVA answers **“Yes, sir?”** → speak your command.

Starter commands:

| Say | Does |
|---|---|
| “Activate my study mode” | Greets you and opens your study URLs/apps |
| “Get ready for my entertainment mode” | Greets you and opens your entertainment URLs/apps |
| “Mute” / “Unmute” | Speech on/off (monitoring unaffected) |
| “What mode?” | Announces the active mode |
| “Open settings” | Opens the Control Center |
| “Hide NOVA” / “Show NOVA” | Face visibility |
| “Exit NOVA” | Clean shutdown |

Everything is processed locally with Vosk; speech is Windows' built-in SAPI
voices. Recordings are never saved.

**If the microphone or model is missing:** NOVA shows a friendly error and the
Voice Settings page has a **Download model** button and a **Test voice**
button so you can fix and verify each piece separately.

## 5. Modes (Study / Entertainment)

Both pages in the Control Center let you edit:

- **Greeting** — what NOVA says (default: “Welcome back, sir. Your study
  environment is ready.”).
- **URLs** — opened in your default browser; only http/https accepted.
- **Applications** — browse to any `.exe`; optional arguments. Nothing is
  hardcoded — your list is yours.

Save, then trigger by voice (or from the tray menu).

## 6. Monitors (Application Monitoring)

Each monitor has its own on/off switch and keeps running even when the face is
hidden. Speech mute only silences the voice — monitors still report.

- **OpenCode** — connects to the local OpenCode Desktop server
  (`http://127.0.0.1:<port>`, discovered automatically from OpenCode's own
  logs; Basic-auth password read from OpenCode's local `service.json`).
  Reports task started / completed / failed / interrupted. Use **Test** to
  verify the stream; it tells you if the server is unreachable or
  authentication is missing.
- **Codex** — reads the real Codex session files in `~/.codex/sessions`
  (zstd-compressed rollout logs) and reports working / success / failure /
  interrupted per task.

Statuses shown are only ever based on real events. If something cannot be
monitored, the UI says exactly why — nothing is faked.

## 7. Uninstall

**Double-click `Uninstall NOVA.bat`** (or the button in Startup Settings).

1. Removes your Windows startup entry (always).
2. Stops NOVA if it is running (always).
3. Asks whether to delete the speech model, settings and logs.
4. Asks whether to delete the Python environment.
5. You delete the folder itself when ready.

---

## Feature status — WORKING (verified on this machine)

| Feature | Evidence |
|---|---|
| Test suite | 49/49 pytest tests pass (repeatable: `.venv\Scripts\python.exe -m pytest tests\ -q`) |
| Install (`Install NOVA.bat`) | Run end-to-end on this PC, exit code 0 |
| Launch (`Launch NOVA.bat`) | App started, control center visible, clean exit |
| Uninstall (`Uninstall NOVA.bat`) | Run on this PC, exit code 0, data-keep choices honoured |
| First-launch wizard | Appears on first launch; checks mic/voice/model |
| Face window | Visible in enumeration tests; drag/position/state logic unit-tested |
| Single instance | Second launch exits and reopens the first instance's window |
| Quiet `--startup` mode | Face + tray only; control center stays closed |
| Start with Windows | Shortcut created, target/arguments verified, removal verified |
| Wake word “NOVA” | Real detections from the live microphone in `logs/nova.log` |
| Offline command recognition | Vosk recognised spoken phrases from the live microphone |
| Speech output (SAPI) | Voices enumerated (David, Zira); utterances accepted by the host |
| Speech host lifecycle | Restart/mute paths tested; no spurious errors after fix |
| Speech model download | 40 MB Apache 2.0 model downloaded, verified, skips when present |
| OpenCode monitor | Connected live to `127.0.0.1:28894` with authentication; dynamic port discovery from desktop logs; real frame format verified |
| OpenCode event classification | Unit-tested: succeeded/failed/interrupted/busy→idle, no false completions |
| Codex monitor | Parsed the real `.jsonl.zst` rollout files on this PC; all four outcome kinds produced |
| Logs | Rotating `logs/nova.log` written; clean-shutdown sequence confirmed |
| Clean shutdown | “Full shutdown requested → services stopped → shut down” in log, no orphan processes |
| Portable exe (`NOVA.exe`) | Built with PyInstaller 6.22, launched on this PC: face + wizard + control center visible, Vosk model loaded, OpenCode connected, graceful shutdown; packaged in `NOVA_Portable_v2.0.0.zip` |
| Delivery ZIPs | `NOVA_Assistant_v2.0.0.zip` (40.7 MB source package) and `NOVA_Portable_v2.0.0.zip` (117.7 MB) created and entry-verified |

## Feature status — LIMITED / UNAVAILABLE (honestly)

| Feature | Status and real reason |
|---|---|
| Codex **live** Desktop monitoring | **Unavailable.** Codex Desktop exposes no supported local event interface that attaches to conversations already open in its UI. NOVA uses Codex's own session logs instead (verified working) and never invents events. |
| OpenCode events during downtime | **Limited by design.** SSE connections drop sometimes; events emitted while disconnected cannot be recovered (OpenCode's route is documented as experimental). |
| Audible confirmation of speech | The SAPI pipeline is verified programmatically; whether you *hear* it depends on your speakers/volume — test with the **Test voice** button. |
| Wake-word accuracy | Works with the free Vosk model (verified with real detections). It is not a paid noise-robust engine: very noisy rooms may need a larger model from https://alphacephei.com/vosk/models. |
| Multi-monitor face placement | The face targets the primary screen; a "screen" setting exists but extra-monitor placement is not specifically tested. |

## Privacy summary

- Microphone audio is processed in memory, locally, and never written to disk.
- The only network traffic: downloading the free speech model during install,
  and `127.0.0.1`-only connections to your own local OpenCode server.
- No accounts, no telemetry, no cloud APIs.

## Troubleshooting

| Problem | Fix |
|---|---|
| “Python not found” during install | Install Python 3.11+ from python.org and re-run `Install NOVA.bat`. |
| No wake word | Voice Settings → **Download model**, check the microphone device, ensure "Wake detection" is on. |
| No speech | Voice Settings → pick a voice, click **Test voice**. |
| OpenCode “not running” | Start OpenCode Desktop, click **Test** in Application Monitoring (the port is found automatically). |
| “Authentication required” | OpenCode's local `service.json` is missing — reinstall/update OpenCode Desktop. |
| Want a fresh start | Delete `config\settings.json`, or run the uninstaller with the data option. |
| Logs | `logs\nova.log` (rotating). |
