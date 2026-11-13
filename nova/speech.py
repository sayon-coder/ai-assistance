"""Speech output through Windows' built-in SAPI voices (System.Speech).

A single small PowerShell host process is kept alive so the first word starts
immediately; no cloud service, key or package is involved. The host is stopped
and re-created on demand, which also makes muting interrupt current speech.
"""
from __future__ import annotations

import base64
import json
import logging
import queue
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("nova.speech")

_PS_SCRIPT = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$synth.Volume = 90
[Console]::Out.WriteLine('READY')
[Console]::Out.Flush()
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  if ($line -eq 'EXIT') { break }
  $idx = $line.IndexOf(':')
  if ($idx -lt 0) { $cmd = $line; $arg = '' } else { $cmd = $line.Substring(0,$idx); $arg = $line.Substring($idx+1) }
  try {
    switch ($cmd) {
      'SAY' {
        $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($arg))
        if ($text) { $synth.Speak($text) }
        [Console]::Out.WriteLine('DONE')
        [Console]::Out.Flush()
      }
      'VOICE' {
        $name = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($arg))
        if ($name) { try { $synth.SelectVoice($name) } catch { } }
      }
      'RATE' {
        [int]$rate = 0
        if ([int]::TryParse($arg, [ref]$rate)) { $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $rate)) }
      }
      'VOLUME' {
        [int]$vol = 0
        if ([int]::TryParse($arg, [ref]$vol)) { $synth.Volume = [Math]::Max(0, [Math]::Min(100, $vol)) }
      }
      'LIST' {
        $names = @()
        foreach ($v in $synth.GetInstalledVoices()) { if ($v.Enabled) { $names += $v.VoiceInfo.Name } }
        $json = ConvertTo-Json -InputObject $names -Compress
        if ($null -eq $json) { $json = '[]' }
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
        [Console]::Out.WriteLine('VOICES:' + $b64)
        [Console]::Out.Flush()
      }
      'PING' { [Console]::Out.WriteLine('PONG'); [Console]::Out.Flush() }
    }
  } catch {
    [Console]::Out.WriteLine('ERR:' + $_.Exception.Message)
    [Console]::Out.Flush()
  }
}
"""


class SapiBackend:
    """Persistent Windows PowerShell + System.Speech process."""

    def __init__(self, on_error: Callable[[str], None] | None = None):
        self.on_error = on_error or (lambda message: None)
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._voices_ready = threading.Event()
        self._voices: list[str] = []
        self._reader: threading.Thread | None = None
        self._last_error = ""
        self._pending_rate = 0
        self._pending_volume = 90
        self._pending_voice = ""

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
        encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
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
        self._reader = threading.Thread(target=self._read_loop, name="NOVA-SAPI", daemon=True)
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
                        data = json.loads(base64.b64decode(line[7:]).decode("utf-8"))
                        if isinstance(data, str):
                            data = [data]
                        self._voices = [str(v) for v in data if v]
                    except Exception:
                        log.exception("Could not read SAPI voice list")
                        self._voices = []
                    self._voices_ready.set()
                elif line.startswith("ERR:"):
                    log.warning("Speech host error: %s", line[4:])
                    self._last_error = line[4:]
        except Exception:
            log.exception("Speech host reader stopped")
        finally:
            self._ready.clear()
            self._done.set()
            self._voices_ready.set()
            # Only a genuine failure: when stop()/restart replaced the host,
            # self._process no longer points at this reader's process and the
            # shutdown was deliberate.
            if self._process is process:
                self.on_error("Windows speech host stopped.")

    def stop(self, lock: bool = True) -> None:
        if lock:
            self._lock.acquire()
        try:
            process = self._process
            self._process = None
            self._ready.clear()
            self._done.set()
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
            self.set_voice(self._pending_voice)

    def set_rate(self, rate: int) -> None:
        self._pending_rate = max(-10, min(10, int(rate)))
        if self._ready.is_set():
            self._write(f"RATE:{self._pending_rate}")

    def set_volume(self, volume: int) -> None:
        self._pending_volume = max(0, min(100, int(volume)))
        if self._ready.is_set():
            self._write(f"VOLUME:{self._pending_volume}")

    def set_voice(self, name: str) -> None:
        self._pending_voice = str(name or "")
        if self._pending_voice and self._ready.is_set():
            payload = base64.b64encode(self._pending_voice.encode("utf-8")).decode("ascii")
            self._write("VOICE:" + payload)

    def voices(self, timeout: float = 15.0) -> list[str]:
        if not self.ensure_started():
            return []
        self._voices_ready.clear()
        if not self._write("LIST"):
            return []
        if not self._voices_ready.wait(timeout):
            return []
        return list(self._voices)

    def say(self, text: str) -> bool:
        if not self.ensure_started():
            return False
        payload = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
        self._done.clear()
        if not self._write("SAY:" + payload):
            return False
        timeout = max(8.0, min(45.0, len(text) * 0.11 + 6))
        finished = self._done.wait(timeout)
        if not finished:
            log.warning("Speech host did not confirm completion; restarting it")
            self.stop()
        return finished

    def stop_speaking(self) -> None:
        """Terminate the host: this immediately stops any current utterance."""
        self.stop()


class SpeechOutput:
    """Thread-safe, muting-aware queue in front of the SAPI host."""

    def __init__(self, enabled: bool = True, on_speaking=None, on_error=None,
                 backend: SapiBackend | None = None):
        self.enabled = bool(enabled)
        self._on_speaking = on_speaking or (lambda speaking: None)
        self._on_error = on_error or (lambda message: None)
        self.backend = backend or SapiBackend(on_error=self._handle_backend_error)
        self._queue: queue.Queue = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._speaking = False
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._error_reported = False
        self._thread = threading.Thread(target=self._worker, name="NOVA-TTS", daemon=True)
        self._thread.start()

    def _handle_backend_error(self, message: str) -> None:
        if self._speaking:
            self._set_speaking(False)
        if not self._error_reported:
            self._error_reported = True
            self._on_error(str(message))

    def _set_speaking(self, value: bool) -> None:
        if self._speaking == value:
            return
        self._speaking = value
        if value:
            self._idle.clear()
        else:
            self._idle.set()
        try:
            self._on_speaking(value)
        except Exception:
            log.exception("Speaking callback failed")

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def is_busy(self) -> bool:
        with self._pending_lock:
            return self._speaking or self._pending > 0

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the currently playing utterance has finished."""
        return self._idle.wait(timeout)

    def wait_until_quiet(self, timeout: float | None = None) -> bool:
        """Wait for queued speech to start and finish, not just current playback."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._pending_lock:
                busy = self._speaking or self._pending > 0
            if not busy:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    # -- public API --------------------------------------------------------
    def start(self) -> None:
        # Warm the engine in the background so the first announcement is quick.
        threading.Thread(target=self._warm, name="NOVA-TTS-Warm", daemon=True).start()

    def _warm(self) -> None:
        try:
            self.backend.ensure_started()
        except Exception:
            log.exception("Speech warm-up failed")

    def voices(self) -> list[str]:
        try:
            voices = self.backend.voices()
        except Exception:
            log.exception("Could not list SAPI voices")
            voices = []
        return voices

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.clear()
            self.backend.stop_speaking()
            self._set_speaking(False)

    def clear(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        with self._pending_lock:
            self._pending = 0

    def say(self, text: str) -> None:
        if not self.enabled or not text:
            return
        if len(str(text)) > 700:
            text = str(text)[:700]
        # Count the item before it becomes visible to the worker.  If the count
        # were incremented after the put, a very fast utterance could finish
        # first and leave the busy counter stuck above zero forever.
        with self._pending_lock:
            self._pending += 1
        try:
            self._queue.put_nowait(str(text))
        except queue.Full:
            with self._pending_lock:
                self._pending = max(0, self._pending - 1)
            log.warning("Speech queue full; dropping announcement")

    def say_and_wait(self, text: str, timeout: float = 30.0) -> bool:
        """Queue an announcement and block until playback has really finished."""
        if not self.enabled or not text:
            return True
        self.say(text)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._pending_lock:
                pending = self._pending
            if pending <= 0 and not self._speaking:
                return True
            time.sleep(0.02)
        return False

    def set_voice(self, name: str) -> None:
        self.backend.set_voice(name)

    def set_rate(self, rate: int) -> None:
        self.backend.set_rate(rate)

    def set_volume(self, volume: int) -> None:
        self.backend.set_volume(volume)

    def stop(self) -> None:
        self._stop.set()
        self.clear()
        self.backend.stop()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._set_speaking(False)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                if not self.enabled:
                    continue
                self._set_speaking(True)
                self.backend.say(item)
            except Exception:
                log.exception("Speech output failed")
            finally:
                with self._pending_lock:
                    self._pending = max(0, self._pending - 1)
                if self._queue.empty():
                    self._set_speaking(False)
