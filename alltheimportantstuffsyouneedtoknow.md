can you give me some modifications that i should make to make it fast, efficent and also the cmds that i am giving is still not upto the mark and i want to make it like jarvis like iron man and do you have any recommendations on how can i make the voice of ai to look more human like like jarvis or chatgpt voice mode without using money and also not not consuming too much of resources of my laptop as because i dont have a high end laptop just give me the recommendations and the advice i will tell with which one you should continue

## Assistant

I’ll keep this advisory-only. I’ll base the recommendations on TALCON’s current architecture and verify the lightweight voice-engine options against their official documentation, then give you a short prioritized menu so you can choose what to build next.

## Activity

    Searched the web for Piper TTS GitHub Rhasspy official lightweight local text to speech Windows, Kokoro TTS GitHub official lightweight CPU local text to speech, Microsoft Windows natural voices offline text to speech official documentation, Vosk API offline speech recognition grammar official GitHub

## Assistant

Yes. My recommendation is to make TALCON feel more “Jarvis-like” through responsiveness and command reliability first—not by adding a heavy model. On a modest laptop, that will make the biggest difference.

My suggested priority:

1. Improve command understanding — do this first

The present weak point is not just recognition; it is the large, rigid command grammar. TALCON currently carries hundreds of app phrases, which makes a small offline Vosk model more likely to mishear natural wording.

I recommend a three-layer command system:

- Exact safety commands: “stop,” “hide yourself,” “shutdown,” etc.
- Intent patterns with flexible fillers: understand “how many programs do I have open right now?” as the same intent as “how many apps are running.”
- Alias learning: when TALCON misunderstands an app name, let you correct it once and retain the spoken alias locally.

Add a “What I heard” line and a “Did you mean…” confirmation only when confidence is low. This makes it feel much more intelligent without using more CPU.

2. Create a real Jarvis interaction style

This is mostly software behavior, not a large AI model:

- Short acknowledgements: “On it,” “Checking,” “Done.”
- Interruptible speech: saying “stop” immediately halts TALCON.
- Follow-up context: “Open Chrome” → “close it.”
- A command history panel showing transcript, interpreted intent, result, and duration.
- Clear states: listening, thinking, acting, waiting for confirmation, complete.
- Avoid speaking long answers by default; give a concise answer first, then offer: “Would you like more detail?”

3. Keep the current Windows Natural Voice as the default

For zero cost and minimal laptop use, this is the best starting option. Windows Natural Voices run on-device after installation and do not need an internet connection. Install and preview a few through Windows Narrator voice settings; TALCON already supports Windows voices. [Microsoft Support](https://support.microsoft.com/en-us/servicing/os/configuration-updates/2024/01/february-29-2024-windows-configuration-update)

To make it sound less robotic, I would tune delivery rather than replace the engine immediately:

- Slightly slower rate
- Shorter sentence chunks
- SSML pauses after greetings, actions, and confirmations
- A consistent calm “assistant” writing style
- Avoid long technical paragraphs being read verbatim

4. Optional upgrade: local neural voice

If you want more natural speech than Windows voices:

| Option | Quality | Laptop impact | Recommendation |
|---|---:|---:|---|
| Windows Natural Voice | Good | Very low | Best default |
| Piper | Fair–good | Low | Best lightweight neural option |
| Kokoro | Very good | Medium | Best “wow” upgrade if laptop handles it |
| Pocket TTS | Promising | Medium | Experimental; defer for now |

Piper is designed as a fast local neural TTS system and is the safer low-resource experiment. Kokoro generally sounds more expressive, but it has a larger runtime/model cost. [Rhasspy/Piper](https://github.com/rhasspy), [Kokoro local HTTP API](https://github.com/hangry-labs/kokoroTTS), [Pocket TTS](https://github.com/kyutai-labs/pocket-tts)

My preference for your laptop: test Windows Natural Voice tuning first, then add Piper as an optional selectable backend. Only add Kokoro if you specifically prefer voice realism over memory/CPU use.

5. Performance improvements worth making

- Keep Vosk for command mode; do not run a larger speech model continuously.
- Use push-to-talk/manual listen for longer natural-language questions.
- Keep app discovery cached; refresh only on request or every few minutes.
- Split startup into stages: face/UI first, monitors next, voice model last.
- Reduce verbose microphone logging in normal mode.
- Keep AI answers on direct chat, as now; reserve agent/tool workflows for explicit requests.
- Add a lightweight health panel: microphone, recognition, TTS, AI, Codex monitor, and app catalogue status.

What I recommend we do next, in order:

1. Flexible command-intent layer plus alias-learning.
2. Jarvis-style response and voice-delivery tuning.
3. Diagnostic/history panel for misunderstood commands.
4. Optional Piper voice backend.
5. Only then evaluate Kokoro.

Tell me which number you want to continue with.

## User

well then implement 1. 2. for 3. i have already implemented windows tts but it is still to robotic and no emotions at the time of speaking back for 4. i want you analyse my whole laptop hardware and then implement the best one possible for 5, dont implement '- Use push-to-talk/manual listen for longer natural-language questions.'

## Assistant

I’ll implement the command-intent/alias layer and Jarvis-style interaction first, then inspect the actual CPU, RAM, GPU, and available storage before choosing a free local voice backend. I’ll keep continuous listening unchanged—no push-to-talk change. After the hardware check, I’ll install only the voice option your machine can run comfortably and wire it into TALCON.

## Activity

    $ Write-Output '--- CPU ---'; Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed; Write-Output '--- Memory ---'; Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory; Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory,TotalVisibleMemorySize; Write-Output '--- GPU ---'; Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor; Write-Output '--- Disk capacity ---'; Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,Size,FreeSpace,FileSystem; Write-Output '--- OS ---'; Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture; Write-Output '--- Current speech architecture ---'; rg -n -C 4 'class SpeechOutput|WindowsNaturalVoiceBackend|SapiBackend|create_backend|set_backend|say\(|build_ssml|speech_backend' talcon/speech.py talcon/runtime.py talcon/ui/main_window.py talcon/config.py; Write-Output '--- Command parsing and alias persistence ---'; rg -n -C 4 'normalize_phrase|polite|_LEADING|alias|extract_.*command|handle\(' talcon/commands.py talcon/app_catalog.py talcon/runtime.py tests/test_commands.py tests/test_running_apps.py
    --- CPU ---
    Get-CimInstance : Access denied
    At line:2 char:29
    + Write-Output '--- CPU ---'; Get-CimInstance Win32_Processor | Select- ...
    +                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (root\cimv2:Win32_Processor:String) [Get-CimInstance], CimException
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    --- Memory ---
    Get-CimInstance : Access denied
    At line:2 char:168
    + ... Output '--- Memory ---'; Get-CimInstance Win32_ComputerSystem | Selec ...
    +                              ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (root\cimv2:Win32_ComputerSystem:String) [Get-CimInstance], CimExcepti
       on
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    Get-CimInstance : Access denied
    At line:2 char:242
    + ... ct TotalPhysicalMemory; Get-CimInstance Win32_OperatingSystem | Selec ...
    +                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (root\cimv2:Win32_OperatingSystem:String) [Get-CimInstance], CimExcept
       ion
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    --- GPU ---
    Get-CimInstance : Access denied
    At line:2 char:367
    + ... e-Output '--- GPU ---'; Get-CimInstance Win32_VideoController | Selec ...
    +                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (root\cimv2:Win32_VideoController:String) [Get-CimInstance], CimExcept
       ion
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    --- Disk capacity ---
    Get-CimInstance : Access denied
    At line:2 char:505
    + ... acity ---'; Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ...
    +                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (:) [Get-CimInstance], CimException
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    --- OS ---
    Get-CimInstance : Access denied
    At line:2 char:640
    + ... te-Output '--- OS ---'; Get-CimInstance Win32_OperatingSystem | Selec ...
    +                             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        + CategoryInfo          : PermissionDenied: (root\cimv2:Win32_OperatingSystem:String) [Get-CimInstance], CimExcept
       ion
        + FullyQualifiedErrorId : HRESULT 0x80041003,Microsoft.Management.Infrastructure.CimCmdlets.GetCimInstanceCommand
    
    --- Current speech architecture ---
    talcon/ui/main_window.py-1188-        self.volume_slider.setValue(int(s.get("speech_volume", 90)))
    talcon/ui/main_window.py-1189-        self.volume_slider.blockSignals(False)
    talcon/ui/main_window.py-1190-        self._on_volume_changed(int(s.get("speech_volume", 90)))
    talcon/ui/main_window.py-1191-
    talcon/ui/main_window.py:1192:        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))
    talcon/ui/main_window.py-1193-
    talcon/ui/main_window.py-1194-        self.size_slider.blockSignals(True)
    talcon/ui/main_window.py-1195-        self.size_slider.setValue(int(s.get("face_size", 66)))
    talcon/ui/main_window.py-1196-        self.size_slider.blockSignals(False)
    --
    talcon/ui/main_window.py-1214-            details = self.runtime.speech_voice_details()
    talcon/ui/main_window.py-1215-        engine = (
    talcon/ui/main_window.py-1216-            self.runtime.speech_engine_info()
    talcon/ui/main_window.py-1217-            if hasattr(self.runtime, "speech_engine_info")
    talcon/ui/main_window.py:1218:            else {"requested": s.get("speech_backend", BACKEND_AUTO)}
    talcon/ui/main_window.py-1219-        )
    talcon/ui/main_window.py-1220-        self._populate_speech_voices(details, engine)
    talcon/ui/main_window.py-1221-        self._populate_microphones()
    talcon/ui/main_window.py-1222-        self._refresh_model_status()
    --
    talcon/ui/main_window.py-1282-            # boxes auto-save on valueChanged).
    talcon/ui/main_window.py-1283-            widget.blockSignals(True)
    talcon/ui/main_window.py-1284-            widget.setValue(float(s.get(key, default)))
    talcon/ui/main_window.py-1285-            widget.blockSignals(False)
    talcon/ui/main_window.py:1286:        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))
    talcon/ui/main_window.py-1287-        self._sync_menu_face_controls(s)
    talcon/ui/main_window.py-1288-        self._update_mute_labels()
    talcon/ui/main_window.py-1289-
    talcon/ui/main_window.py-1290-    def _on_settings_changed(self, settings: dict) -> None:
    --
    talcon/ui/main_window.py-1460-    def _on_engine_chosen(self, index: int) -> None:
    talcon/ui/main_window.py-1461-        key = self.engine_combo.itemData(index)
    talcon/ui/main_window.py-1462-        if not key:
    talcon/ui/main_window.py-1463-            return
    talcon/ui/main_window.py:1464:        if hasattr(self.runtime, "set_speech_backend"):
    talcon/ui/main_window.py:1465:            self.runtime.set_speech_backend(key)
    talcon/ui/main_window.py-1466-        else:
    talcon/ui/main_window.py:1467:            self._save({"speech_backend": key})
    talcon/ui/main_window.py-1468-
    talcon/ui/main_window.py-1469-    def _on_voice_chosen(self, index: int) -> None:
    talcon/ui/main_window.py-1470-        voice_id = self.voice_combo.itemData(index)
    talcon/ui/main_window.py-1471-        if not voice_id:
    --
    talcon/config.py-101-    # Voice conversations need a prompt answer.  Hermes agent runs use an SSE
    talcon/config.py-102-    # tool-workflow which is useful for explicit automation, but can remain
    talcon/config.py-103-    # open without a final answer.  Use the normal chat endpoint by default.
    talcon/config.py-104-    "ai_use_agent_runs": False,
    talcon/config.py:105:    "speech_backend": "auto",
    talcon/config.py-106-    "speech_voice": "",
    talcon/config.py-107-    "speech_voice_id": "",
    talcon/config.py-108-    "speech_voice_ids": {},
    talcon/config.py-109-    "speech_rate": 0,
    --
    talcon/config.py-289-    # Imported lazily so configuration stays usable even if the speech module
    talcon/config.py-290-    # is unavailable for some reason.
    talcon/config.py-291-    from .speech import normalize_backend
    talcon/config.py-292-
    talcon/config.py:293:    merged["speech_backend"] = normalize_backend(merged.get("speech_backend"))
    talcon/config.py-294-    merged["speech_voice"] = str(merged.get("speech_voice") or "").strip()
    talcon/config.py-295-    merged["speech_voice_id"] = str(merged.get("speech_voice_id") or "").strip()
    talcon/config.py-296-    # Keep one saved voice per synthesis API.  A WinRT registry token is not a
    talcon/config.py-297-    # SAPI voice name, so a single field would overwrite the other engine's
    --
    talcon/speech.py-2-
    talcon/speech.py-3-Two synthesis backends are supported, both driven through a small persistent
    talcon/speech.py-4-PowerShell host so no Python WinRT package is required:
    talcon/speech.py-5-
    talcon/speech.py:6:* :class:`WindowsNaturalVoiceBackend` uses the public Windows Runtime API
    talcon/speech.py-7-  ``Windows.Media.SpeechSynthesis.SpeechSynthesizer``.  It synthesises to an
    talcon/speech.py-8-  in-memory ``SpeechSynthesisStream`` and plays it with ``SoundPlayer``.
    talcon/speech.py:9:* :class:`SapiBackend` keeps the original ``System.Speech`` engine as a
    talcon/speech.py-10-  compatibility fallback.  It can also expose a natural voice if Windows (or
    talcon/speech.py-11-  an installed SAPI adapter) registers one there.
    talcon/speech.py-12-
    talcon/speech.py-13-Everything happens locally: no cloud service, API key or downloaded model is
    --
    talcon/speech.py-421-            merged.append(part)
    talcon/speech.py-422-    return merged
    talcon/speech.py-423-
    talcon/speech.py-424-
    talcon/speech.py:425:def build_ssml(text: str, lang: str = "en-US") -> str:
    talcon/speech.py-426-    """Build conservative, well-formed SSML in Windows' supported profile.
    talcon/speech.py-427-
    talcon/speech.py-428-    The public ``SynthesizeSsmlToStreamAsync`` API rejects ``version="1.1"``
    talcon/speech.py-429-    on current Windows builds, so TALCON emits the SSML 1.0 profile: the same
    --
    talcon/speech.py-458-    return {
    talcon/speech.py-459-        "text": str(text or ""),
    talcon/speech.py-460-        "voice_id": str(voice_id or ""),
    talcon/speech.py-461-        "lang": str(lang or "en-US"),
    talcon/speech.py:462:        "ssml": build_ssml(text, lang) if ssml else "",
    talcon/speech.py-463-        "rate": max(0.5, min(1.5, float(rate))),
    talcon/speech.py-464-        "volume": max(0.0, min(1.0, float(volume))),
    talcon/speech.py-465-    }
    talcon/speech.py-466-
    --
    talcon/speech.py-1085-
    talcon/speech.py-1086-    def has_natural_voice(self, timeout: float = 10.0) -> bool:
    talcon/speech.py-1087-        return any(voice.natural for voice in self.voice_details(timeout))
    talcon/speech.py-1088-
    talcon/speech.py:1089:    def _send_say(self, payload: dict, text: str, timeout: float | None = None) -> bool:
    talcon/speech.py-1090-        if not self.ensure_started():
    talcon/speech.py-1091-            return False
    talcon/speech.py-1092-        encoded = base64.b64encode(
    talcon/speech.py-1093-            json.dumps(payload, ensure_ascii=False).encode("utf-8")
    --
    talcon/speech.py-1127-        """Terminate the host: this immediately stops any current utterance."""
    talcon/speech.py-1128-        self.stop()
    talcon/speech.py-1129-
    talcon/speech.py-1130-
    talcon/speech.py:1131:class SapiBackend(_PowerShellHost):
    talcon/speech.py-1132-    """Persistent Windows PowerShell + ``System.Speech`` process."""
    talcon/speech.py-1133-
    talcon/speech.py-1134-    script = _SAPI_PS_SCRIPT
    talcon/speech.py-1135-    reader_name = "TALCON-SAPI"
    talcon/speech.py-1136-    backend_kind = BACKEND_SAPI
    talcon/speech.py-1137-
    talcon/speech.py:1138:    def say(self, text: str, timeout: float | None = None) -> bool:
    talcon/speech.py-1139-        if not self.ensure_started():
    talcon/speech.py-1140-            return False
    talcon/speech.py-1141-        payload = {"text": str(text), "voice_id": self._pending_voice}
    talcon/speech.py:1142:        return self._send_say(payload, text, timeout)
    talcon/speech.py-1143-
    talcon/speech.py-1144-
    talcon/speech.py:1145:class WindowsNaturalVoiceBackend(_PowerShellHost):
    talcon/speech.py-1146-    """Persistent PowerShell host around the Windows Runtime speech API."""
    talcon/speech.py-1147-
    talcon/speech.py-1148-    script = _WINRT_PS_SCRIPT
    talcon/speech.py-1149-    reader_name = "TALCON-WinRT"
    --
    talcon/speech.py-1154-
    talcon/speech.py-1155-    def _volume_value(self, volume: int) -> str:
    talcon/speech.py-1156-        return f"{max(0.0, min(1.0, int(volume) / 100.0)):.2f}"
    talcon/speech.py-1157-
    talcon/speech.py:1158:    def say(self, text: str, timeout: float | None = None) -> bool:
    talcon/speech.py-1159-        if not self.ensure_started():
    talcon/speech.py-1160-            return False
    talcon/speech.py-1161-        if self._pending_voice:
    talcon/speech.py-1162-            ok, _voice_id, error = self.wait_voice(3.0)
    --
    talcon/speech.py-1172-            lang=self._pending_voice_language or "en-US",
    talcon/speech.py-1173-            rate=rate,
    talcon/speech.py-1174-            volume=volume,
    talcon/speech.py-1175-        )
    talcon/speech.py:1176:        ok = self._send_say(payload, text, timeout)
    talcon/speech.py-1177-        used = self.last_synthesis
    talcon/speech.py-1178-        if ok and used.get("mode") == "text" and used.get("fallback"):
    talcon/speech.py-1179-            log.warning("SSML synthesis failed; plain text was used: %s", used["fallback"])
    talcon/speech.py-1180-        return ok
    talcon/speech.py-1181-
    talcon/speech.py-1182-
    talcon/speech.py:1183:def create_backend(kind: str, on_error: Callable[[str], None] | None = None) -> _PowerShellHost:
    talcon/speech.py-1184-    """Create a backend without probing it."""
    talcon/speech.py-1185-    if normalize_backend(kind) == BACKEND_WINDOWS:
    talcon/speech.py:1186:        return WindowsNaturalVoiceBackend(on_error=on_error)
    talcon/speech.py:1187:    return SapiBackend(on_error=on_error)
    talcon/speech.py-1188-
    talcon/speech.py-1189-
    talcon/speech.py-1190-# -- Narrator-only voice discovery ----------------------------------------
    talcon/speech.py-1191-_NARRATOR_CACHE: list[dict] | None = None
    --
    talcon/speech.py-1307-        log.exception("Could not list voices for %s", type(backend).__name__)
    talcon/speech.py-1308-        return []
    talcon/speech.py-1309-
    talcon/speech.py-1310-
    talcon/speech.py:1311:class SpeechOutput:
    talcon/speech.py-1312-    """Thread-safe, muting-aware queue in front of a Windows speech backend."""
    talcon/speech.py-1313-
    talcon/speech.py-1314-    def __init__(self, enabled: bool = True, on_speaking=None, on_error=None,
    talcon/speech.py-1315-                 backend=None, backend_kind: str = BACKEND_AUTO,
    --
    talcon/speech.py-1388-            summary += " (SSML fell back to plain text)"
    talcon/speech.py-1389-        return summary
    talcon/speech.py-1390-
    talcon/speech.py-1391-    def _new_backend(self, kind: str):
    talcon/speech.py:1392:        return create_backend(kind, on_error=self._handle_backend_error)
    talcon/speech.py-1393-
    talcon/speech.py-1394-    def _build_backend(self, requested: str) -> tuple:
    talcon/speech.py-1395-        """Create and probe backends without holding the state lock.
    talcon/speech.py-1396-
    --
    talcon/speech.py-1490-                except Exception:
    talcon/speech.py-1491-                    log.exception("Could not apply speech settings to the backend")
    talcon/speech.py-1492-                return backend
    talcon/speech.py-1493-
    talcon/speech.py:1494:    def set_backend(self, kind: str) -> None:
    talcon/speech.py-1495-        """Switch backend. The new host starts lazily on the next request."""
    talcon/speech.py-1496-        normalized = normalize_backend(kind)
    talcon/speech.py-1497-        with self._backend_lock:
    talcon/speech.py-1498-            if normalized == self._requested_backend and self._backend is not None:
    --
    talcon/speech.py-1654-            pass
    talcon/speech.py-1655-        with self._pending_lock:
    talcon/speech.py-1656-            self._pending = 0
    talcon/speech.py-1657-
    talcon/speech.py:1658:    def say(self, text: str) -> None:
    talcon/speech.py-1659-        if not self.enabled or not text:
    talcon/speech.py-1660-            return
    talcon/speech.py-1661-        full_text = str(text)
    talcon/speech.py-1662-        speech_text = spoken_excerpt(full_text)
    --
    talcon/speech.py-1677-    def say_and_wait(self, text: str, timeout: float = 30.0) -> bool:
    talcon/speech.py-1678-        """Queue an announcement and block until playback has really finished."""
    talcon/speech.py-1679-        if not self.enabled or not text:
    talcon/speech.py-1680-            return True
    talcon/speech.py:1681:        self.say(text)
    talcon/speech.py-1682-        deadline = time.monotonic() + timeout
    talcon/speech.py-1683-        while time.monotonic() < deadline:
    talcon/speech.py-1684-            with self._pending_lock:
    talcon/speech.py-1685-                pending = self._pending
    --
    talcon/speech.py-1773-                    self._on_utterance(full_text)
    talcon/speech.py-1774-                except Exception:
    talcon/speech.py-1775-                    log.exception("Utterance callback failed")
    talcon/speech.py-1776-                backend = self._ensure_backend()
    talcon/speech.py:1777:                ok = backend.say(speech_text)
    talcon/speech.py-1778-                if ok is False:
    talcon/speech.py-1779-                    self._report_error(
    talcon/speech.py-1780-                        getattr(backend, "last_error", "") or "Speech synthesis failed."
    talcon/speech.py-1781-                    )
    --
    talcon/speech.py-1820-    lines: list[str] = []
    talcon/speech.py-1821-    lines.append("TALCON Windows voice diagnostic")
    talcon/speech.py-1822-    lines.append("=" * 40)
    talcon/speech.py-1823-
    talcon/speech.py:1824:    winrt = WindowsNaturalVoiceBackend()
    talcon/speech.py-1825-    try:
    talcon/speech.py-1826-        winrt_voices = _backend_voice_list(winrt)
    talcon/speech.py-1827-        lines.append("")
    talcon/speech.py-1828-        lines.append(f"Windows Runtime (WinRT) voices: {len(winrt_voices)}")
    --
    talcon/speech.py-1832-            lines.append("  (none exposed by Windows.Media.SpeechSynthesis)")
    talcon/speech.py-1833-    finally:
    talcon/speech.py-1834-        winrt.stop()
    talcon/speech.py-1835-
    talcon/speech.py:1836:    sapi = SapiBackend()
    talcon/speech.py-1837-    try:
    talcon/speech.py-1838-        sapi_voices = _backend_voice_list(sapi)
    talcon/speech.py-1839-        lines.append("")
    talcon/speech.py-1840-        lines.append(f"SAPI (System.Speech) voices: {len(sapi_voices)}")
    --
    talcon/runtime.py-115-            enabled=not self.muted,
    talcon/runtime.py-116-            on_speaking=self._on_speech_state,
    talcon/runtime.py-117-            on_error=self._on_speech_error,
    talcon/runtime.py-118-            on_utterance=self._on_utterance,
    talcon/runtime.py:119:            backend_kind=self.settings.get("speech_backend", BACKEND_AUTO),
    talcon/runtime.py-120-        )
    talcon/runtime.py-121-        self.modes = ModeManager(self.modes_data, self.speech)
    talcon/runtime.py-122-        self.commands = CommandRouter(self.modes, self.speech, self)
    talcon/runtime.py-123-        self.commands.register_modes(self.modes_data)
    --
    talcon/runtime.py-641-            self._set_face("working")
    talcon/runtime.py-642-            self._set_status("last_event", f"{source} is working on a task.")
    talcon/runtime.py-643-        elif kind == "success":
    talcon/runtime.py-644-            self._set_face("success")
    talcon/runtime.py:645:            self.speech.say("Sir, " + message)
    talcon/runtime.py-646-            self._set_status("last_event", f"{source}: {message}")
    talcon/runtime.py-647-        elif kind == "failure":
    talcon/runtime.py-648-            self._set_face("error")
    talcon/runtime.py:649:            self.speech.say("Sir, " + message)
    talcon/runtime.py-650-            self._set_status("last_event", f"{source}: {message}")
    talcon/runtime.py-651-        elif kind == "interrupted":
    talcon/runtime.py-652-            self._set_face("idle")
    talcon/runtime.py-653-            if self.settings.get("announce_interruptions", False):
    talcon/runtime.py:654:                self.speech.say("Sir, " + message)
    talcon/runtime.py-655-            self._set_status("last_event", f"{source}: {message}")
    talcon/runtime.py-656-
    talcon/runtime.py-657-    # -- commands callable from the service layer -------------------------
    talcon/runtime.py-658-    def mode_title(self, name: str) -> str:
    --
    talcon/runtime.py-938-            return dict(self.speech.engine_info())
    talcon/runtime.py-939-        except Exception:
    talcon/runtime.py-940-            log.exception("Could not read speech engine information")
    talcon/runtime.py-941-            return {
    talcon/runtime.py:942:                "requested": self.settings.get("speech_backend", BACKEND_AUTO),
    talcon/runtime.py-943-                "requested_label": SPEECH_BACKEND_LABELS.get(
    talcon/runtime.py:944:                    self.settings.get("speech_backend", BACKEND_AUTO), "Auto"
    talcon/runtime.py-945-                ),
    talcon/runtime.py-946-                "active": "",
    talcon/runtime.py-947-                "active_label": "Not started yet",
    talcon/runtime.py-948-                "natural_available": False,
    talcon/runtime.py-949-                "note": "",
    talcon/runtime.py-950-            }
    talcon/runtime.py-951-
    talcon/runtime.py:952:    def set_speech_backend(self, kind: str) -> None:
    talcon/runtime.py-953-        normalized = normalize_backend(kind)
    talcon/runtime.py:954:        if normalized == self.settings.get("speech_backend", BACKEND_AUTO):
    talcon/runtime.py-955-            return
    talcon/runtime.py:956:        self.save_settings({"speech_backend": normalized})
    talcon/runtime.py-957-
    talcon/runtime.py-958-    def choose_speech_voice(self, voice_id: str, name: str = "") -> None:
    talcon/runtime.py-959-        """Persist and apply a voice chosen from the real installed list."""
    talcon/runtime.py-960-        voice_id = str(voice_id or "").strip()
    --
    talcon/runtime.py-1066-        self._set_status("pending_action", PENDING_LABELS["close_all"])
    talcon/runtime.py-1067-        self._set_status("pending_remaining", "15 s")
    talcon/runtime.py-1068-        self._set_status("last_result", message)
    talcon/runtime.py-1069-        self._record_activity("Close-all requested; waiting for confirmation")
    talcon/runtime.py:1070:        self.speech.say(message)
    talcon/runtime.py-1071-        return True, message
    talcon/runtime.py-1072-
    talcon/runtime.py-1073-    def confirm_close_all(self) -> tuple[bool, str]:
    talcon/runtime.py-1074-        if not self.pending.confirm("close_all"):
    talcon/runtime.py-1075-            message = "There is no close-all request waiting for confirmation, sir."
    talcon/runtime.py:1076:            self.speech.say(message)
    talcon/runtime.py-1077-            return False, message
    talcon/runtime.py-1078-        self._set_status("pending_action", "None")
    talcon/runtime.py-1079-        self._set_status("pending_remaining", "")
    talcon/runtime.py-1080-        return self.close_all_now()
    --
    talcon/runtime.py-1093-            self._last_close_result = message
    talcon/runtime.py-1094-            self._set_status("last_close_result", message)
    talcon/runtime.py-1095-            self._set_status("last_result", message)
    talcon/runtime.py-1096-            self._set_status("last_event", message)
    talcon/runtime.py:1097:            self.speech.say(message)
    talcon/runtime.py-1098-            return False, message
    talcon/runtime.py-1099-
    talcon/runtime.py-1100-        self._last_close_result = result.summary()
    talcon/runtime.py-1101-        self._last_closed = [window.label for window in result.closed]
    --
    talcon/runtime.py-1108-        self._set_status("last_refused", list(self._last_refused))
    talcon/runtime.py-1109-        self._set_status("last_result", message)
    talcon/runtime.py-1110-        self._set_status("last_event", message)
    talcon/runtime.py-1111-        self._record_activity(message)
    talcon/runtime.py:1112:        self.speech.say(message)
    talcon/runtime.py-1113-        return not result.errors, message
    talcon/runtime.py-1114-
    talcon/runtime.py-1115-    # -- running applications ---------------------------------------------
    talcon/runtime.py-1116-    def running_applications(self) -> list:
    --
    talcon/runtime.py-1171-            message = "Close-all cancelled, sir. Nothing was closed."
    talcon/runtime.py-1172-        else:
    talcon/runtime.py-1173-            message = "There is no pending close-all request, sir."
    talcon/runtime.py-1174-        self._set_status("last_result", message)
    talcon/runtime.py:1175:        self.speech.say(message)
    talcon/runtime.py-1176-        return cancelled, message
    talcon/runtime.py-1177-
    talcon/runtime.py-1178-    def request_shutdown(self) -> tuple[bool, str]:
    talcon/runtime.py-1179-        self.pending.request("shutdown")
    --
    talcon/runtime.py-1181-        self._set_status("pending_action", PENDING_LABELS["shutdown"])
    talcon/runtime.py-1182-        self._set_status("pending_remaining", "15 s")
    talcon/runtime.py-1183-        self._set_status("last_result", message)
    talcon/runtime.py-1184-        self._record_activity("Windows shutdown requested; waiting for confirmation")
    talcon/runtime.py:1185:        self.speech.say(message)
    talcon/runtime.py-1186-        return True, message
    talcon/runtime.py-1187-
    talcon/runtime.py-1188-    def confirm_shutdown(self) -> tuple[bool, str]:
    talcon/runtime.py-1189-        if not self.pending.confirm("shutdown"):
    talcon/runtime.py-1190-            message = "There is no shutdown request waiting for confirmation, sir."
    talcon/runtime.py:1191:            self.speech.say(message)
    talcon/runtime.py-1192-            return False, message
    talcon/runtime.py-1193-        self._set_status("pending_action", "None")
    talcon/runtime.py-1194-        self._set_status("pending_remaining", "")
    talcon/runtime.py-1195-        ok, detail = windows_shutdown.shutdown_computer()
    --
    talcon/runtime.py-1211-            self._set_status("shutdown_state", "Shutdown failed")
    talcon/runtime.py-1212-        self._set_status("last_result", message)
    talcon/runtime.py-1213-        self._set_status("last_event", message)
    talcon/runtime.py-1214-        self._record_activity(message)
    talcon/runtime.py:1215:        self.speech.say(message)
    talcon/runtime.py-1216-        return ok, message
    talcon/runtime.py-1217-
    talcon/runtime.py-1218-    def cancel_shutdown(self) -> tuple[bool, str]:
    talcon/runtime.py-1219-        if self.pending.cancel("shutdown"):
    talcon/runtime.py-1220-            self._set_status("pending_action", "None")
    talcon/runtime.py-1221-            self._set_status("pending_remaining", "")
    talcon/runtime.py-1222-            message = "Shutdown cancelled, sir."
    talcon/runtime.py-1223-            self._set_status("last_result", message)
    talcon/runtime.py:1224:            self.speech.say(message)
    talcon/runtime.py-1225-            return True, message
    talcon/runtime.py-1226-        if self._shutdown_scheduled:
    talcon/runtime.py-1227-            ok, detail = windows_shutdown.abort_shutdown()
    talcon/runtime.py-1228-            if ok:
    --
    talcon/runtime.py-1234-                message = f"I could not cancel the shutdown. {detail}"
    talcon/runtime.py-1235-            self._set_status("last_result", message)
    talcon/runtime.py-1236-            self._set_status("last_event", message)
    talcon/runtime.py-1237-            self._record_activity(message)
    talcon/runtime.py:1238:            self.speech.say(message)
    talcon/runtime.py-1239-            return ok, message
    talcon/runtime.py-1240-        message = "There is no shutdown waiting to be cancelled, sir."
    talcon/runtime.py-1241-        self._set_status("last_result", message)
    talcon/runtime.py:1242:        self.speech.say(message)
    talcon/runtime.py-1243-        return False, message
    talcon/runtime.py-1244-
    talcon/runtime.py-1245-    # -- startup greeting --------------------------------------------------
    talcon/runtime.py-1246-    def speak_startup_greeting(self) -> bool:
    --
    talcon/runtime.py-1290-        normalized = persist_settings(merged)
    talcon/runtime.py-1291-        self.settings = normalized
    talcon/runtime.py-1292-        self.speech.set_enabled(not self.muted)
    talcon/runtime.py-1293-
    talcon/runtime.py:1294:        backend_changed = old.get("speech_backend") != normalized.get("speech_backend")
    talcon/runtime.py-1295-        voice_changed = (
    talcon/runtime.py-1296-            old.get("speech_voice_id") != normalized.get("speech_voice_id")
    talcon/runtime.py-1297-            or old.get("speech_voice") != normalized.get("speech_voice")
    talcon/runtime.py-1298-        )
    talcon/runtime.py-1299-        if backend_changed:
    talcon/runtime.py:1300:            self.speech.set_backend(normalized.get("speech_backend", BACKEND_AUTO))
    talcon/runtime.py-1301-            self._active_voice = None
    talcon/runtime.py-1302-            self.voice_details = []
    talcon/runtime.py-1303-            self.voices = []
    talcon/runtime.py-1304-            self._set_status("speech", "Switching speech engine…")
    --
    talcon/runtime.py-1565-        if error:
    talcon/runtime.py-1566-            log.warning("Application discovery failed: %s", error)
    talcon/runtime.py-1567-            self._set_status("apps", f"Application discovery failed: {error}")
    talcon/runtime.py-1568-            if announce:
    talcon/runtime.py:1569:                self.speech.say("I could not refresh the application list, sir.")
    talcon/runtime.py-1570-            return
    talcon/runtime.py-1571-        count = len(entries or ())
    talcon/runtime.py-1572-        self._set_status("apps", f"{count} applications discovered")
    talcon/runtime.py-1573-        log.info("Application catalogue ready: %d entries", count)
    --
    talcon/runtime.py-1575-            self._refresh_grammar()
    talcon/runtime.py-1576-        except Exception:
    talcon/runtime.py-1577-            log.exception("Could not update the grammar after app discovery")
    talcon/runtime.py-1578-        if announce:
    talcon/runtime.py:1579:            self.speech.say(f"I found {count} applications, sir.")
    talcon/runtime.py-1580-
    talcon/runtime.py-1581-    def refresh_apps(self, announce: bool = True) -> None:
    talcon/runtime.py-1582-        """Rebuild the application catalogue off the UI/voice thread."""
    talcon/runtime.py-1583-        self._set_status("apps", "Refreshing installed applications…")
    --- Command parsing and alias persistence ---
    tests/test_commands.py:1:from talcon.commands import CommandRouter, normalize_phrase
    tests/test_commands.py-2-
    tests/test_commands.py-3-
    tests/test_commands.py-4-class FakeSpeech:
    tests/test_commands.py-5-    def __init__(self):
    --
    tests/test_commands.py-64-    return CommandRouter(modes, speech, app), speech, app
    tests/test_commands.py-65-
    tests/test_commands.py-66-
    tests/test_commands.py-67-def test_normalize_strips_wake_word_and_punctuation():
    tests/test_commands.py:68:    assert normalize_phrase("TALCON, activate my study mode.") == "activate my study mode"
    tests/test_commands.py:69:    assert normalize_phrase("TALCON, activate my study mode.") == "activate my study mode"
    tests/test_commands.py:70:    assert normalize_phrase("Hey Talcon — mute!") == "mute"
    tests/test_commands.py:71:    assert normalize_phrase("Hey Talcon — mute!") == "mute"
    tests/test_commands.py:72:    assert normalize_phrase("tell can introduce yourself") == "introduce yourself"
    tests/test_commands.py:73:    assert normalize_phrase("hey talcon to open the eyes") == "open the eyes"
    tests/test_commands.py-74-
    tests/test_commands.py-75-
    tests/test_commands.py-76-def test_study_and_entertainment():
    tests/test_commands.py-77-    router, _speech, app = build()
    tests/test_commands.py:78:    assert router.handle("TALCON, activate my study mode.") == "study"
    tests/test_commands.py:79:    assert router.handle("get ready for my entertainment mode") == "entertainment"
    tests/test_commands.py-80-    assert app.events == [("mode", "study"), ("mode", "entertainment")]
    tests/test_commands.py-81-
    tests/test_commands.py-82-
    tests/test_commands.py-83-def test_open_the_eyes_wakes_the_face_and_listening():
    tests/test_commands.py-84-    router, speech, app = build()
    tests/test_commands.py:85:    assert router.handle("hey talcon to open the eyes") == "open_eyes"
    tests/test_commands.py-86-    assert ("wake", True) in app.events
    tests/test_commands.py-87-    assert ("face", True) in app.events
    tests/test_commands.py-88-    assert speech.spoken
    tests/test_commands.py:89:    assert router.handle("close your eyes") == "close_eyes"
    tests/test_commands.py-90-    assert ("face", False) in app.events
    tests/test_commands.py-91-
    tests/test_commands.py-92-
    tests/test_commands.py-93-def test_mute_and_unmute_are_independent_commands():
    tests/test_commands.py-94-    router, speech, app = build()
    tests/test_commands.py:95:    router.handle("mute")
    tests/test_commands.py-96-    assert app.muted is True
    tests/test_commands.py:97:    router.handle("unmute")
    tests/test_commands.py-98-    assert app.muted is False
    tests/test_commands.py-99-    assert any("enabled" in line.lower() for line in speech.spoken)
    tests/test_commands.py-100-    assert ("mute", True, True) in app.events
    tests/test_commands.py-101-    assert ("mute", False, True) in app.events
    tests/test_commands.py-102-
    tests/test_commands.py-103-
    tests/test_commands.py-104-def test_introduce_yourself_greets_and_describes_talcon():
    tests/test_commands.py-105-    router, speech, app = build()
    tests/test_commands.py:106:    assert router.handle("TALCON introduce yourself") == "introduce"
    tests/test_commands.py-107-    assert app.events == []
    tests/test_commands.py-108-    assert speech.spoken
    tests/test_commands.py-109-    said = speech.spoken[-1].lower()
    tests/test_commands.py-110-    assert "hello everyone" in said
    --
    tests/test_commands.py-114-
    tests/test_commands.py-115-
    tests/test_commands.py-116-def test_window_and_lifecycle_commands():
    tests/test_commands.py-117-    router, _speech, app = build()
    tests/test_commands.py:118:    router.handle("open settings")
    tests/test_commands.py:119:    router.handle("hide talcon")
    tests/test_commands.py:120:    router.handle("show talcon")
    tests/test_commands.py:121:    router.handle("exit talcon")
    tests/test_commands.py-122-    assert ("settings",) in app.events
    tests/test_commands.py-123-    assert ("face", False) in app.events
    tests/test_commands.py-124-    assert ("face", True) in app.events
    tests/test_commands.py-125-    assert ("hide",) not in app.events
    --
    tests/test_commands.py-127-
    tests/test_commands.py-128-
    tests/test_commands.py-129-def test_show_and_hide_yourself_never_open_the_control_center():
    tests/test_commands.py-130-    router, _speech, app = build()
    tests/test_commands.py:131:    assert router.handle("hide yourself") == "hide"
    tests/test_commands.py:132:    assert router.handle("show yourself") == "show"
    tests/test_commands.py-133-    assert ("face", False) in app.events
    tests/test_commands.py-134-    assert ("face", True) in app.events
    tests/test_commands.py-135-    as
    ... command output truncated for persistence ...
    guous"
    tests/test_running_apps.py-345-    assert "Which one should I close" in speech.spoken[-1]
    tests/test_running_apps.py:346:    assert router.handle("second") == "app_closed"
    tests/test_running_apps.py-347-    assert fake.closed and fake.closed[0] is chrome
    tests/test_running_apps.py-348-
    tests/test_running_apps.py-349-
    tests/test_running_apps.py-350-def test_shell_injection_cannot_close_an_unmatched_application():
    tests/test_running_apps.py-351-    app = make_running("Google Chrome", "chrome.exe")
    tests/test_running_apps.py-352-    router, _speech, fake = build_router([app])
    tests/test_running_apps.py-353-    for phrase in ("close chrome & calc", "close C:\\Windows\\calc.exe",
    tests/test_running_apps.py-354-                   "quit calc.exe; shutdown /s"):
    tests/test_running_apps.py:355:        assert router.handle(phrase) in {"running_unknown", "app_not_running"}
    tests/test_running_apps.py-356-    assert fake.closed == []
    tests/test_running_apps.py-357-
    tests/test_running_apps.py-358-
    tests/test_running_apps.py-359-# -- running application questions ----------------------------------------
    tests/test_running_apps.py-360-def test_is_running_and_list_running():
    tests/test_running_apps.py-361-    router, speech, _app = build_router(
    tests/test_running_apps.py-362-        [make_running("Google Chrome"), make_running("Notepad")]
    tests/test_running_apps.py-363-    )
    tests/test_running_apps.py:364:    assert router.handle("is chrome running") == "running_yes"
    tests/test_running_apps.py-365-    assert "Yes" in speech.spoken[-1]
    tests/test_running_apps.py:366:    assert router.handle("is spotify open") == "running_no"
    tests/test_running_apps.py-367-    assert "not running" in speech.spoken[-1]
    tests/test_running_apps.py:368:    assert router.handle("what applications are currently running") == "running_list"
    tests/test_running_apps.py-369-    assert "Google Chrome" in speech.spoken[-1] and "Notepad" in speech.spoken[-1]
    tests/test_running_apps.py:370:    assert router.handle("how many apps are running right now") == "running_list"
    tests/test_running_apps.py-371-    assert "2 applications" in speech.spoken[-1]
    tests/test_running_apps.py-372-
    tests/test_running_apps.py-373-
    tests/test_running_apps.py-374-# -- force close -----------------------------------------------------------
    tests/test_running_apps.py-375-def test_force_close_requires_exact_separate_confirmation():
    tests/test_running_apps.py-376-    app = make_running("Spotify", "Spotify.exe", pids=(10,))
    tests/test_running_apps.py-377-    router, speech, fake = build_router([app])
    tests/test_running_apps.py:378:    assert router.handle("force close spotify") == "force_close_pending"
    tests/test_running_apps.py-379-    assert "unsaved work" in speech.spoken[-1]
    tests/test_running_apps.py-380-    # A mismatched confirmation aborts and never terminates anything.
    tests/test_running_apps.py:381:    assert router.handle("confirm force close chrome") == "force_close_mismatch"
    tests/test_running_apps.py-382-    assert fake.forced == []
    tests/test_running_apps.py-383-    # The separate exact confirmation is required.
    tests/test_running_apps.py:384:    assert router.handle("force close spotify") == "force_close_pending"
    tests/test_running_apps.py:385:    assert router.handle("confirm force close spotify") == "app_force_closed"
    tests/test_running_apps.py-386-    assert [item.name for item in fake.forced] == ["Spotify"]
    tests/test_running_apps.py-387-
    tests/test_running_apps.py-388-
    tests/test_running_apps.py-389-def test_force_close_can_be_cancelled():
    tests/test_running_apps.py-390-    router, speech, fake = build_router([make_running("Spotify")])
    tests/test_running_apps.py:391:    assert router.handle("force close spotify") == "force_close_pending"
    tests/test_running_apps.py:392:    assert router.handle("cancel force close") == "force_close_cancelled"
    tests/test_running_apps.py-393-    assert fake.forced == []
    tests/test_running_apps.py-394-    assert "Nothing was closed" in speech.spoken[-1]
    tests/test_running_apps.py-395-
    tests/test_running_apps.py-396-
    tests/test_running_apps.py-397-def test_protected_process_cannot_be_force_closed():
    tests/test_running_apps.py-398-    app = make_running("Windows Security", "MsMpEng.exe", pids=(10,))
    tests/test_running_apps.py-399-    router, speech, fake = build_router([app])
    tests/test_running_apps.py:400:    assert router.handle("force close windows security") == "app_protected"
    tests/test_running_apps.py-401-    assert fake.forced == []
    tests/test_running_apps.py-402-    assert "protected" in speech.spoken[-1].lower()
    tests/test_running_apps.py-403-
    tests/test_running_apps.py-404-
    --
    tests/test_running_apps.py-421-    runtime.apps = FakeInstalled(entries)
    tests/test_running_apps.py-422-    runtime.commands = SimpleNamespace(
    tests/test_running_apps.py-423-        recognition_phrases=lambda: {"study mode"},
    tests/test_running_apps.py-424-        pending_phrases=lambda: set(),
    tests/test_running_apps.py:425:        app_aliases={},
    tests/test_running_apps.py-426-    )
    tests/test_running_apps.py-427-    runtime.custom = SimpleNamespace(snapshot=lambda: {"commands": [], "automations": []})
    tests/test_running_apps.py-428-    runtime.voice = object()  # causes live running-app phrases to be included
    tests/test_running_apps.py-429-    runtime.running_applications = lambda: running
    --
    tests/test_running_apps.py-481-    monkeypatch.setattr(running_apps, "close_application_by_name", fake_close)
    tests/test_running_apps.py-482-
    tests/test_running_apps.py-483-    router, speech, _app = build_router([], installed=[AppEntry(name="Mechvibes")])
    tests/test_running_apps.py-484-    router._last_opened = AppEntry(name="Mechvibes")
    tests/test_running_apps.py:485:    assert router.handle("now close it") == "app_closed"
    tests/test_running_apps.py-486-    assert posted == ["Mechvibes"]
    tests/test_running_apps.py-487-    assert speech.spoken[-1] == "Closing Mechvibes, sir."
    tests/test_running_apps.py-488-
    tests/test_running_apps.py-489-
    --
    tests/test_running_apps.py-494-        running_apps, "close_application_by_name", lambda *a, **k: CloseResult()
    tests/test_running_apps.py-495-    )
    tests/test_running_apps.py-496-    router, speech, _app = build_router([], installed=[AppEntry(name="Mechvibes")])
    tests/test_running_apps.py-497-    router._last_opened = AppEntry(name="Mechvibes")
    tests/test_running_apps.py:498:    assert router.handle("close it") == "app_not_running"
    tests/test_running_apps.py-499-    assert "isn't running" in speech.spoken[-1]
    --
    talcon/runtime.py-10-from PySide6.QtCore import QObject, QTimer
    talcon/runtime.py-11-
    talcon/runtime.py-12-from . import startup, windows_control, windows_media
    talcon/runtime.py-13-from . import shutdown as windows_shutdown
    talcon/runtime.py:14:from .app_catalog import extract_app_command, load_aliases, resolve_application
    talcon/runtime.py-15-from .apps import ApplicationLibrary, discover_start_apps
    talcon/runtime.py-16-from .assistant import APPROVAL_TIMEOUT, AssistantController
    talcon/runtime.py-17-from .bridge import UiBridge
    talcon/runtime.py:18:from .commands import CommandRouter, build_builtin_registry, normalize_phrase
    talcon/runtime.py-19-from .config import (
    talcon/runtime.py-20-    BUILTIN_MODES, RESERVED_MODE_KEYS, ROOT, load_modes, load_settings,
    talcon/runtime.py-21-    normalize_modes, save_modes as persist_modes,
    talcon/runtime.py-22-    save_settings as persist_settings,
    --
    talcon/runtime.py-135-
    talcon/runtime.py-136-        # Windows control services.  The window API is injectable so tests can
    talcon/runtime.py-137-        # substitute a fake and never touch real windows.
    talcon/runtime.py-138-        self.apps = ApplicationLibrary(packaged_loader=discover_start_apps)
    talcon/runtime.py:139:        self.commands.set_application_library(self.apps, load_aliases())
    talcon/runtime.py-140-        self.cron = CronScheduler(self)
    talcon/runtime.py-141-        self.pending = PendingActions()
    talcon/runtime.py-142-        # AI (Hermes) assistant.  Disabled unless the user turns it on; the
    talcon/runtime.py-143-        # router consults it only after every local parser has failed.
    --
    talcon/runtime.py-1116-    def running_applications(self) -> list:
    talcon/runtime.py-1117-        """Live list of user applications with visible windows."""
    talcon/runtime.py-1118-        return enumerate_running_apps(
    talcon/runtime.py-1119-            self.apps.cached_entries(), api=self.window_api,
    talcon/runtime.py:1120:            own_pid=os.getpid(), aliases=self.commands.app_aliases,
    talcon/runtime.py-1121-        )
    talcon/runtime.py-1122-
    talcon/runtime.py-1123-    def close_application(self, app):
    talcon/runtime.py-1124-        """Graceful WM_CLOSE of every window belonging to one running app."""
    --
    talcon/runtime.py-1381-            triggers = mode.get("triggers") or []
    talcon/runtime.py-1382-            if not triggers:
    talcon/runtime.py-1383-                raise ValueError(f"Add at least one spoken command for {title} mode.")
    talcon/runtime.py-1384-            for raw in triggers:
    talcon/runtime.py:1385:                phrase = normalize_phrase(raw)
    talcon/runtime.py-1386-                if not phrase:
    talcon/runtime.py-1387-                    raise ValueError(f"{title} mode has an empty spoken command.")
    talcon/runtime.py-1388-                if phrase in custom_triggers:
    talcon/runtime.py-1389-                    raise ValueError(
    --
    talcon/runtime.py-1433-
    talcon/runtime.py-1434-    @staticmethod
    talcon/runtime.py-1435-    def _app_name_forms(name: str) -> list[str]:
    talcon/runtime.py-1436-        """Full name plus the short forms people actually say ("Chrome")."""
    talcon/runtime.py:1437:        base = normalize_phrase(name)
    talcon/runtime.py-1438-        if not base:
    talcon/runtime.py-1439-            return []
    talcon/runtime.py-1440-        forms = [base]
    talcon/runtime.py-1441-        words = base.split()
    --
    talcon/runtime.py-1466-            log.debug("Could not read cached applications", exc_info=True)
    talcon/runtime.py-1467-            return set()
    talcon/runtime.py-1468-
    talcon/runtime.py-1469-        def rank(entry):
    talcon/runtime.py:1470:            name = normalize_phrase(entry.name)
    talcon/runtime.py-1471-            try:
    talcon/runtime.py-1472-                priority = PRIORITY_APP_NAMES.index(name)
    talcon/runtime.py-1473-            except ValueError:
    talcon/runtime.py-1474-                # Everyday apps first (in the order above), then taskbar pins,
    --
    talcon/runtime.py-1480-
    talcon/runtime.py-1481-        chosen = []
    talcon/runtime.py-1482-        seen_names: set[str] = set()
    talcon/runtime.py-1483-        for entry in sorted(entries, key=rank):
    talcon/runtime.py:1484:            key = normalize_phrase(entry.name)
    talcon/runtime.py-1485-            if not key or key in seen_names:
    talcon/runtime.py-1486-                continue
    talcon/runtime.py-1487-            seen_names.add(key)
    talcon/runtime.py-1488-            chosen.append(entry)
    --
    talcon/runtime.py-1492-        # Reserve room for the force/confirm templates before filling the
    talcon/runtime.py-1493-        # budget with ordinary open/close phrases.
    talcon/runtime.py-1494-        budget = MAX_APP_PHRASES - FORCE_GRAMMAR_LIMIT * 2 - 8
    talcon/runtime.py-1495-        names = {
    talcon/runtime.py:1496:            normalize_phrase(entry.name) for entry in chosen
    talcon/runtime.py:1497:            if normalize_phrase(entry.name)
    talcon/runtime.py-1498-        }
    talcon/runtime.py-1499-        # Alias phrases are reserved first so "open vs code" is never pushed
    talcon/runtime.py-1500-        # out by the bounded budget.
    talcon/runtime.py:1501:        aliases = getattr(self.commands, "app_aliases", {}) or {}
    talcon/runtime.py:1502:        for alias, target in aliases.items():
    talcon/runtime.py-1503-            if len(phrases) >= budget:
    talcon/runtime.py-1504-                break
    talcon/runtime.py:1505:            alias_name = normalize_phrase(alias)
    talcon/runtime.py:1506:            if alias_name and normalize_phrase(target) in names:
    talcon/runtime.py-1507-                for verb in ("open", "close", "launch", "exit"):
    talcon/runtime.py:1508:                    phrases.add(f"{verb} {alias_name}")
    talcon/runtime.py-1509-                    if len(phrases) >= budget:
    talcon/runtime.py-1510-                        break
    talcon/runtime.py-1511-        processed = 0
    talcon/runtime.py-1512-        for entry in chosen:
    talcon/runtime.py-1513-            if len(phrases) >= budget:
    talcon/runtime.py-1514-                break
    talcon/runtime.py:1515:            name = normalize_phrase(entry.name)
    talcon/runtime.py-1516-            if not name or len(name.split()) > 6:
    talcon/runtime.py-1517-                continue
    talcon/runtime.py-1518-            processed += 1
    talcon/runtime.py-1519-            verbs = ["open", "close"]
    --
    talcon/runtime.py-1528-                    break
    talcon/runtime.py-1529-        # Force close needs a separate, explicitly confirmed template.  It is
    talcon/runtime.py-1530-        # only added for the shortest app names to keep the grammar bounded.
    talcon/runtime.py-1531-        for entry in chosen[:FORCE_GRAMMAR_LIMIT]:
    talcon/runtime.py:1532:            name = normalize_phrase(entry.name)
    talcon/runtime.py-1533-            if not name:
    talcon/runtime.py-1534-                continue
    talcon/runtime.py-1535-            phrases.add(f"force close {name}")
    talcon/runtime.py-1536-            phrases.add(f"confirm force close {name}")
    --
    talcon/runtime.py-1596-                log.exception("Could not refresh the recognition grammar")
    talcon/runtime.py-1597-
    talcon/runtime.py-1598-    def test_phrase(self, text: str) -> dict:
    talcon/runtime.py-1599-        """Report what a typed phrase would match, without executing it."""
    talcon/runtime.py:1600:        normalized = normalize_phrase(text)
    talcon/runtime.py-1601-        if not normalized:
    talcon/runtime.py-1602-            return {"matched": False, "phrase": "", "reason": "Type a phrase to test."}
    talcon/runtime.py-1603-        command = self.commands.registry.match(normalized)
    talcon/runtime.py-1604-        if command is not None:
    --
    talcon/runtime.py-1628-                "phrase": normalized,
    talcon/runtime.py-1629-                "name": entry.get("name", "Custom command"),
    talcon/runtime.py-1630-                "action": entry.get("action", "automation"),
    talcon/runtime.py-1631-            }
    talcon/runtime.py:1632:        query = extract_app_command(normalized)
    talcon/runtime.py-1633-        if query:
    talcon/runtime.py-1634-            try:
    talcon/runtime.py-1635-                entries = self.apps.entries()
    talcon/runtime.py:1636:                result = resolve_application(query, entries, self.commands.app_aliases)
    talcon/runtime.py-1637-            except Exception:
    talcon/runtime.py-1638-                log.exception("Could not test an application phrase")
    talcon/runtime.py-1639-            else:
    talcon/runtime.py-1640-                if result.status == "match" and result.entry is not None:
    --
    talcon/commands.py-1-"""Deterministic, offline voice/typed command routing.
    talcon/commands.py-2-
    talcon/commands.py-3-There is no language model and no fuzzy matching here.  Every built-in
    talcon/commands.py:4:command is an exact alias of a normalised phrase stored in a registry, so a
    talcon/commands.py-5-transcript either matches a known command exactly or it does not run at all.
    talcon/commands.py-6-The registry also makes duplicate/conflicting phrases impossible to add.
    talcon/commands.py-7-"""
    talcon/commands.py-8-from __future__ import annotations
    --
    talcon/commands.py-14-import time
    talcon/commands.py-15-from dataclasses import dataclass, field
    talcon/commands.py-16-
    talcon/commands.py-17-from .app_catalog import (
    talcon/commands.py:18:    _CLOSE_VERBS, extract_app_command, extract_close_command,
    talcon/commands.py:19:    extract_force_close_command, extract_force_confirm_command,
    talcon/commands.py-20-    extract_running_query, is_force_cancel, normalize_app_name,
    talcon/commands.py-21-    resolve_application,
    talcon/commands.py-22-)
    talcon/commands.py-23-from .apps import launch_app
    --
    talcon/commands.py-150-    "play",
    talcon/commands.py-151-}
    talcon/commands.py-152-
    talcon/commands.py-153-
    talcon/commands.py:154:def normalize_phrase(phrase: str) -> str:
    talcon/commands.py:155:    """Lower-case, strip punctuation and remove wake/polite lead-ins.
    talcon/commands.py-156-
    talcon/commands.py-157-    This is still deterministic phrase matching: only known filler prefixes are
    talcon/commands.py-158-    removed, never arbitrary words, and the remaining phrase must match a
    talcon/commands.py:159:    registered alias exactly.
    talcon/commands.py-160-    """
    talcon/commands.py-161-    text = str(phrase or "").lower().strip()
    talcon/commands.py-162-    text = _PUNCTUATION.sub(" ", text)
    talcon/commands.py-163-    text = " ".join(text.split())
    --
    talcon/commands.py-178-
    talcon/commands.py-179-
    talcon/commands.py-180-def strip_wake_phrase(phrase: str) -> str:
    talcon/commands.py-181-    """Return the command after a wake word, or "" when only the wake word was said."""
    talcon/commands.py:182:    remainder = normalize_phrase(phrase)
    talcon/commands.py-183-    if not remainder or remainder in _WAKE_WORDS:
    talcon/commands.py-184-        return ""
    talcon/commands.py-185-    return remainder
    talcon/commands.py-186-
    --
    talcon/commands.py-190-
    talcon/commands.py-191-    Only used to decide whether to ask the user to repeat.  It never executes
    talcon/commands.py-192-    anything and never changes command matching.
    talcon/commands.py-193-    """
    talcon/commands.py:194:    text = normalize_phrase(phrase)
    talcon/commands.py-195-    if not text:
    talcon/commands.py-196-        return False
    talcon/commands.py-197-    if text.split()[0] in _COMMAND_INTENT_PREFIXES:
    talcon/commands.py-198-        return True
    --
    talcon/commands.py-211-
    talcon/commands.py-212-
    talcon/commands.py-213-def spoken_form(phrase: str) -> str:
    talcon/commands.py-214-    """Return the form of a phrase that the installed Vosk model can decode."""
    talcon/commands.py:215:    words = normalize_phrase(phrase).split()
    talcon/commands.py-216-    return " ".join(_GRAMMAR_SPOKEN_FORMS.get(word, word) for word in words)
    talcon/commands.py-217-
    talcon/commands.py-218-
    talcon/commands.py-219-def grammar_form(phrase: str) -> str:
    --
    talcon/commands.py-223-    yourself") to recognise a one-shot request, so it must not strip them.
    talcon/commands.py-224-    """
    talcon/commands.py-225-    text = _PUNCTUATION.sub(" ", str(phrase).lower())
    talcon/commands.py-226-    text = " ".join(text.split())
    talcon/commands.py:227:    # Match normalize_phrase's deterministic fixes without dropping wake words.
    talcon/commands.py-228-    text = re.sub(r"\bmod\b", "mode", text)
    talcon/commands.py-229-    words = text.split()
    talcon/commands.py-230-    return " ".join(_GRAMMAR_SPOKEN_FORMS.get(word, word) for word in words)
    talcon/commands.py-231-
    --
    talcon/commands.py-270-        if not action or not str(action).strip():
    talcon/commands.py-271-            raise ValueError("A command needs an action name.")
    talcon/commands.py-272-        clean: list[str] = []
    talcon/commands.py-273-        for phrase in phrases:
    talcon/commands.py:274:            normalized = normalize_phrase(phrase)
    talcon/commands.py-275-            if not normalized:
    talcon/commands.py-276-                raise ValueError(f"Command {action!r} has an empty phrase.")
    talcon/commands.py-277-            if normalized in _WAKE_WORDS:
    talcon/commands.py-278-                raise ValueError(f"{normalized!r} is reserved for the wake word.")
    --
    talcon/commands.py-292-            self._by_phrase[phrase] = command
    talcon/commands.py-293-        return command
    talcon/commands.py-294-
    talcon/commands.py-295-    def match(self, text: str) -> Command | None:
    talcon/commands.py:296:        return self._by_phrase.get(normalize_phrase(text))
    talcon/commands.py-297-
    talcon/commands.py-298-    def is_prefix(self, text: str) -> bool:
    talcon/commands.py-299-        """True when the phrase is a proper prefix of a registered command."""
    talcon/commands.py:300:        phrase = normalize_phrase(text)
    talcon/commands.py-301-        if not phrase:
    talcon/commands.py-302-            return False
    talcon/commands.py-303-        prefix = phrase + " "
    talcon/commands.py-304-        return any(known.startswith(prefix) for known in self._by_phrase)
    --
    talcon/commands.py-636-        # so local commands always keep priority; ``control_handler`` consumes
    talcon/commands.py-637-        # "stop that"-style phrases while an AI request is in flight.
    talcon/commands.py-638-        self.natural_handler = None
    talcon/commands.py-639-        self.control_handler = None
    talcon/commands.py:640:        # Discovered application catalogue, aliases and the launch function.
    talcon/commands.py-641-        # ``app_launcher`` is injectable so tests never start a real program.
    talcon/commands.py-642-        self.apps = None
    talcon/commands.py:643:        self.app_aliases: dict[str, str] = {}
    talcon/commands.py-644-        self.app_launcher = launch_app
    talcon/commands.py-645-        self._pending_apps: tuple = ()
    talcon/commands.py-646-        self._pending_query = ""
    talcon/commands.py-647-        self._pending_kind = ""
    --
    talcon/commands.py-652-        # Dynamic user-created modes: action name -> mode key.
    talcon/commands.py-653-        self._mode_actions: dict[str, str] = {}
    talcon/commands.py-654-
    talcon/commands.py-655-    # -- applications ------------------------------------------------------
    talcon/commands.py:656:    def set_application_library(self, library, aliases=None) -> None:
    talcon/commands.py-657-        self.apps = library
    talcon/commands.py:658:        self.app_aliases = dict(aliases or {})
    talcon/commands.py-659-
    talcon/commands.py-660-    def pending_phrases(self) -> set[str]:
    talcon/commands.py-661-        """Bare names the user may say to answer an ambiguity prompt."""
    talcon/commands.py-662-        phrases: set[str] = set()
    talcon/commands.py-663-        for entry in self._pending_apps[:6]:
    talcon/commands.py:664:            name = normalize_phrase(entry.name)
    talcon/commands.py-665-            if name:
    talcon/commands.py-666-                phrases.add(name)
    talcon/commands.py-667-        if self._pending_apps:
    talcon/commands.py-668-            # The prompt offers numbered choices, so the numbered answers must
    --
    talcon/commands.py-679-    # -- dynamic modes -----------------------------------------------------
    talcon/commands.py-680-    def register_modes(self, modes: dict, strict: bool = False) -> list[str]:
    talcon/commands.py-681-        """Register the voice triggers of user-created modes.
    talcon/commands.py-682-
    talcon/commands.py:683:        Built-in modes keep their large alias lists from the static registry.
    talcon/commands.py-684-        Returns a list of human-readable conflicts when strict is False.
    talcon/commands.py-685-        """
    talcon/commands.py-686-        self.registry.remove_source("mode")
    talcon/commands.py-687-        self._mode_actions = {}
    --
    talcon/commands.py-726-    def looks_like_command(self, phrase: str, vocabulary=()) -> bool:
    talcon/commands.py-727-        known = self.registry.phrases() | set(vocabulary or ())
    talcon/commands.py-728-        return is_command_like(phrase, known)
    talcon/commands.py-729-
    talcon/commands.py:730:    def handle(self, phrase: str, announce_unknown: bool = True) -> str:
    talcon/commands.py:731:        text = normalize_phrase(phrase)
    talcon/commands.py-732-        log.info("Recognized command: %s", text or "(empty)")
    talcon/commands.py-733-        if not text:
    talcon/commands.py-734-            return "none"
    talcon/commands.py-735-
    --
    talcon/commands.py-762-        pending = self._serve_pending_app(text)
    talcon/commands.py-763-        if pending is not None:
    talcon/commands.py-764-            return pending
    talcon/commands.py-765-
    talcon/commands.py:766:        confirm_force = extract_force_confirm_command(text)
    talcon/commands.py-767-        if confirm_force is not None:
    talcon/commands.py-768-            return self._confirm_force_close(confirm_force)
    talcon/commands.py-769-        if is_force_cancel(text):
    talcon/commands.py-770-            return self._cancel_force_close()
    talcon/commands.py:771:        force_query = extract_force_close_command(text)
    talcon/commands.py-772-        if force_query:
    talcon/commands.py-773-            return self._do_force_close(force_query)
    talcon/commands.py-774-
    talcon/commands.py-775-        running_query = extract_running_query(text)
    talcon/commands.py-776-        if running_query:
    talcon/commands.py-777-            return self._do_is_running(running_query)
    talcon/commands.py-778-
    talcon/commands.py:779:        close_query = extract_close_command(text)
    talcon/commands.py-780-        if close_query is None and is_pronoun_close(text):
    talcon/commands.py-781-            close_query = "it"
    talcon/commands.py-782-        if close_query:
    talcon/commands.py-783-            return self._do_close_app(close_query)
    talcon/commands.py-784-
    talcon/commands.py:785:        query = extract_app_command(text)
    talcon/commands.py-786-        if query:
    talcon/commands.py-787-            return self._do_open_app(query)
    talcon/commands.py-788-
    talcon/commands.py-789-        if self.natural_handler is not None:
    --
    talcon/commands.py-1156-                entries = self.apps.refresh()
    talcon/commands.py-1157-            except Exception:
    talcon/commands.py-1158-                log.exception("Application discovery failed")
    talcon/commands.py-1159-                entries = []
    talcon/commands.py:1160:        result = resolve_application(query, entries, self.app_aliases)
    talcon/commands.py-1161-        if result.status == "match" and result.entry is not None:
    talcon/commands.py-1162-            log.info("Application match: %r -> %s (%s)", query, result.entry.name,
    talcon/commands.py-1163-                     result.reason)
    talcon/commands.py-1164-            return self._launch_catalogued(result.entry)
    --
    talcon/commands.py-1191-            # nothing happened because no pending state survived the question.
    talcon/commands.py-1192-            suggested = self._entries_for_names(names, entries)
    talcon/commands.py-1193-            if suggested:
    talcon/commands.py-1194-                self._pending_apps = tuple(suggested)
    talcon/commands.py:1195:                self._pending_query = result.query or normalize_phrase(query)
    talcon/commands.py-1196-                self._pending_kind = "suggest"
    talcon/commands.py-1197-                self._notify_grammar()
    talcon/commands.py-1198-            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" or {names[-1]}"
    talcon/commands.py-1199-            question = "Is that the one you meant?" if len(names) == 1 else (
    --
    talcon/commands.py-1260-        match and open nothing.
    talcon/commands.py-1261-        """
    talcon/commands.py-1262-        if not self._pending_apps:
    talcon/commands.py-1263-            return None
    talcon/commands.py:1264:        words = normalize_phrase(text).split()
    talcon/commands.py-1265-        if not words:
    talcon/commands.py-1266-            return None
    talcon/commands.py-1267-
    talcon/commands.py-1268-        affirm = any(word in _ANSWER_YES for word in words)
    --
    talcon/commands.py-1298-                log.info("Pending answer chose %s (index %d)", entry.name, index)
    talcon/commands.py-1299-            return self._dispatch_pending(entry)
    talcon/commands.py-1300-
    talcon/commands.py-1301-        result = resolve_application(
    talcon/commands.py:1302:            text, self._pending_apps, self.app_aliases, threshold=0.9
    talcon/commands.py-1303-        )
    talcon/commands.py-1304-        if result.status == "match" and result.entry is not None:
    talcon/commands.py:1305:            self._learn_alias(text, result.entry)
    talcon/commands.py-1306-            return self._dispatch_pending(result.entry)
    talcon/commands.py-1307-        return None
    talcon/commands.py-1308-
    talcon/commands.py:1309:    def _learn_alias(self, spoken: str, entry) -> None:
    talcon/commands.py-1310-        """Remember how the user just pronounced an ambiguous application.
    talcon/commands.py-1311-
    talcon/commands.py-1312-        Only a successful, high-confidence answer to an ambiguity prompt is
    talcon/commands.py-1313-        learned.  The spoken text is never executed, and writing the small
    talcon/commands.py:1314:        alias map only affects future catalogue lookups.
    talcon/commands.py-1315-        """
    talcon/commands.py:1316:        phrase = normalize_phrase(spoken)
    talcon/commands.py-1317-        if not phrase or len(phrase) > 40 or len(phrase.split()) > 6:
    talcon/commands.py-1318-            return
    talcon/commands.py-1319-        if phrase == normalize_app_name(entry.name):
    talcon/commands.py-1320-            return
    talcon/commands.py:1321:        self.app_aliases[phrase] = entry.name
    talcon/commands.py:1322:        target = CONFIG_DIR / "app_aliases.json"
    talcon/commands.py-1323-        try:
    talcon/commands.py-1324-            data = {}
    talcon/commands.py-1325-            if target.is_file():
    talcon/commands.py-1326-                loaded = json.loads(target.read_text(encoding="utf-8"))
    --
    talcon/commands.py-1376-        apps = self._running_apps()
    talcon/commands.py-1377-        if apps is None:
    talcon/commands.py-1378-            log.info("Running-application discovery is unavailable")
    talcon/commands.py-1379-            return "unknown"
    talcon/commands.py:1380:        result = resolve_application(query, apps, self.app_aliases)
    talcon/commands.py-1381-        if result.status == "match" and result.entry is not None:
    talcon/commands.py-1382-            self.speech.say(f"Yes, {result.entry.name} is running, sir.")
    talcon/commands.py-1383-            return "running_yes"
    talcon/commands.py-1384-        if result.status == "ambiguous" and result.candidates:
    --
    talcon/commands.py-1396-        try:
    talcon/commands.py-1397-            entries = self.apps.entries()
    talcon/commands.py-1398-        except Exception:
    talcon/commands.py-1399-            return ""
    talcon/commands.py:1400:        result = resolve_application(query, entries, self.app_aliases)
    talcon/commands.py-1401-        if result.status == "match" and result.entry is not None:
    talcon/commands.py-1402-            return result.entry.name
    talcon/commands.py-1403-        return ""
    talcon/commands.py-1404-
    talcon/commands.py-1405-    def _do_close_app(self, query: str) -> str:
    talcon/commands.py:1406:        pronoun = is_pronoun_query(normalize_phrase(query))
    talcon/commands.py-1407-        if pronoun:
    talcon/commands.py-1408-            # "close it" refers to whatever TALCON opened last.
    talcon/commands.py-1409-            entry = getattr(self, "_last_opened", None)
    talcon/commands.py-1410-            if entry is None:
    --
    talcon/commands.py-1436-            self.speech.say(
    talcon/commands.py-1437-                "I cannot inspect the running applications right now, sir."
    talcon/commands.py-1438-            )
    talcon/commands.py-1439-            return "close_unavailable"
    talcon/commands.py:1440:        result = resolve_application(query, apps, self.app_aliases)
    talcon/commands.py-1441-        if result.status == "match" and result.entry is not None:
    talcon/commands.py-1442-            return self._close_running_app(result.entry)
    talcon/commands.py-1443-        if result.status == "ambiguous" and result.candidates:
    talcon/commands.py-1444-            self._pending_apps = tuple(result.candidates)
    --
    talcon/commands.py-1529-        apps = self._running_apps()
    talcon/commands.py-1530-        if apps is None:
    talcon/commands.py-1531-            log.info("Running-application discovery is unavailable")
    talcon/commands.py-1532-            return "unknown"
    talcon/commands.py:1533:        result = resolve_application(query, apps, self.app_aliases)
    talcon/commands.py-1534-        if result.status != "match" or result.entry is None:
    talcon/commands.py-1535-            if result.status == "ambiguous" and result.candidates:
    talcon/commands.py-1536-                labels = self._candidate_labels(result.candidates)
    talcon/commands.py-1537-                joined = " and ".join(labels) if len(labels) == 2 else ", ".join(labels)
    --
    talcon/commands.py-1575-        if time.monotonic() > float(pending.get("deadline", 0.0)):
    talcon/commands.py-1576-            self._force_pending = None
    talcon/commands.py-1577-            self.speech.say("The force-close request expired, sir. Nothing was closed.")
    talcon/commands.py-1578-            return "force_close_expired"
    talcon/commands.py:1579:        expected = normalize_phrase(str(pending.get("name", "")))
    talcon/commands.py:1580:        spoken = normalize_phrase(query)
    talcon/commands.py-1581-        if spoken != expected:
    talcon/commands.py-1582-            self._force_pending = None
    talcon/commands.py-1583-            self.speech.say(
    talcon/commands.py-1584-                f"That confirmation does not match {pending.get('name')}, sir. "
    ✓ • 736ms

## Assistant

The initial hardware query is blocked by this session’s Windows-management permission boundary, so I’m rerunning that read-only inventory with system access. It will only read CPU, RAM, GPU, disk, and OS information—no settings or files are changed.

## Activity

    $ Write-Output '--- CPU ---'; Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed; Write-Output '--- Memory ---'; Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory; Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory,TotalVisibleMemorySize; Write-Output '--- GPU ---'; Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor; Write-Output '--- Disk capacity ---'; Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,Size,FreeSpace,FileSystem; Write-Output '--- OS ---'; Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture
    --- CPU ---
    
    Name                                            NumberOfCores NumberOfLogicalProcessors MaxClockSpeed
    ----                                            ------------- ------------------------- -------------
    AMD Ryzen 5 3550H with Radeon Vega Mobile Gfx               4                         8          2100
    --- Memory ---
    
    
    --- GPU ---
    NVIDIA GeForce GTX 1650
    AMD Radeon(TM) Vega 8 Graphics
    --- Disk capacity ---
    
    --- OS ---
    
    
    ✓ • 2.21s

## Activity

    $ Get-CimInstance Win32_ComputerSystem | Format-List TotalPhysicalMemory; Get-CimInstance Win32_OperatingSystem | Format-List Caption,Version,OSArchitecture,FreePhysicalMemory,TotalVisibleMemorySize; Get-CimInstance Win32_VideoController | Format-List Name,AdapterRAM,CurrentHorizontalResolution,CurrentVerticalResolution; Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Format-List DeviceID,Size,FreeSpace,FileSystem
    
    TotalPhysicalMemory : 14899982336
    
    
    Caption                : Microsoft Windows 11 Home Single Language
    Version                : 10.0.26300
    OSArchitecture         : 64-bit
    FreePhysicalMemory     : 3971196
    TotalVisibleMemorySize : 14550764
    
    
    Name                        : NVIDIA GeForce GTX 1650
    AdapterRAM                  : 4293918720
    CurrentHorizontalResolution :
    CurrentVerticalResolution   :
    
    Name                        : AMD Radeon(TM) Vega 8 Graphics
    AdapterRAM                  : 2147483648
    CurrentHorizontalResolution : 1920
    CurrentVerticalResolution   : 1080
    
    
    DeviceID   : C:
    Size       : 238987493376
    FreeSpace  : 33916956672
    FileSystem : NTFS
    
    ✓ • 630ms

## Activity

    Searched the web for site:github.com/hexgrad/kokoro README Windows onnxruntime CPU install Kokoro 82M, site:github.com/thewh1teagle/kokoro-onnx README Windows Python onnxruntime, site:github.com/hexgrad/kokoro model size license Apache 2.0

## Activity

    $ py -0p; Write-Output '--- Existing relevant packages ---'; & .\.venv\Scripts\python.exe -m pip show onnxruntime kokoro kokoro-onnx piper-tts 2>$null; Write-Output '--- Free space after current app ---'; Get-PSDrive C | Select-Object Name,Used,Free; Write-Output '--- GPU driver / compute availability ---'; nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv,noheader,nounits 2>$null
    Program 'py.exe' failed to run: The file cannot be accessed by the systemAt line:2 char:1
    + py -0p; Write-Output '--- Existing relevant packages ---'; & .\.venv\ ...
    + ~~~~~~.
    At line:2 char:1
    + py -0p; Write-Output '--- Existing relevant packages ---'; & .\.venv\ ...
    + ~~~~~~
        + CategoryInfo          : ResourceUnavailable: (:) [], ApplicationFailedException
        + FullyQualifiedErrorId : NativeCommandFailed
    
    --- Existing relevant packages ---
    --- Free space after current app ---
    
    NVIDIA GeForce GTX 1650, 610.91, 4096, 3906
    Name Used Free
    ---- ---- ----
    C       0
    --- GPU driver / compute availability ---
    
    
    ✓ • 2.12s

## Assistant

Hardware verdict: Ryzen 5 3550H, 16 GB RAM with roughly 3.8 GB currently free, GTX 1650 with 4 GB VRAM (about 3.9 GB free), and ~32 GB free disk. That can run a quantized local Kokoro voice, but not safely as a heavyweight always-on GPU service alongside the assistant. I’m choosing a lazy-loaded, CPU-first local neural TTS integration with an expressive Windows fallback; it will synthesize only while speaking and unload after idle, so continuous listening stays lightweight.

## Activity

    $ Get-Content talcon/speech.py | Select-Object -First 180; Get-Content talcon/speech.py | Select-Object -Skip 380 -First 115; Get-Content talcon/speech.py | Select-Object -Skip 780 -First 420; rg -n -C 5 'SPEECH_BACKEND|engine_combo|BACKEND_' talcon/ui/main_window.py talcon/runtime.py tests/test_speech.py tests/test_ui.py tests/test_config.py
    """Speech output through Microsoft's on-device Windows voices.
    
    Two synthesis backends are supported, both driven through a small persistent
    PowerShell host so no Python WinRT package is required:
    
    * :class:`WindowsNaturalVoiceBackend` uses the public Windows Runtime API
      ``Windows.Media.SpeechSynthesis.SpeechSynthesizer``.  It synthesises to an
      in-memory ``SpeechSynthesisStream`` and plays it with ``SoundPlayer``.
    * :class:`SapiBackend` keeps the original ``System.Speech`` engine as a
      compatibility fallback.  It can also expose a natural voice if Windows (or
      an installed SAPI adapter) registers one there.
    
    Everything happens locally: no cloud service, API key or downloaded model is
    involved.  Windows installs and maintains its own voice packages.
    
    Important truth: Windows 11 Narrator Natural Voice packages are currently not
    exposed to third-party desktop apps through either API on most builds.  This
    module detects those packages and reports them as Narrator-only instead of
    pretending they are usable.  A voice is only labelled "Natural" when Windows
    metadata actually says so (``VoiceType=Neural`` or an equivalent attribute),
    never merely because it is an OneCore voice.
    """
    from __future__ import annotations
    
    import base64
    import json
    import logging
    import queue
    import re
    import subprocess
    import threading
    import time
    from dataclasses import dataclass
    from typing import Callable
    from xml.sax.saxutils import escape as _xml_escape
    
    log = logging.getLogger("talcon.speech")
    
    # -- backend identifiers ---------------------------------------------------
    BACKEND_AUTO = "auto"
    BACKEND_WINDOWS = "windows"
    BACKEND_SAPI = "sapi"
    
    SPEECH_BACKEND_OPTIONS = (
        (BACKEND_AUTO, "Auto (Recommended)"),
        (BACKEND_WINDOWS, "Windows Natural Voice"),
        (BACKEND_SAPI, "Legacy Windows SAPI"),
    )
    SPEECH_BACKEND_LABELS = dict(SPEECH_BACKEND_OPTIONS)
    
    ENGINE_NATURAL = "windows-natural"
    ENGINE_ONECORE = "windows-onecore"
    ENGINE_SAPI = "sapi"
    ENGINE_SAPI_NATURAL = "sapi-natural"
    
    ENGINE_LABELS = {
        ENGINE_NATURAL: "Windows Natural Voice",
        ENGINE_ONECORE: "Windows OneCore voice",
        ENGINE_SAPI: "Legacy Windows SAPI",
        ENGINE_SAPI_NATURAL: "SAPI Natural Voice",
    }
    
    NATURAL_PREVIEW = (
        "Hello, sir. I'm TALCON, your personal desktop assistant. "
        "How can I help you today?"
    )
    
    _BACKEND_ALIASES = {
        "": BACKEND_AUTO,
        "auto": BACKEND_AUTO,
        "recommended": BACKEND_AUTO,
        "windows": BACKEND_WINDOWS,
        "natural": BACKEND_WINDOWS,
        "winrt": BACKEND_WINDOWS,
        "sapi": BACKEND_SAPI,
        "legacy": BACKEND_SAPI,
        "system": BACKEND_SAPI,
    }
    
    
    def normalize_backend(value) -> str:
        """Return a known backend id, falling back to Auto."""
        return _BACKEND_ALIASES.get(str(value or "").strip().lower(), BACKEND_AUTO)
    
    
    # -- voice metadata --------------------------------------------------------
    _NATURAL_WORD_RE = re.compile(r"(?i)\b(natural|neural)\b")
    _NATURAL_PAREN_RE = re.compile(r"(?i)\(\s*(natural|neural)\s*\)")
    _NATURAL_TYPES = ("neural", "natural", "premium", "hd")
    
    
    def _natural_evidence(
        name: str,
        voice_id: str = "",
        description: str = "",
        voice_type: str = "",
        natural_flag=None,
    ) -> bool:
        """Decide from Windows metadata, not from a name alone.
    
        ``voice_type`` comes from the voice token's ``Attributes`` registry key
        (``VoiceType=Neural``) when Windows exposes it.  Name/description matching
        is only a secondary signal for voices that do not publish a token type.
        """
        if natural_flag is True:
            return True
        if natural_flag is False:
            # An explicit false flag is not proof that the voice is OneCore; some
            # engines omit the attribute.  Fall through to the other metadata.
            pass
        kind = str(voice_type or "").strip().lower()
        if kind and any(token in kind for token in _NATURAL_TYPES):
            return True
        text = f"{name} {description} {voice_id}"
        if _NATURAL_PAREN_RE.search(text) or _NATURAL_WORD_RE.search(text):
            return True
        # Registry-style ids may join words without a boundary, e.g.
        # ``Microsoft.Speech.NaturalVoice``.
        lowered_id = str(voice_id or "").lower()
        if "natural" in lowered_id or "neural" in lowered_id:
            return True
        return False
    
    
    def classify_windows_voice(
        name: str,
        voice_id: str = "",
        description: str = "",
        voice_type: str = "",
        natural_flag=None,
    ) -> str:
        """Classify a WinRT voice without ever calling an OneCore voice Natural."""
        if _natural_evidence(name, voice_id, description, voice_type, natural_flag):
            return ENGINE_NATURAL
        return ENGINE_ONECORE
    
    
    def classify_sapi_voice(
        name: str,
        voice_id: str = "",
        description: str = "",
        voice_type: str = "",
        natural_flag=None,
    ) -> str:
        """Classify a SAPI voice; a natural voice can be registered there too."""
        if _natural_evidence(name, voice_id, description, voice_type, natural_flag):
            return ENGINE_SAPI_NATURAL
        return ENGINE_SAPI
    
    
    def language_label(tag: str) -> str:
        """Turn a BCP-47 tag such as ``en-IN`` into a friendly label."""
        raw = str(tag or "").strip()
        if not raw:
            return "Unknown language"
        names = {
            "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French",
            "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
            "ko": "Korean", "zh": "Chinese", "ar": "Arabic", "ru": "Russian",
        }
        regions = {
            "IN": "India", "US": "United States", "GB": "United Kingdom",
            "AU": "Australia", "CA": "Canada", "NZ": "New Zealand",
            "IE": "Ireland", "ZA": "South Africa", "SG": "Singapore",
        }
        parts = raw.replace("_", "-").split("-")
        base = names.get(parts[0].lower(), parts[0].upper())
        if len(parts) > 1:
            region = parts[-1].upper()
            return f"{base} ({regions.get(region, region)})"
        return base
    
    
    @dataclass(frozen=True)
    class VoiceInfo:
        """Truthful description of one installed voice.
    
        ``engine`` is the classification label (Natural, OneCore, SAPI), while
        ``backend`` identifies the synthesis API (``windows`` or ``sapi``).  The
        two must never be conflated: a Natural voice can, in principle, be
    
        Cutting at exactly ``limit`` characters used to slice words in half; this
        keeps whole sentences so the last thing spoken still makes sense.
        """
        text = str(text or "")
        if len(text) <= limit:
            return text
        window = text[:limit]
        floor = int(limit * 0.45)
        for separator in (". ", "! ", "? ", "; ", ", "):
            index = window.rfind(separator)
            if index >= floor:
                return window[: index + 1].strip()
        return window.rsplit(" ", 1)[0].strip() or window.strip()
    
    
    def escape_ssml(text: str) -> str:
        """Escape user text so it can never create SSML markup."""
        cleaned = _CONTROL.sub(" ", str(text or ""))
        return _xml_escape(cleaned, {'"': "&quot;", "'": "&apos;"})
    
    
    def _ends_with_abbreviation(text: str) -> bool:
        token = text.rstrip().rsplit(" ", 1)[-1].strip('"\'()[]')
        lowered = token.lower().rstrip(".")
        if lowered in _ABBREVIATIONS:
            return True
        # Initials such as "J." or "A." should not split a sentence.
        if len(token) == 2 and token[0].isalpha() and token[1] == ".":
            return token[0].isupper()
        return False
    
    
    def _split_sentences(text: str) -> list[str]:
        parts = [part for part in _SENTENCE_SPLIT.split(text) if part]
        merged: list[str] = []
        for part in parts:
            if merged and _ends_with_abbreviation(merged[-1]):
                merged[-1] = f"{merged[-1]} {part}"
            else:
                merged.append(part)
        return merged
    
    
    def build_ssml(text: str, lang: str = "en-US") -> str:
        """Build conservative, well-formed SSML in Windows' supported profile.
    
        The public ``SynthesizeSsmlToStreamAsync`` API rejects ``version="1.1"``
        on current Windows builds, so TALCON emits the SSML 1.0 profile: the same
        ``<speak>``/``<break>`` subset with correct XML escaping.  Rate and volume
        are applied through the synthesizer options, which every Windows voice
        supports, rather than through engine-specific prosody attributes.
        """
        raw = _WHITESPACE.sub(" ", _CONTROL.sub(" ", str(text or ""))).strip()
        if not raw:
            return (
                f'<speak version="1.0" xmlns="{_SSML_NS}" '
                f'xml:lang="{escape_ssml(lang)}"></speak>'
            )
        sentences = _split_sentences(raw)
        body = '<break time="220ms"/>'.join(escape_ssml(part) for part in sentences)
        safe_lang = escape_ssml(lang or "en-US")
        return (
            f'<speak version="1.0" xmlns="{_SSML_NS}" xml:lang="{safe_lang}">'
            f"{body}</speak>"
        )
    
    
    def speech_payload(
        text: str,
        voice_id: str = "",
        lang: str = "en-US",
        rate: float = 1.0,
        volume: float = 1.0,
        ssml: bool = True,
    ) -> dict:
        """Payload sent to a host: text, optional voice id, in-memory SSML."""
        return {
            "text": str(text or ""),
            "voice_id": str(voice_id or ""),
            "lang": str(lang or "en-US"),
            "ssml": build_ssml(text, lang) if ssml else "",
            "rate": max(0.5, min(1.5, float(rate))),
            "volume": max(0.0, min(1.0, float(volume))),
        }
    
    
    # -- PowerShell hosts ------------------------------------------------------
    _HOST_COMMON = r"""
    function B64([string]$s) {
      [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s))
    }
    function Decode([string]$s) {
      [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
    }
    function Emit([string]$s) {
      [Console]::Out.WriteLine($s)
      [Console]::Out.Flush()
    }
    """
    
    _SAPI_PS_SCRIPT = _HOST_COMMON + r"""
    $ErrorActionPreference='Stop'
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Rate = 0
    $synth.Volume = 90
    $script:lastVoiceName = $synth.Voice.Name
    $script:lastMode = ''
    $script:lastFallback = ''
    Emit 'READY'
    
    function SetVoiceByName([string]$wanted) {
      foreach ($v in $synth.GetInstalledVoices()) {
        $i = $v.VoiceInfo
                Language = $v.Language
                Gender = [string]$v.Gender
                Description = $v.Description
                VoiceType = $meta.VoiceType
                Natural = [bool]$meta.Natural
                Ssml = $true
              }
            }
            $json = $items | ConvertTo-Json -Depth 5 -Compress
            if ($null -eq $json) { $json = '[]' }
            Emit ('VOICES:' + (B64 $json))
          }
          'PING' { Emit 'PONG' }
        }
      } catch {
        Emit ('ERR:' + (B64 $_.Exception.Message))
      } finally {
        if ($cmd -eq 'SAY') { Emit 'DONE' }
      }
    }
    """
    
    
    def _decode_message(value: str) -> str:
        """Decode a base64 host message, tolerating older plain-text errors."""
        try:
            return base64.b64decode(value).decode("utf-8", "replace")
        except Exception:
            return value
    
    
    class _PowerShellHost:
        """Shared persistent-host plumbing for both speech backends."""
    
        script = ""
        reader_name = "TALCON-TTS-Host"
        backend_kind = ""
    
        def __init__(self, on_error: Callable[[str], None] | None = None):
            self.on_error = on_error or (lambda message: None)
            self._process: subprocess.Popen | None = None
            self._lock = threading.Lock()
            self._ready = threading.Event()
            self._done = threading.Event()
            self._voices_ready = threading.Event()
            self._voices: list[VoiceInfo] = []
            self._reader: threading.Thread | None = None
            self._last_error = ""
            self._say_failed = threading.Event()
            self._voice_ok = threading.Event()
            self._voice_err = ""
            self._voice_confirmed_id = ""
            self._voice_confirmed_name = ""
            self._pending_voice = ""
            self._pending_voice_language = ""
            self._used_event = threading.Event()
            self._used: dict = {}
            self._state_ready = threading.Event()
            self._state: dict = {}
            self._pending_rate = 0
            self._pending_volume = 90
    
        # -- process lifecycle -------------------------------------------------
        def ensure_started(self, timeout: float = 25.0) -> bool:
            with self._lock:
                if self._process and self._process.poll() is None and self._ready.is_set():
                    return True
                self._start_locked()
                process = self._process
            if process is None:
                return False
            if not self._ready.wait(timeout):
                self.on_error("Windows speech engine did not start.")
                self.stop()
                return False
            self._apply_pending()
            return True
    
        def _start_locked(self) -> None:
            self.stop(lock=False)
            encoded = base64.b64encode(self.script.encode("utf-16-le")).decode("ascii")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._process = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                     "Bypass", "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=flags,
                )
            except Exception as exc:
                self._process = None
                self._last_error = str(exc)
                log.exception("Could not launch the Windows speech host")
                self.on_error(f"Windows speech is unavailable: {exc}")
                return
            self._ready.clear()
            self._done.clear()
            self._voices_ready.clear()
            self._voice_ok.clear()
            self._used_event.clear()
            self._state_ready.clear()
            self._reader = threading.Thread(target=self._read_loop, name=self.reader_name, daemon=True)
            self._reader.start()
    
        def _read_loop(self) -> None:
            process = self._process
            if process is None or process.stdout is None:
                return
            try:
                for raw in process.stdout:
                    line = raw.strip()
                    if not line:
                        continue
                    if line == "READY":
                        self._ready.set()
                    elif line == "DONE":
                        self._done.set()
                    elif line.startswith("VOICES:"):
                        try:
                            data = json.loads(_decode_message(line[7:]))
                            self._voices = parse_voice_payload(data, self.backend_kind)
                        except Exception:
                            log.exception("Could not read the Windows voice list")
                            self._voices = []
                        self._voices_ready.set()
                    elif line.startswith("VOICE_OK:"):
                        self._voice_confirmed_id = _decode_message(line[9:]).strip()
                        for voice in self._voices:
                            if voice.id == self._voice_confirmed_id:
                                self._voice_confirmed_name = voice.name
                                break
                        self._voice_err = ""
                        self._voice_ok.set()
                    elif line.startswith("VOICE_ERR:"):
                        self._voice_err = _decode_message(line[10:]).strip()
                        self._voice_ok.set()
                    elif line.startswith("USED:"):
                        try:
                            self._used = json.loads(_decode_message(line[5:]))
                        except Exception:
                            log.exception("Could not read the synthesis result")
                            self._used = {}
                        self._used_event.set()
                    elif line.startswith("STATE:"):
                        try:
                            self._state = json.loads(_decode_message(line[6:]))
                        except Exception:
                            self._state = {}
                        self._state_ready.set()
                    elif line.startswith("ERR:"):
                        message = _decode_message(line[4:])
                        log.warning("Speech host error: %s", message)
                        self._last_error = message
                        self._say_failed.set()
            except Exception:
                log.exception("Speech host reader stopped")
            finally:
                self._ready.clear()
                self._done.set()
                self._voices_ready.set()
                self._voice_ok.set()
                self._used_event.set()
                self._state_ready.set()
                if self._process is process:
                    self._say_failed.set()
                    self.on_error("Windows speech host stopped.")
    
        def stop(self, lock: bool = True) -> None:
            if lock:
                self._lock.acquire()
            try:
                process = self._process
                self._process = None
                self._ready.clear()
                self._done.set()
                self._voice_ok.set()
                self._used_event.set()
                self._state_ready.set()
                if process is not None:
                    try:
                        if process.poll() is None and process.stdin:
                            process.stdin.write("EXIT\n")
                            process.stdin.flush()
                    except Exception:
                        pass
                    try:
                        process.terminate()
                        process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    for stream in (process.stdin, process.stdout):
                        try:
                            if stream:
                                stream.close()
                        except Exception:
                            pass
            finally:
                if lock:
                    self._lock.release()
    
        # -- commands ----------------------------------------------------------
        def _write(self, line: str) -> bool:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                return False
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
                return True
            except Exception:
                log.exception("Could not write to the speech host")
                return False
    
        def _apply_pending(self) -> None:
            self.set_rate(self._pending_rate)
            self.set_volume(self._pending_volume)
            if self._pending_voice:
                self.set_voice(self._pending_voice, wait=True)
    
        def set_rate(self, rate: int) -> None:
            self._pending_rate = max(-10, min(10, int(rate)))
            if self._ready.is_set():
                self._write(f"RATE:{self._rate_value(self._pending_rate)}")
    
        def set_volume(self, volume: int) -> None:
            self._pending_volume = max(0, min(100, int(volume)))
            if self._ready.is_set():
                self._write(f"VOLUME:{self._volume_value(self._pending_volume)}")
    
        def _known_voice(self, candidate: str) -> VoiceInfo | None:
            lowered = candidate.lower()
            for voice in self._voices:
                if voice.id == candidate or voice.name.lower() == lowered:
                    return voice
            return None
    
        def request_voice(self, name: str) -> bool:
            """Send a voice selection without waiting for confirmation."""
            candidate = str(name or "")
            if not candidate:
                return False
            if self._voices:
                match = self._known_voice(candidate)
                if match is None:
                    self._last_error = f"Voice is not installed: {candidate}"
                    return False
                self._pending_voice = match.id
                self._pending_voice_language = match.language
            else:
                self._pending_voice = candidate
            if self._ready.is_set():
                self._voice_ok.clear()
                self._voice_err = ""
                target = self._pending_voice or candidate
                payload = base64.b64encode(target.encode("utf-8")).decode("ascii")
                return self._write("VOICE:" + payload)
            return True
    
        def wait_voice(self, timeout: float = 3.0) -> tuple[bool, str, str]:
            """Wait for the host to confirm the requested voice is active."""
            if not self._pending_voice:
                return True, "", ""
            if not self._ready.is_set():
                return False, "", "The Windows speech engine is not running."
            if not self._voice_ok.wait(timeout):
                return False, "", "Windows did not confirm the voice selection."
            if self._voice_err:
                return False, "", self._voice_err
            return True, self._voice_confirmed_id, ""
    
        def set_voice(self, name: str, wait: bool = True) -> bool:
            if not self.request_voice(name):
                self.on_error(self._last_error)
                return False
            if wait and self._ready.is_set():
                ok, _voice_id, error = self.wait_voice()
                if not ok:
                    self._last_error = error
                    self.on_error(error)
                    return False
            return True
    
        def _rate_value(self, rate: int) -> str:
            return str(rate)
    
        def _volume_value(self, volume: int) -> str:
            return str(volume)
    
        def voices(self, timeout: float = 15.0) -> list[str]:
            return [voice.name for voice in self.voice_details(timeout)]
    
        def voice_details(self, timeout: float = 15.0) -> list[VoiceInfo]:
            if not self.ensure_started():
                return []
            self._voices_ready.clear()
            if not self._write("LIST"):
                return []
            if not self._voices_ready.wait(timeout):
                return []
            return list(self._voices)
    
        def has_natural_voice(self, timeout: float = 10.0) -> bool:
            return any(voice.natural for voice in self.voice_details(timeout))
    
        def _send_say(self, payload: dict, text: str, timeout: float | None = None) -> bool:
            if not self.ensure_started():
                return False
            encoded = base64.b64encode(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
            self._say_failed.clear()
            self._used_event.clear()
            self._used = {}
            self._done.clear()
            if not self._write("SAY:" + encoded):
                return False
            limit = timeout or max(8.0, min(90.0, len(text) * 0.14 + 8))
            finished = self._done.wait(limit)
            if not finished:
                log.warning("Speech host did not confirm playback; restarting it")
                self.stop()
            return finished and not self._say_failed.is_set()
    
        @property
        def last_error(self) -> str:
            return self._last_error
    
        @property
        def last_synthesis(self) -> dict:
            return dict(self._used or {})
    
        def state(self, timeout: float = 3.0) -> dict:
            if not self.ensure_started():
                return {}
            self._state_ready.clear()
            if not self._write("STATE"):
                return {}
            if not self._state_ready.wait(timeout):
                return {}
            return dict(self._state or {})
    
        def stop_speaking(self) -> None:
            """Terminate the host: this immediately stops any current utterance."""
            self.stop()
    
    
    class SapiBackend(_PowerShellHost):
        """Persistent Windows PowerShell + ``System.Speech`` process."""
    
        script = _SAPI_PS_SCRIPT
        reader_name = "TALCON-SAPI"
        backend_kind = BACKEND_SAPI
    
        def say(self, text: str, timeout: float | None = None) -> bool:
            if not self.ensure_started():
                return False
            payload = {"text": str(text), "voice_id": self._pending_voice}
            return self._send_say(payload, text, timeout)
    
    
    class WindowsNaturalVoiceBackend(_PowerShellHost):
        """Persistent PowerShell host around the Windows Runtime speech API."""
    
        script = _WINRT_PS_SCRIPT
        reader_name = "TALCON-WinRT"
        backend_kind = BACKEND_WINDOWS
    
        def _rate_value(self, rate: int) -> str:
            return f"{max(0.5, min(1.5, 1.0 + (int(rate) * 0.05))):.2f}"
    
        def _volume_value(self, volume: int) -> str:
            return f"{max(0.0, min(1.0, int(volume) / 100.0)):.2f}"
    
        def say(self, text: str, timeout: float | None = None) -> bool:
            if not self.ensure_started():
                return False
            if self._pending_voice:
                ok, _voice_id, error = self.wait_voice(3.0)
                if not ok:
                    self._last_error = error
                    self.on_error(error)
                    return False
            rate = max(0.5, min(1.5, 1.0 + (self._pending_rate * 0.05)))
            volume = max(0.0, min(1.0, self._pending_volume / 100.0))
            payload = speech_payload(
                text,
                voice_id=self._pending_voice,
                lang=self._pending_voice_language or "en-US",
                rate=rate,
                volume=volume,
            )
            ok = self._send_say(payload, text, timeout)
            used = self.last_synthesis
            if ok and used.get("mode") == "text" and used.get("fallback"):
                log.warning("SSML synthesis failed; plain text was used: %s", used["fallback"])
            return ok
    
    
    def create_backend(kind: str, on_error: Callable[[str], None] | None = None) -> _PowerShellHost:
        """Create a backend without probing it."""
        if normalize_backend(kind) == BACKEND_WINDOWS:
            return WindowsNaturalVoiceBackend(on_error=on_error)
        return SapiBackend(on_error=on_error)
    
    
    # -- Narrator-only voice discovery ----------------------------------------
    _NARRATOR_CACHE: list[dict] | None = None
    _NARRATOR_LOCK = threading.Lock()
    _NARRATOR_SELECTED = ""
    
    
    def _parse_voice_package(name: str) -> dict | None:
        """Parse ``MicrosoftWindows.Voice.en-IN.Prabhat.1`` honestly."""
        parts = str(name or "").split(".")
        if len(parts) < 5 or parts[0] != "MicrosoftWindows" or parts[1] != "Voice":
            return None
    talcon/runtime.py-29-from .monitors.deepai import DeepAICodeMonitor
    talcon/runtime.py-30-from .monitors.opencode import OpenCodeMonitor
    talcon/runtime.py-31-from .pending_actions import PendingActions
    talcon/runtime.py-32-from .running_apps import close_running_app, enumerate_running_apps
    talcon/runtime.py-33-from .speech import (
    talcon/runtime.py:34:    BACKEND_AUTO, BACKEND_SAPI, BACKEND_WINDOWS, NATURAL_PREVIEW,
    talcon/runtime.py:35:    SPEECH_BACKEND_LABELS, SpeechOutput, VoiceInfo, narrator_note,
    talcon/runtime.py-36-    narrator_report, normalize_backend, select_voice,
    talcon/runtime.py-37-)
    talcon/runtime.py-38-from .voice import VoiceController, list_microphones
    talcon/runtime.py-39-
    talcon/runtime.py-40-log = logging.getLogger("talcon.runtime")
    --
    talcon/runtime.py-114-        self.speech = SpeechOutput(
    talcon/runtime.py-115-            enabled=not self.muted,
    talcon/runtime.py-116-            on_speaking=self._on_speech_state,
    talcon/runtime.py-117-            on_error=self._on_speech_error,
    talcon/runtime.py-118-            on_utterance=self._on_utterance,
    talcon/runtime.py:119:            backend_kind=self.settings.get("speech_backend", BACKEND_AUTO),
    talcon/runtime.py-120-        )
    talcon/runtime.py-121-        self.modes = ModeManager(self.modes_data, self.speech)
    talcon/runtime.py-122-        self.commands = CommandRouter(self.modes, self.speech, self)
    talcon/runtime.py-123-        self.commands.register_modes(self.modes_data)
    talcon/runtime.py-124-        self.custom = CustomStore(
    --
    talcon/runtime.py-378-            self._load_voices_locked()
    talcon/runtime.py-379-
    talcon/runtime.py-380-    def _voice_backend_key(self, info: dict) -> str:
    talcon/runtime.py-381-        """Which synthesis API's saved voice should be restored."""
    talcon/runtime.py-382-        for candidate in (info.get("active"), info.get("requested")):
    talcon/runtime.py:383:            if candidate in (BACKEND_WINDOWS, BACKEND_SAPI):
    talcon/runtime.py-384-                return str(candidate)
    talcon/runtime.py-385-        return ""
    talcon/runtime.py-386-
    talcon/runtime.py-387-    def _load_voices_locked(self) -> None:
    talcon/runtime.py-388-        """Resolve the backend, list real voices and restore the saved choice."""
    --
    talcon/runtime.py-937-        try:
    talcon/runtime.py-938-            return dict(self.speech.engine_info())
    talcon/runtime.py-939-        except Exception:
    talcon/runtime.py-940-            log.exception("Could not read speech engine information")
    talcon/runtime.py-941-            return {
    talcon/runtime.py:942:                "requested": self.settings.get("speech_backend", BACKEND_AUTO),
    talcon/runtime.py:943:                "requested_label": SPEECH_BACKEND_LABELS.get(
    talcon/runtime.py:944:                    self.settings.get("speech_backend", BACKEND_AUTO), "Auto"
    talcon/runtime.py-945-                ),
    talcon/runtime.py-946-                "active": "",
    talcon/runtime.py-947-                "active_label": "Not started yet",
    talcon/runtime.py-948-                "natural_available": False,
    talcon/runtime.py-949-                "note": "",
    talcon/runtime.py-950-            }
    talcon/runtime.py-951-
    talcon/runtime.py-952-    def set_speech_backend(self, kind: str) -> None:
    talcon/runtime.py-953-        normalized = normalize_backend(kind)
    talcon/runtime.py:954:        if normalized == self.settings.get("speech_backend", BACKEND_AUTO):
    talcon/runtime.py-955-            return
    talcon/runtime.py-956-        self.save_settings({"speech_backend": normalized})
    talcon/runtime.py-957-
    talcon/runtime.py-958-    def choose_speech_voice(self, voice_id: str, name: str = "") -> None:
    talcon/runtime.py-959-        """Persist and apply a voice chosen from the real installed list."""
    --
    talcon/runtime.py-1295-        voice_changed = (
    talcon/runtime.py-1296-            old.get("speech_voice_id") != normalized.get("speech_voice_id")
    talcon/runtime.py-1297-            or old.get("speech_voice") != normalized.get("speech_voice")
    talcon/runtime.py-1298-        )
    talcon/runtime.py-1299-        if backend_changed:
    talcon/runtime.py:1300:            self.speech.set_backend(normalized.get("speech_backend", BACKEND_AUTO))
    talcon/runtime.py-1301-            self._active_voice = None
    talcon/runtime.py-1302-            self.voice_details = []
    talcon/runtime.py-1303-            self.voices = []
    talcon/runtime.py-1304-            self._set_status("speech", "Switching speech engine…")
    talcon/runtime.py-1305-            self._set_status("speech_engine_active", "Starting…")
    --
    tests/test_speech.py-6-
    tests/test_speech.py-7-import pytest
    tests/test_speech.py-8-
    tests/test_speech.py-9-from talcon import speech
    tests/test_speech.py-10-from talcon.speech import (
    tests/test_speech.py:11:    BACKEND_AUTO, BACKEND_SAPI, BACKEND_WINDOWS, ENGINE_NATURAL, ENGINE_ONECORE,
    tests/test_speech.py-12-    ENGINE_SAPI, NATURAL_PREVIEW, SapiBackend, SpeechOutput, VoiceInfo,
    tests/test_speech.py-13-    WindowsNaturalVoiceBackend, build_ssml, classify_windows_voice,
    tests/test_speech.py-14-    parse_voice_payload, select_voice, speech_payload,
    tests/test_speech.py-15-)
    tests/test_speech.py-16-
    --
    tests/test_speech.py-395-
    tests/test_speech.py-396-    class FakeSapi(_SelectableBackend):
    tests/test_speech.py-397-        pass
    tests/test_speech.py-398-
    tests/test_speech.py-399-    def fake_create(kind, on_error=None):
    tests/test_speech.py:400:        backend = FakeWinRT(on_error) if kind == BACKEND_WINDOWS else FakeSapi(on_error)
    tests/test_speech.py-401-        created.append((kind, backend))
    tests/test_speech.py-402-        return backend
    tests/test_speech.py-403-
    tests/test_speech.py-404-    monkeypatch.setattr(speech, "create_backend", fake_create)
    tests/test_speech.py:405:    output = SpeechOutput(enabled=True, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-406-    try:
    tests/test_speech.py-407-        backend = output._ensure_backend()
    tests/test_speech.py-408-        assert isinstance(backend, FakeSapi)
    tests/test_speech.py:409:        assert output.active_backend == BACKEND_SAPI
    tests/test_speech.py:410:        assert created[0][0] == BACKEND_WINDOWS
    tests/test_speech.py-411-        assert created[0][1].stopped == 1
    tests/test_speech.py-412-        info = output.engine_info()
    tests/test_speech.py:413:        assert info["requested"] == BACKEND_AUTO
    tests/test_speech.py-414-        assert "No Windows Natural Voice" in info["note"]
    tests/test_speech.py-415-        assert info["natural_available"] is False
    tests/test_speech.py-416-    finally:
    tests/test_speech.py-417-        output.stop()
    tests/test_speech.py-418-
    --
    tests/test_speech.py-421-    class FakeWinRT(_SelectableBackend):
    tests/test_speech.py-422-        def voice_details(self, timeout=15.0):
    tests/test_speech.py-423-            return [
    tests/test_speech.py-424-                VoiceInfo(
    tests/test_speech.py-425-                    "natural-aria", "Microsoft Aria (Natural)", ENGINE_NATURAL,
    tests/test_speech.py:426:                    "en-US", "Female", backend=BACKEND_WINDOWS,
    tests/test_speech.py-427-                )
    tests/test_speech.py-428-            ]
    tests/test_speech.py-429-
    tests/test_speech.py-430-    monkeypatch.setattr(
    tests/test_speech.py-431-        speech, "create_backend",
    tests/test_speech.py-432-        lambda kind, on_error=None: FakeWinRT(on_error),
    tests/test_speech.py-433-    )
    tests/test_speech.py:434:    output = SpeechOutput(enabled=True, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-435-    try:
    tests/test_speech.py-436-        backend = output._ensure_backend()
    tests/test_speech.py-437-        assert isinstance(backend, FakeWinRT)
    tests/test_speech.py:438:        assert output.active_backend == BACKEND_WINDOWS
    tests/test_speech.py-439-        assert output.engine_info()["active_label"] == "Windows Natural Voice"
    tests/test_speech.py-440-        assert output.natural_available is True
    tests/test_speech.py-441-    finally:
    tests/test_speech.py-442-        output.stop()
    tests/test_speech.py-443-
    --
    tests/test_speech.py-446-    class FakeWinRT(_SelectableBackend):
    tests/test_speech.py-447-        def voice_details(self, timeout=15.0):
    tests/test_speech.py-448-            return [
    tests/test_speech.py-449-                VoiceInfo(
    tests/test_speech.py-450-                    "ravi", "Microsoft Ravi", ENGINE_ONECORE,
    tests/test_speech.py:451:                    "en-IN", "Male", backend=BACKEND_WINDOWS,
    tests/test_speech.py-452-                )
    tests/test_speech.py-453-            ]
    tests/test_speech.py-454-
    tests/test_speech.py-455-    monkeypatch.setattr(
    tests/test_speech.py-456-        speech, "create_backend",
    tests/test_speech.py-457-        lambda kind, on_error=None: FakeWinRT(on_error),
    tests/test_speech.py-458-    )
    tests/test_speech.py:459:    output = SpeechOutput(enabled=True, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-460-    try:
    tests/test_speech.py-461-        assert isinstance(output._ensure_backend(), FakeWinRT)
    tests/test_speech.py:462:        assert output.active_backend == BACKEND_WINDOWS
    tests/test_speech.py-463-        info = output.engine_info()
    tests/test_speech.py-464-        assert info["natural_available"] is False
    tests/test_speech.py-465-        assert info["active_label"] == "Windows OneCore voice"
    tests/test_speech.py-466-        assert "OneCore" in info["note"]
    tests/test_speech.py-467-    finally:
    --
    tests/test_speech.py-477-        pass
    tests/test_speech.py-478-
    tests/test_speech.py-479-    monkeypatch.setattr(
    tests/test_speech.py-480-        speech, "create_backend",
    tests/test_speech.py-481-        lambda kind, on_error=None: BrokenWinRT(on_error)
    tests/test_speech.py:482:        if kind == BACKEND_WINDOWS else FakeSapi(on_error),
    tests/test_speech.py-483-    )
    tests/test_speech.py:484:    output = SpeechOutput(enabled=True, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-485-    try:
    tests/test_speech.py-486-        assert isinstance(output._ensure_backend(), FakeSapi)
    tests/test_speech.py:487:        assert output.active_backend == BACKEND_SAPI
    tests/test_speech.py-488-    finally:
    tests/test_speech.py-489-        output.stop()
    tests/test_speech.py-490-
    tests/test_speech.py-491-
    tests/test_speech.py-492-def test_switching_backend_stops_the_previous_host(monkeypatch):
    --
    tests/test_speech.py-496-        speech, "create_backend",
    tests/test_speech.py-497-        lambda kind, on_error=None: new,
    tests/test_speech.py-498-    )
    tests/test_speech.py-499-    output = SpeechOutput(enabled=True, backend=old)
    tests/test_speech.py-500-    try:
    tests/test_speech.py:501:        output.set_backend(BACKEND_WINDOWS)
    tests/test_speech.py-502-        assert old.stopped == 1
    tests/test_speech.py:503:        assert output.requested_backend == BACKEND_WINDOWS
    tests/test_speech.py-504-        assert output._ensure_backend() is new
    tests/test_speech.py:505:        assert output.active_backend == BACKEND_WINDOWS
    tests/test_speech.py-506-    finally:
    tests/test_speech.py-507-        output.stop()
    tests/test_speech.py-508-
    tests/test_speech.py-509-
    tests/test_speech.py-510-def test_manual_windows_backend_is_reported_as_onecore_without_natural_voice():
    tests/test_speech.py-511-    backend = _SelectableBackend()
    tests/test_speech.py-512-    output = SpeechOutput(enabled=True, backend=backend)
    tests/test_speech.py-513-    try:
    tests/test_speech.py:514:        output._requested_backend = BACKEND_WINDOWS
    tests/test_speech.py:515:        output._active_backend = BACKEND_WINDOWS
    tests/test_speech.py-516-        output._natural_available = False
    tests/test_speech.py-517-        assert output.engine_info()["active_label"] == "Windows OneCore voice"
    tests/test_speech.py-518-    finally:
    tests/test_speech.py-519-        output.stop()
    tests/test_speech.py-520-
    --
    tests/test_speech.py-536-            return []
    tests/test_speech.py-537-
    tests/test_speech.py-538-    monkeypatch.setattr(
    tests/test_speech.py-539-        speech, "create_backend",
    tests/test_speech.py-540-        lambda kind, on_error=None: SlowWinRT(on_error)
    tests/test_speech.py:541:        if kind == BACKEND_WINDOWS else _SelectableBackend(on_error),
    tests/test_speech.py-542-    )
    tests/test_speech.py:543:    output = SpeechOutput(enabled=False, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-544-    worker = threading.Thread(target=output._ensure_backend, daemon=True)
    tests/test_speech.py-545-    probe = None
    tests/test_speech.py-546-    try:
    tests/test_speech.py-547-        worker.start()
    tests/test_speech.py-548-        assert wait_for(lambda: "probe" in created, 2.0)
    --
    tests/test_speech.py-552-        info = output.engine_info()
    tests/test_speech.py-553-        output.set_rate(3)
    tests/test_speech.py-554-        output.set_volume(40)
    tests/test_speech.py-555-        elapsed = time.monotonic() - started
    tests/test_speech.py-556-        assert elapsed < 0.5
    tests/test_speech.py:557:        assert info["requested"] == BACKEND_AUTO
    tests/test_speech.py-558-        assert info["active"] == ""
    tests/test_speech.py-559-    finally:
    tests/test_speech.py-560-        if probe is not None:
    tests/test_speech.py-561-            probe.release.set()
    tests/test_speech.py-562-        worker.join(5.0)
    --
    tests/test_speech.py-729-
    tests/test_speech.py-730-    details = [
    tests/test_speech.py-731-        VoiceInfo("sapi-david", "Microsoft David Desktop", ENGINE_SAPI, "en-US", "Male")
    tests/test_speech.py-732-    ]
    tests/test_speech.py-733-    info = {
    tests/test_speech.py:734:        "requested": BACKEND_AUTO,
    tests/test_speech.py-735-        "requested_label": "Auto (Recommended)",
    tests/test_speech.py:736:        "active": BACKEND_SAPI,
    tests/test_speech.py-737-        "active_label": "Legacy Windows SAPI",
    tests/test_speech.py-738-        "natural_available": False,
    tests/test_speech.py-739-        "note": "",
    tests/test_speech.py-740-    }
    tests/test_speech.py-741-    speech = _RuntimeSpeech(details, info)
    --
    tests/test_speech.py-772-
    tests/test_speech.py-773-    details = [
    tests/test_speech.py-774-        VoiceInfo("sapi-zira", "Microsoft Zira Desktop", ENGINE_SAPI, "en-US", "Female")
    tests/test_speech.py-775-    ]
    tests/test_speech.py-776-    info = {
    tests/test_speech.py:777:        "requested": BACKEND_AUTO,
    tests/test_speech.py-778-        "requested_label": "Auto (Recommended)",
    tests/test_speech.py:779:        "active": BACKEND_SAPI,
    tests/test_speech.py-780-        "active_label": "Legacy Windows SAPI",
    tests/test_speech.py-781-        "natural_available": False,
    tests/test_speech.py-782-        "note": "",
    tests/test_speech.py-783-    }
    tests/test_speech.py-784-    speech = _RuntimeSpeech(details, info)
    --
    tests/test_speech.py-814-
    tests/test_speech.py-815-    details = [
    tests/test_speech.py-816-        VoiceInfo("sapi-david", "Microsoft David Desktop", ENGINE_SAPI, "en-US", "Male")
    tests/test_speech.py-817-    ]
    tests/test_speech.py-818-    info = {
    tests/test_speech.py:819:        "requested": BACKEND_AUTO,
    tests/test_speech.py-820-        "requested_label": "Auto (Recommended)",
    tests/test_speech.py:821:        "active": BACKEND_SAPI,
    tests/test_speech.py-822-        "active_label": "Legacy Windows SAPI",
    tests/test_speech.py-823-        "natural_available": False,
    tests/test_speech.py-824-        "note": "No Windows Natural Voice is exposed to desktop apps; using legacy Windows SAPI.",
    tests/test_speech.py-825-    }
    tests/test_speech.py-826-    speech = _RuntimeSpeech(details, info)
    --
    tests/test_speech.py-855-                "Language": "en-IN",
    tests/test_speech.py-856-                "Gender": "Male",
    tests/test_speech.py-857-                "VoiceType": "Neural",
    tests/test_speech.py-858-            }
    tests/test_speech.py-859-        ],
    tests/test_speech.py:860:        BACKEND_WINDOWS,
    tests/test_speech.py-861-    )
    tests/test_speech.py-862-    assert voices[0].natural is True
    tests/test_speech.py-863-    assert voices[0].voice_type == "Neural"
    tests/test_speech.py-864-    assert voices[0].engine == ENGINE_NATURAL
    tests/test_speech.py-865-
    --
    tests/test_speech.py-880-def test_engine_info_reports_cached_narrator_voices(monkeypatch):
    tests/test_speech.py-881-    monkeypatch.setattr(
    tests/test_speech.py-882-        speech, "_NARRATOR_CACHE",
    tests/test_speech.py-883-        [{"display": "Microsoft Prabhat (Natural) - English (India)"}],
    tests/test_speech.py-884-    )
    tests/test_speech.py:885:    output = SpeechOutput(enabled=False, backend_kind=BACKEND_AUTO)
    tests/test_speech.py-886-    try:
    tests/test_speech.py-887-        info = output.engine_info()
    tests/test_speech.py-888-        assert info["narrator_voices"]
    tests/test_speech.py-889-        assert "Prabhat" in info["narrator_note"]
    tests/test_speech.py-890-    finally:
    --
    tests/test_speech.py-953-    )
    tests/test_speech.py-954-    details = [
    tests/test_speech.py-955-        VoiceInfo("sapi-zira", "Microsoft Zira Desktop", ENGINE_SAPI, "en-US", "Female")
    tests/test_speech.py-956-    ]
    tests/test_speech.py-957-    info = {
    tests/test_speech.py:958:        "requested": BACKEND_AUTO,
    tests/test_speech.py-959-        "requested_label": "Auto (Recommended)",
    tests/test_speech.py:960:        "active": BACKEND_SAPI,
    tests/test_speech.py-961-        "active_label": "Legacy Windows SAPI",
    tests/test_speech.py-962-        "natural_available": False,
    tests/test_speech.py-963-        "note": "",
    tests/test_speech.py-964-    }
    tests/test_speech.py-965-    speech = _RuntimeSpeech(details, info)
    --
    tests/test_speech.py-994-    )
    tests/test_speech.py-995-    details = [
    tests/test_speech.py-996-        VoiceInfo("sapi-zira", "Microsoft Zira Desktop", ENGINE_SAPI, "en-US", "Female")
    tests/test_speech.py-997-    ]
    tests/test_speech.py-998-    info = {
    tests/test_speech.py:999:        "requested": BACKEND_AUTO,
    tests/test_speech.py-1000-        "requested_label": "Auto (Recommended)",
    tests/test_speech.py:1001:        "active": BACKEND_SAPI,
    tests/test_speech.py-1002-        "active_label": "Legacy Windows SAPI",
    tests/test_speech.py-1003-        "natural_available": False,
    tests/test_speech.py-1004-        "note": "",
    tests/test_speech.py-1005-    }
    tests/test_speech.py-1006-    speech = _RuntimeSpeech(details, info)
    --
    talcon/ui/main_window.py-18-from .. import startup
    talcon/ui/main_window.py-19-from ..config import BUILTIN_MODES, LOGS_DIR, MODELS_DIR, RESERVED_MODE_KEYS, ROOT
    talcon/ui/main_window.py-20-from ..face_render import render_face_image
    talcon/ui/main_window.py-21-from ..model_setup import MODEL_LICENSE, model_is_ready, resolve_model
    talcon/ui/main_window.py-22-from ..runtime import TalconRuntime
    talcon/ui/main_window.py:23:from ..speech import BACKEND_AUTO, SPEECH_BACKEND_OPTIONS
    talcon/ui/main_window.py-24-from ..audio import calibrate_microphone
    talcon/ui/main_window.py-25-from ..voice import measure_input
    talcon/ui/main_window.py-26-from . import theme
    talcon/ui/main_window.py-27-from .components import Card, FacePreview, Heading, StatusText, Switch, footnote, muted
    talcon/ui/main_window.py-28-from .cron_jobs import CronJobsPage
    --
    talcon/ui/main_window.py-427-        output_row.addWidget(self.speech_switch)
    talcon/ui/main_window.py-428-        output.add_layout(output_row)
    talcon/ui/main_window.py-429-
    talcon/ui/main_window.py-430-        engine_row = QHBoxLayout()
    talcon/ui/main_window.py-431-        engine_row.addWidget(QLabel("Engine"))
    talcon/ui/main_window.py:432:        self.engine_combo = QComboBox()
    talcon/ui/main_window.py:433:        self.engine_combo.setMinimumWidth(260)
    talcon/ui/main_window.py:434:        for key, label in SPEECH_BACKEND_OPTIONS:
    talcon/ui/main_window.py:435:            self.engine_combo.addItem(label, key)
    talcon/ui/main_window.py:436:        engine_row.addWidget(self.engine_combo, 1)
    talcon/ui/main_window.py-437-        preview_voice = QPushButton("Preview voice")
    talcon/ui/main_window.py-438-        preview_voice.clicked.connect(self._preview_voice)
    talcon/ui/main_window.py-439-        engine_row.addWidget(preview_voice)
    talcon/ui/main_window.py-440-        output.add_layout(engine_row)
    talcon/ui/main_window.py-441-
    --
    talcon/ui/main_window.py-1129-        self.face_switch.toggled.connect(lambda checked: self._save({"show_face": checked}))
    talcon/ui/main_window.py-1130-        self.expressions_switch.toggled.connect(
    talcon/ui/main_window.py-1131-            lambda checked: self._save({"face_expressions": checked})
    talcon/ui/main_window.py-1132-        )
    talcon/ui/main_window.py-1133-        self.startup_switch.toggled.connect(self._on_startup_toggled)
    talcon/ui/main_window.py:1134:        self.engine_combo.currentIndexChanged.connect(self._on_engine_chosen)
    talcon/ui/main_window.py-1135-        self.voice_combo.currentIndexChanged.connect(self._on_voice_chosen)
    talcon/ui/main_window.py-1136-        self.mic_combo.currentIndexChanged.connect(self._on_mic_chosen)
    talcon/ui/main_window.py-1137-
    talcon/ui/main_window.py-1138-        bridge = self.runtime.bridge
    talcon/ui/main_window.py-1139-        bridge.status_changed.connect(self.update_statuses)
    --
    talcon/ui/main_window.py-1187-        self.volume_slider.blockSignals(True)
    talcon/ui/main_window.py-1188-        self.volume_slider.setValue(int(s.get("speech_volume", 90)))
    talcon/ui/main_window.py-1189-        self.volume_slider.blockSignals(False)
    talcon/ui/main_window.py-1190-        self._on_volume_changed(int(s.get("speech_volume", 90)))
    talcon/ui/main_window.py-1191-
    talcon/ui/main_window.py:1192:        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))
    talcon/ui/main_window.py-1193-
    talcon/ui/main_window.py-1194-        self.size_slider.blockSignals(True)
    talcon/ui/main_window.py-1195-        self.size_slider.setValue(int(s.get("face_size", 66)))
    talcon/ui/main_window.py-1196-        self.size_slider.blockSignals(False)
    talcon/ui/main_window.py-1197-        self.size_value.setText(f"{int(s.get('face_size', 66))} px")
    --
    talcon/ui/main_window.py-1213-        if hasattr(self.runtime, "speech_voice_details"):
    talcon/ui/main_window.py-1214-            details = self.runtime.speech_voice_details()
    talcon/ui/main_window.py-1215-        engine = (
    talcon/ui/main_window.py-1216-            self.runtime.speech_engine_info()
    talcon/ui/main_window.py-1217-            if hasattr(self.runtime, "speech_engine_info")
    talcon/ui/main_window.py:1218:            else {"requested": s.get("speech_backend", BACKEND_AUTO)}
    talcon/ui/main_window.py-1219-        )
    talcon/ui/main_window.py-1220-        self._populate_speech_voices(details, engine)
    talcon/ui/main_window.py-1221-        self._populate_microphones()
    talcon/ui/main_window.py-1222-        self._refresh_model_status()
    talcon/ui/main_window.py-1223-        self._update_mute_labels()
    --
    talcon/ui/main_window.py-1281-            # Programmatic sync must not look like a user edit (the spin
    talcon/ui/main_window.py-1282-            # boxes auto-save on valueChanged).
    talcon/ui/main_window.py-1283-            widget.blockSignals(True)
    talcon/ui/main_window.py-1284-            widget.setValue(float(s.get(key, default)))
    talcon/ui/main_window.py-1285-            widget.blockSignals(False)
    talcon/ui/main_window.py:1286:        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))
    talcon/ui/main_window.py-1287-        self._sync_menu_face_controls(s)
    talcon/ui/main_window.py-1288-        self._update_mute_labels()
    talcon/ui/main_window.py-1289-
    talcon/ui/main_window.py-1290-    def _on_settings_changed(self, settings: dict) -> None:
    talcon/ui/main_window.py-1291-        self.sync_controls()
    --
    talcon/ui/main_window.py-1446-        if callable(preview):
    talcon/ui/main_window.py-1447-            preview()
    talcon/ui/main_window.py-1448-        else:
    talcon/ui/main_window.py-1449-            self.runtime.test_voice()
    talcon/ui/main_window.py-1450-
    talcon/ui/main_window.py:1451:    def _sync_engine_combo(self, value: str) -> None:
    talcon/ui/main_window.py:1452:        index = self.engine_combo.findData(value)
    talcon/ui/main_window.py-1453-        if index < 0:
    talcon/ui/main_window.py:1454:            index = self.engine_combo.findData(BACKEND_AUTO)
    talcon/ui/main_window.py:1455:        if index >= 0 and index != self.engine_combo.currentIndex():
    talcon/ui/main_window.py:1456:            self.engine_combo.blockSignals(True)
    talcon/ui/main_window.py:1457:            self.engine_combo.setCurrentIndex(index)
    talcon/ui/main_window.py:1458:            self.engine_combo.blockSignals(False)
    talcon/ui/main_window.py-1459-
    talcon/ui/main_window.py-1460-    def _on_engine_chosen(self, index: int) -> None:
    talcon/ui/main_window.py:1461:        key = self.engine_combo.itemData(index)
    talcon/ui/main_window.py-1462-        if not key:
    talcon/ui/main_window.py-1463-            return
    talcon/ui/main_window.py-1464-        if hasattr(self.runtime, "set_speech_backend"):
    talcon/ui/main_window.py-1465-            self.runtime.set_speech_backend(key)
    talcon/ui/main_window.py-1466-        else:
    --
    talcon/ui/main_window.py-1521-            self.voice_combo.addItem("No usable Windows voices found")
    talcon/ui/main_window.py-1522-            self.voice_combo.setEnabled(False)
    talcon/ui/main_window.py-1523-        else:
    talcon/ui/main_window.py-1524-            self.voice_combo.setEnabled(True)
    talcon/ui/main_window.py-1525-        if engine:
    talcon/ui/main_window.py:1526:            self._sync_engine_combo(engine.get("requested", BACKEND_AUTO))
    talcon/ui/main_window.py-1527-        self._update_speech_engine_status()
    talcon/ui/main_window.py-1528-
    talcon/ui/main_window.py-1529-    def _update_speech_engine_status(self) -> None:
    talcon/ui/main_window.py-1530-        requested = str(self._status.get("speech_engine", "") or "")
    talcon/ui/main_window.py-1531-        active = str(self._status.get("speech_engine_active", "") or "")
    ✓ • 537ms

## Activity

    $ & .\.venv\Scripts\python.exe -m pip index versions kokoro-onnx; & .\.venv\Scripts\python.exe -m pip index versions onnxruntime; & .\.venv\Scripts\python.exe -m pip index versions piper-tts
