# TALCON Assistant — User Guide (v2.0.0)

TALCON is a small desktop companion for Windows 10/11: a floating circular face,
a voice you can talk to, and local monitors that tell you when your coding
tasks finish. Everything is offline. Nothing you say or do leaves this
computer.

---

## 1. Install (one time)

1. Extract the complete `TALCON_Assistant` folder to a permanent location.
2. Install Python 3.11 or newer (64-bit) from https://www.python.org/downloads/
   — leave the "py launcher" option ticked.
3. **Double-click `Install TALCON.bat`.**
   It installs the dependencies into a private `.venv` folder and downloads the
   free offline speech model (40 MB, Apache 2.0, no account).
4. Done. The window tells you the next step.

**You do not need:** any API key, paid service, Picovoice account, cloud
speech, or administrator rights.

### Alternative — portable (no Python required)

Extract `TALCON_Portable_v2.0.0.zip` anywhere and **double-click `TALCON.exe`**.
That folder is self-contained: the app, the interface libraries and the speech
model are all inside. Settings and logs are created next to the exe (or in
`%LOCALAPPDATA%\TALCON` if the folder is read-only).

## 2. Launch

**Double-click `Launch TALCON.bat`.**

- First launch opens the **TALCON Control Center** and a short setup wizard that
  checks your microphone, speech voice and speech model.
- If TALCON is already running, launching again simply brings its window back.
- `run_talcon.bat` also works and does the same thing.

### The three window buttons (top right of the Control Center)

| Button | Meaning |
|---|---|
| `—` Minimise | Goes to the taskbar. TALCON keeps running, face stays visible. |
| `↓` Hide in tray | Window and face disappear. Voice + monitors keep running; reopen from the tray icon. |
| `×` Close | Stops TALCON completely (face, microphone, speech, monitors). |

Closing the window **never** disables "Start with Windows".

### Start with Windows

Startup Settings → toggle "Start with Windows". This creates a shortcut in
your personal Startup folder (no admin). In that mode TALCON starts silently —
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

## 4. Talking to TALCON

Say **“TALCON”** → TALCON answers **“Yes, sir?”** → speak your command.

Starter commands:

| Say | Does |
|---|---|
| “Activate my study mode” | Greets you and opens your study URLs/apps |
| “Get ready for my entertainment mode” | Greets you and opens your entertainment URLs/apps |
| “Mute” / “Unmute” | Speech on/off (monitoring unaffected) |
| “Don’t take commands” | Ignores commands but keeps talking, until you say “take command” |
| “Take command” | Starts taking commands again after a pause |
| “What mode?” | Announces the active mode |
| “Open settings” | Opens the Control Center |
| “Hide TALCON” / “Show TALCON” | Face visibility |
| “Exit TALCON” | Clean shutdown |
| “How many programs do I have open right now?” | Counts your open applications |
| “Is Chrome running?” / “Do I have Spotify open?” | Answers yes or no |
| “Volume 40” / “Turn it up” / “Speak slower” | Changes the voice itself |
| “Be quiet” | Stops the current answer only (voice stays on) |
| “Say that again” / “Open it again” | Repeats the last answer / reopens the last app |

**When TALCON is not sure.** If a name is misheard, TALCON suggests the
closest installed applications and asks. Answer naturally:

| You say | What happens |
|---|---|
| “Yes” | Opens the app it named first |
| “No” | Opens nothing |
| “Yes, the first one” / “Number one” / “The first” | Opens the first choice |
| “The second one” / “Number two” | Opens the second choice |
| The app's name | Opens that one and remembers how you pronounced it |

No application names are written into the code: the choices always come from
your own installed apps, and the numbers follow the order TALCON read them
aloud.

**Response preview.** Every spoken reply appears in a compact box just below
the avatar. It shows the first two lines; click it to read the full answer in a
separate panel with **Copy response** and **Close**. The panel scrolls long
answers. Right-click the small box to dismiss it.

**History.** The **History** page in the sidebar keeps every reply from the
current session (newest at the bottom) together with what you asked. Click an
entry to read it in full, or use **Clear history** (with confirmation).
History lives in memory only: it is never written to disk, a database or
browser storage, it survives minimising to the tray, and it disappears when
TALCON exits or is force-closed.

**Commands.** The **Commands** page records every command that actually ran:
the transcript the microphone produced, the phrase TALCON interpreted, the
action, the execution time and Vosk's confidence. If TALCON misunderstood you,
this page shows exactly where. It is session-only, like History.

**Choosing the voice.** TALCON speaks only through the free Kokoro neural
voice; Microsoft SAPI and Windows voices are no longer used. If the Kokoro
model is missing, TALCON says so and stays silent instead of switching to a
robotic voice. To install Kokoro (about 118 MB, Apache-2.0, no account):

```
python -m talcon.model_setup --kokoro
```

Kokoro runs on the CPU, loads only while speaking and unloads after 90 seconds
of silence, which keeps a modest laptop responsive. Its default voice is
**George** (calm British male); other voices are listed in Voice Settings.

**Pace between sentences.** Streamed answers are spoken sentence by sentence.
TALCON synthesises the next sentence while the current one is still playing, so
the next words are ready before the full stop ends. **Pause after a full stop**
in Voice Settings sets the beat of silence that separates sentences (one second
by default; slide it down for a brisker delivery).

Everything is processed locally. Vosk is the default recogniser; Voice Settings
also offers **Automatic** and **sherpa-onnx** engines when that optional package
and model are installed, with Vosk used automatically as the fallback. Shared
automatic gain and noise suppression improve quiet or distant speech, and an
optional faster-whisper pass is available for long questions only. Recordings
are never saved and no voice files are bundled.

To measure the engines on your own machine instead of guessing:

```bat
.venv\Scripts\python.exe scripts\benchmark_speech.py
```

The report includes word error rate and latency at 0.5 m, 2 m and 3 m.

## AI answers (on by default)

AI answers are switched **on** out of the box and can always be turned off
with **Enable AI answers** in Voice Settings. TALCON forwards the things it
cannot do locally to the locally-installed Hermes Agent, then speaks and
displays the answer. Anything you say that is not a known command goes to
the AI, so you do not have to remember exact wording. Say things like:

| You say | What happens |
| --- | --- |
| “What’s the difference between RAM and storage?” | Hermes answers; TALCON speaks it and shows it under the face |
| “Why did my last build fail?” | Same — plus a one-line status while it works |
| “Summarise the notes in my project folder” | Hermes runs it as a task with its own tools |
| “Stop that” / “Never mind” | Cancels the running AI request || “Yes” / “No” | Approves or denies a dangerous operation the AI asks about |

Routing is **command-first**: anything TALCON can do locally (opening apps,
modes, mute, timers, custom commands) never reaches the AI. Fragments and
ordinary background chatter are not sent either — only phrases that clearly
read as a question or a request.

**What the AI can do.** Through Hermes it can answer questions and run
multi-step tool work: browsing, reading and writing files you point it at,
and terminal commands. Anything Hermes flags as dangerous comes back to you
as a spoken yes/no question; silence counts as *no*. AI requests also stop
while commands are paused (“don’t take commands”).

**Privacy.** Microphone audio is never sent anywhere — only the recognised
text of the question itself. TALCON talks to Hermes on 127.0.0.1 only (it
refuses any other address) using the API key from your local Hermes
dotfiles, never a hard-coded one, and it never prints that key in a log or
message. AI conversation context lives in RAM for this session and vanishes
on exit, like the History page. You can also toggle AI off with the switch
in Voice Settings — **Check connection** there shows whether Hermes is
reachable.

**Repeated questions are answered from memory.** TALCON keeps a small,
bounded cache of answers to informational questions it has already answered
in this session (100 answers, 10 minutes each, in RAM only). Asking the same
factual question again returns the stored answer instantly instead of
calling Hermes again; the Voice Settings card shows the running hit rate.
Nothing that acts on your PC is ever cached: app launching, file changes,
browser automation, terminal commands, destructive operations, live
data such as prices, news or weather, and anything that depends on the
current state of your machine always make a fresh request. Answers that
used a Hermes tool are never cached either, so TALCON never replays a
report of an operation it did not just perform. Turning AI off, or exiting
TALCON completely, empties the cache; hiding TALCON to the tray keeps it
warm. The capacity and lifetime are configurable in `config/settings.json`
as `ai_cache_entries` and `ai_cache_ttl_seconds` (set `ai_cache_enabled` to
`false` to disable it).

**Kokoro voice model:** run `python -m talcon.model_setup --kokoro` once to place
the model in `models/kokoro`, then press **Refresh** in Voice Settings. The
**Check Kokoro voice** button reports the real installed voices and model
files; TALCON never pretends a missing voice is active.

**If the microphone or model is missing:** TALCON shows a friendly error and the
Voice Settings page has a **Download model** button and a **Test voice**
button so you can fix and verify each piece separately.

## 5. Modes (Study / Entertainment)

Both pages in the Control Center let you edit:

- **Greeting** — what TALCON says (default: “Welcome back, sir. Your study
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

**Double-click `Uninstall TALCON.bat`** (or the button in Startup Settings).

1. Removes your Windows startup entry (always).
2. Stops TALCON if it is running (always).
3. Asks whether to delete the speech model, settings and logs.
4. Asks whether to delete the Python environment.
5. You delete the folder itself when ready.

---

## Feature status — WORKING (verified on this machine)

| Feature | Evidence |
|---|---|
| Test suite | 708/708 pytest tests pass (repeatable: `.venv\Scripts\python.exe -m pytest tests\ -q`) |
| Install (`Install TALCON.bat`) | Run end-to-end on this PC, exit code 0 |
| Launch (`Launch TALCON.bat`) | App started, control center visible, clean exit |
| Uninstall (`Uninstall TALCON.bat`) | Run on this PC, exit code 0, data-keep choices honoured |
| First-launch wizard | Appears on first launch; checks mic/voice/model |
| Face window | Visible in enumeration tests; drag/position/state logic unit-tested |
| Single instance | Second launch exits and reopens the first instance's window |
| Quiet `--startup` mode | Face + tray only; control center stays closed |
| Start with Windows | Shortcut created, target/arguments verified, removal verified |
| Wake word “TALCON” | Real detections from the live microphone in `logs/talcon.log` |
| Offline command recognition | Vosk recognised spoken phrases from the live microphone |
| Speech output (Kokoro) | Kokoro-82M voices enumerated; in-memory WAV synthesis and real CPU playback verified |
| Speech host lifecycle | Model load/unload, mute and interrupt paths tested; no spurious errors |
| Speech model download | 40 MB Apache 2.0 model downloaded, verified, skips when present |
| OpenCode monitor | Connected live to `127.0.0.1:28894` with authentication; dynamic port discovery from desktop logs; real frame format verified |
| OpenCode event classification | Unit-tested: succeeded/failed/interrupted/busy→idle, no false completions |
| Codex monitor | Parsed the real `.jsonl.zst` rollout files on this PC; all four outcome kinds produced |
| Logs | Rotating `logs/talcon.log` written; clean-shutdown sequence confirmed |
| Clean shutdown | “Full shutdown requested → services stopped → shut down” in log, no orphan processes |
| Portable exe (`TALCON.exe`) | Built with PyInstaller 6.22, launched on this PC: face + wizard + control center visible, Vosk model loaded, OpenCode connected, graceful shutdown; packaged in `TALCON_Portable_v2.0.0.zip` |
| Delivery ZIPs | `TALCON_Assistant_v2.0.0.zip` (40.7 MB source package) and `TALCON_Portable_v2.0.0.zip` (117.7 MB) created and entry-verified |

## Feature status — LIMITED / UNAVAILABLE (honestly)

| Feature | Status and real reason |
|---|---|
| Codex **live** Desktop monitoring | **Unavailable.** Codex Desktop exposes no supported local event interface that attaches to conversations already open in its UI. TALCON uses Codex's own session logs instead (verified working) and never invents events. |
| OpenCode events during downtime | **Limited by design.** SSE connections drop sometimes; events emitted while disconnected cannot be recovered (OpenCode's route is documented as experimental). |
| Kokoro model missing | **Unavailable until installed.** TALCON reports the missing files and stays silent; there is no Microsoft fallback. Run `python -m talcon.model_setup --kokoro`, then press **Refresh**. |
| Audible confirmation of speech | The Kokoro pipeline is verified programmatically; whether you *hear* it depends on your speakers/volume — test with the **Test voice** button. |
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
| “Python not found” during install | Install Python 3.11+ from python.org and re-run `Install TALCON.bat`. |
| No wake word | Voice Settings → **Download model**, check the microphone device, ensure "Wake detection" is on. |
| No speech | Voice Settings → pick a voice, click **Test voice**. |
| OpenCode “not running” | Start OpenCode Desktop, click **Test** in Application Monitoring (the port is found automatically). |
| “Authentication required” | OpenCode's local `service.json` is missing — reinstall/update OpenCode Desktop. |
| Want a fresh start | Delete `config\settings.json`, or run the uninstaller with the data option. |
| Logs | `logs\talcon.log` (rotating). |
