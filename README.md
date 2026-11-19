# TALCON — local Windows desktop assistant (v2.4.0)

TALCON is a local-first Windows 10/11 assistant with an animated face companion,
a 12-section control center, editable modes, fully offline wake-word and command
recognition, a local **Kokoro-82M** neural voice, an optional on-device Hermes
AI, and local Codex / OpenCode / DeepAI task monitors.

**Everything runs on your PC.** No paid APIs, no cloud speech, no account and no
subscriptions. Microphone audio and local files never leave the machine.

> The project folder and GitHub repository are still named `TALCON_Assistant` /
> `ai-assistance`; the application itself is **TALCON**.

## Quick start — which file do I click?

| You want to… | Click this |
|---|---|
| Install TALCON for the first time | **`Install TALCON.bat`** |
| Start TALCON every day | **`Launch TALCON.bat`** |
| Start it from a terminal | `run_talcon.bat` or `.venv\Scripts\python.exe main.py` |
| Remove TALCON completely | **`Uninstall TALCON.bat`** |

1. Extract the whole folder somewhere stable, e.g.
   `C:\Users\<you>\Apps\TALCON_Assistant`.
2. Install Python **3.11 or newer (64-bit)** from python.org and keep the
   default “py launcher” option ticked.
3. Double-click **`Install TALCON.bat`**. It creates the virtual environment,
   installs the dependencies and downloads both offline models:
   - Vosk recognition model (`python -m talcon.model_setup`)
   - Kokoro neural voice (`python -m talcon.model_setup --kokoro`)
4. Double-click **`Launch TALCON.bat`**. First launch opens the control center
   and a short setup wizard (microphone, voice, model check).

**Speech is Kokoro-only.** Microsoft SAPI and Windows voices are not used at
all. If the Kokoro model is missing, TALCON says so and stays silent instead of
falling back to a robotic voice.

## What is included

- **Animated face companion:** circular face with transparent corners, shown at
  about 60–70 px by default near the clock. Drag to reposition; the offset
  persists across restarts. Click-through outside the circle. Idle, listening,
  working, completed, error and speaking states at roughly 30 FPS.
- **Control center (12 sections):** Dashboard, History, Commands, Study Mode,
  Entertainment Mode, Stock Mode, Add your own mode, Voice Settings, Appearance,
  Application Monitoring, Cron Jobs and Startup Settings.
  - `—` minimises to the taskbar (TALCON keeps running; the face stays visible).
  - `↓` hides the window and face in the tray (voice and monitors keep running).
  - `×` performs a full, clean shutdown.
  - Closing the window never removes the startup setting.
- **Tray menu:** open the control center, show/hide the face, mute speech,
  activate modes, enable/disable startup and exit.
- **Voice (100% offline):**
  - Vosk wake-word detection for **“TALCON”**.
  - Vosk offline command recognition by default, with an optional
    **sherpa-onnx** streaming transducer behind the same interface. The
    engine is a setting (`auto`, `vosk` or `sherpa`) and Vosk remains the
    automatic fallback when the package or model is missing.
  - Shared audio conditioning before every recogniser: automatic gain,
    streaming noise suppression and optional reference echo suppression.
    The VAD still measures the raw microphone level.
  - Optional **faster-whisper** second pass for long questions only
    ("explain…", "what is…"); short commands stay on the streaming model.
  - **Kokoro-82M** neural speech only (free, Apache-2.0, CPU, about 118 MB).
    It loads while speaking and unloads after 90 seconds idle.
  - Saying “TALCON” answers “Yes, sir?”, then take a command.
  - Saying **“be quiet”** stops the current answer without muting future replies.
  - No Picovoice, no Porcupine, no cloud STT/TTS — ever.
- **Natural wording, still deterministic:** a local intent layer understands
  flexible phrasing such as “how many programs do I have open right now?”,
  “is Chrome up?”, “turn it up”, “speak slower”, “say that again” and
  “open it again”, without a language model. Exact phrases stay exact for
  safety, and local commands always run before any AI request.
- **AI answers through local Hermes (optional):** questions that are not local
  commands can be answered by a Hermes Agent installed on the same PC at
  `127.0.0.1` only. Speech and the answer appear together, dangerous tool
  requests become a spoken yes/no question, and repeated informational
  questions are served from a bounded RAM-only cache.
- **History page:** a session-only transcript of every completed response —
  what you asked, what TALCON answered and the expanded reader. Never written
  to disk; it disappears when TALCON exits.
- **Commands page:** every executed command with the transcript Vosk produced,
  the interpreted phrase, the action, execution time and confidence — the
  fastest way to diagnose a misheard command. Session-only.
- **Jarvis-style delivery:** short answers, interruptible speech, a calm British
  male Kokoro voice (George) by default, follow-up context (“close it”) and
  spoken volume/speed control.
- **Modes:** Study, Entertainment and Stock modes are voice-triggered and fully
  editable in the UI (greeting, URLs, applications — no hardcoded paths; the
  default browser is used). Custom modes can be added; example greeting:
  “Welcome back, sir. Your study environment is ready.”
- **OpenCode monitoring:** connects to the documented local
  `GET /api/event` Server-Sent Events route on `127.0.0.1` only, using the
  Basic-auth password OpenCode Desktop stores in
  `~/.config/opencode/service.json`, and discovers the dynamic desktop port from
  `%APPDATA%\ai.opencode.desktop\logs`. Real terminal events
  (`session.execution.succeeded/failed/interrupted/stopped`) plus busy→idle
  transitions are distinguished; dropped connections never fabricate a result.
- **Codex monitoring:** parses the real `~/.codex/sessions/**/*.jsonl.zst`
  rollout logs (zstd), distinguishing working / success / failure /
  interrupted, with per-monitor switches.
- **DeepAI Code monitoring:** reads DeepAI Code’s local
  `~/.deepai-code/userdata/statev2.sqlite` run states (read-only, local only)
  and announces completed, failed, interrupted or working tasks. Existing
  history is never replayed.
- **Cron jobs:** local scheduled actions with spoken completion and failure
  announcements.
- **Startup:** per-user Windows Startup shortcut (no admin), one toggle in the
  UI and a quiet background launch (`--startup`).
- **Single instance:** launching TALCON while it is already running reopens the
  control center of the running copy instead of starting a second one.

## Recent changes in this version

- **The AI backend now starts itself.** If Hermes is installed but its gateway
  is not running, TALCON launches the real Hermes gateway, waits for
  `127.0.0.1:8642` and then verifies the API key. A stopped gateway no longer
  produces "cannot reach the backend" for an ordinary question.
- **Streamed AI answers.** Chat completions are streamed, and the first
  sentence is spoken while the rest is still being generated. History records
  the full answer exactly once.
- **AI details in Voice Settings:** host, port and model fields, an automatic
  start switch, a streaming switch, "Start Hermes" and "Test connection".
- **User data moved out of OneDrive.** Settings, logs and cron history now live
  in `%LOCALAPPDATA%\TALCON` (existing files are migrated once). Models stay in
  the project folder.
- **Faster UI:** the slow `schtasks.exe` startup probe is cached, microphone
  enumeration is cached, status updates are coalesced, the duplicate window
  stylesheet was removed and no-op settings saves no longer touch the disk.
- **Quieter when idle:** the face animates at 30 FPS only while it is doing
  something, the preview stops when hidden, the cron page stops polling and
  the preview window only follows the avatar while it has something to show.
- **Responsive voice:** smaller audio blocks and a 1.0 s trailing-silence
  default cut roughly half a second from every spoken command.
- The window remembers its size, position and last page; `Ctrl+1…9` jump to a
  section, `Esc` hides to the tray, and `Ctrl+Alt+Space` starts listening from
  any application (system-wide hotkey).
- Background work (speech, recognition, AI, monitors) runs below normal
  priority; native crashes are recorded in `logs/crash.log` via faulthandler.
- Kokoro is the only speech engine; the Microsoft SAPI/WinRT backends,
  Narrator probing and every Windows-voice fallback were removed.
- Saved settings are migrated to Kokoro automatically; old values such as
  `windows`, `sapi` or `auto` now normalise to `kokoro`.
- Voice Settings and the setup wizard describe the real Kokoro path and report
  missing model files honestly.
- The installer now downloads the Kokoro voice as well as the Vosk model.
- Added the local intent layer for flexible phrasing, the History and Commands
  pages, response preview, local Hermes AI answers and the RAM-only answer cache.
- Added DeepAI Code monitoring alongside Codex and OpenCode.
- README, user guide, benchmark script and tests were updated for the
  single-engine speech path.

## Important limitation — Codex Desktop live monitoring

Codex Desktop has no supported local event interface that attaches to
conversations already open in its UI. TALCON therefore reads Codex’s own session
rollout logs instead (verified against real logs on this machine). TALCON never
invents events and never infers completion from a process exiting. If the
log-based integration cannot run, Monitoring says so plainly with the real
blocker.

## Voice commands (starter set)

- “Activate my study mode” / “Get ready for my entertainment mode”
- “Mute” / “Unmute” / “Be quiet” (stops the current answer only)
- “What mode?”
- “Open settings”
- “Hide TALCON” / “Show TALCON”
- “Exit TALCON”
- “How many programs do I have open right now?”
- “Is Chrome running?” / “Do I have Spotify open?”
- “Volume 40” / “Turn it up” / “Speak slower”
- “Say that again” / “Open it again”
- Anything else that reads as a question or request goes to the local Hermes AI
  when AI answers are enabled.

## Privacy

- Microphone audio is processed in memory, locally, and never written to disk.
- Speech recognition and speech synthesis are fully offline.
- AI text goes only to Hermes on `127.0.0.1`; it refuses any other address.
- History, the command log and the answer cache live in RAM only.
- The only network traffic is the one-time model download you start and
  localhost connections to your own OpenCode/Hermes services.

## Uninstall

Double-click **`Uninstall TALCON.bat`** (or use the button in Startup Settings).
It always removes the Windows startup entry and stops TALCON, then asks whether
to delete the downloaded models, settings/logs and the Python environment.
Finally delete the folder yourself.

## Face placement and animation

The original supplied GIF is kept at `assets/user_face_original.gif`; the runtime
uses `assets/talcon_face.gif` (circular face only, transparent surroundings).
Adjust size and position in Appearance or by dragging. The overlay is
click-through outside the circle so it never blocks clicks.

## Speech recognition engines and measurement

The recognition engine is selected in **Voice Settings → Recognition engine**
or with `asr_engine` in settings:

- `auto` — the measured, reliable Vosk command path. Installing sherpa-onnx
  does not silently change command behaviour.
- `vosk` — the bundled small English model only.
- `sherpa` — explicitly opt in to sherpa-onnx; if it cannot load, the voice
  pipeline logs the reason and continues on Vosk instead of losing the
  microphone.

Install the optional engine and model from a terminal:

```bat
.venv\Scripts\pip.exe install sherpa-onnx
.venv\Scripts\python.exe -m talcon.model_setup --sherpa
```

The benchmark decodes identical audio with every available engine and reports
word error rate and latency at simulated 0.5 m, 2 m and 3 m distances:

```bat
.venv\Scripts\python.exe scripts\benchmark_speech.py
.venv\Scripts\python.exe scripts\benchmark_speech.py --engine both --distances 0.5,2,3
.venv\Scripts\python.exe scripts\benchmark_speech.py --engine parakeet --parakeet-model models\parakeet
```

`--engine parakeet` is experimental and batch-only; it is never selected by
the live voice loop. Results are written to `logs/speech_benchmark.json`
(WER, action accuracy, mean/p95 latency, real-time factor and the conditioned
audio settings used).

A local run of the synthetic command corpus (Kokoro speech, AGC + noise
suppression, 55 utterances per distance) measured:

| Engine | WER 0.5 m | WER 2 m | WER 3 m | Action accuracy 0.5 m | Mean latency |
| --- | --- | --- | --- | --- | --- |
| Vosk small | 0.29 | 0.52 | 0.52 | 0.73 | ~0.11 s |
| sherpa-onnx zipformer | 0.56 | 0.66 | 0.71 | 0.07 | ~0.30 s |

That corpus is command-heavy and synthetic, so Vosk wins there because its
phrase list biases decoding. Run the benchmark on your own microphone and
phrasing before changing `asr_engine`; sherpa-onnx is still useful for
free-form speech, and Vosk remains the safe automatic fallback.

The optional long-question pass needs `faster-whisper` and is disabled by
default (`long_question_whisper`). It never runs for commands: only transcripts
starting with phrases such as "explain", "what is" or "tell me about" are
eligible.

## Engineering notes

- Tested on this machine: Python 3.14.5, PySide6 6.12, vosk 0.3.45,
  kokoro-onnx 0.4.7, onnxruntime 1.32.
- Full test suite: **737 pytest tests pass**.
- Verified on-machine: real wake-word detections, real OpenCode SSE
  connection, real Codex log parsing, audible Kokoro playback and
  install/launch/uninstall round-trips.
- Kokoro runs on the CPU. Model load is roughly 2.4 s, and synthesis on this
  laptop is around 0.9× real-time, so short replies start quickly while long
  answers take a little longer to speak.
- OpenCode’s `/api/event` route is documented as experimental; reconnection
  cannot recover events missed while disconnected.
- All monitor connections are restricted to `127.0.0.1` / localhost.
- Run TALCON as a normal user, never as Administrator.
- Settings, modes, cron jobs and rotating logs live in
  `%LOCALAPPDATA%\TALCON` (the project's `config/` folder is only a legacy
  location that is migrated once); the offline models stay in `models/`
  (Vosk and Kokoro, both Apache License 2.0).

## Folder layout

```text
TALCON_Assistant/
  Install TALCON.bat      # one-click installer (deps + Vosk + Kokoro)
  Launch TALCON.bat       # daily launcher
  run_talcon.bat          # alternative launcher
  Uninstall TALCON.bat    # one-click uninstaller
  assets/                 # face GIFs and tray icon
  config/                 # settings and mode definitions
  logs/                   # rotating local logs
  models/                 # offline Vosk + Kokoro models
  talcon/                   # UI, face overlay, voice, intent, AI, monitors
  scripts/                # startup shortcut, speech benchmark
  tests/                  # 737 automated tests (pytest)
  USER_GUIDE.md           # full user guide and feature status
  setup.ps1               # venv + dependency installation
  main.py                 # entry point
```
