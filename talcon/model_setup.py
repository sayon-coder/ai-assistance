"""Download and install the free, offline Vosk English speech model.

Model: vosk-model-small-en-us-0.15
Licence: Apache License 2.0 (see https://github.com/alphacep/vosk-model-small-en-us-0.15)
Publisher: Alpha Cephei / Vosk (https://alphacephei.com/vosk/models)
Size: about 40 MB, runs on a CPU without a GPU.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .config import MODELS_DIR, VERSION, resolve_path

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_LICENSE = "Apache License 2.0"
MODEL_HOMEPAGE = "https://alphacephei.com/vosk/models"
MODEL_SIZE_MB = 40

Progress = Callable[[int, str], None]

# -- optional Kokoro neural voice -----------------------------------------
KOKORO_DIR_NAME = "kokoro"
KOKORO_RELEASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
KOKORO_FILES = {
    "kokoro-v1.0.int8.onnx": "kokoro-v1.0.int8.onnx",
    "voices-v1.0.bin": "voices-v1.0.bin",
}
KOKORO_LICENSE = "Apache License 2.0"
KOKORO_HOMEPAGE = "https://github.com/thewh1teagle/kokoro-onnx"

# -- optional sherpa-onnx streaming transducer ------------------------------
SHERPA_MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
SHERPA_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{SHERPA_MODEL_NAME}.tar.bz2"
)
SHERPA_LICENSE = "Apache License 2.0"
SHERPA_HOMEPAGE = "https://github.com/k2-fsa/sherpa-onnx"
SHERPA_SIZE_MB = 250


def model_is_ready(path: Path | str) -> bool:
    try:
        root = Path(path)
        return (root / "am" / "final.mdl").exists() and (root / "conf").is_dir()
    except OSError:
        return False


def default_model_path() -> Path:
    return MODELS_DIR / MODEL_NAME


def resolve_model(value: str | None) -> Path:
    if not value:
        return default_model_path()
    return resolve_path(value)


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if destination not in target.parents and target != destination:
            raise ValueError(f"Refusing unsafe archive member: {member.filename}")
    archive.extractall(destination)


def download(progress: Progress | None = None, target_root: Path | None = None) -> Path:
    """Download, verify and extract the model. Returns the installed folder."""
    progress = progress or (lambda percent, text: None)
    target_root = target_root or MODELS_DIR
    target = target_root / MODEL_NAME
    if model_is_ready(target):
        progress(100, "Speech model already installed.")
        return target

    target_root.mkdir(parents=True, exist_ok=True)
    progress(0, f"Downloading the free offline speech model ({MODEL_SIZE_MB} MB)…")
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": f"TALCON/{VERSION}"})
    with tempfile.TemporaryDirectory(dir=str(target_root), prefix=".talcon-model-") as temp_name:
        temp = Path(temp_name)
        archive_path = temp / f"{MODEL_NAME}.zip"
        with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                received += len(chunk)
                if total:
                    progress(min(80, int(received * 80 / total)),
                             f"Downloading… {received // (1024 * 1024)} of {max(1, total // (1024 * 1024))} MB")
        progress(84, "Verifying archive…")
        if not zipfile.is_zipfile(archive_path):
            raise RuntimeError("The downloaded speech model archive is not a valid ZIP file.")
        extract_dir = temp / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, extract_dir)
        source = extract_dir / MODEL_NAME
        if not model_is_ready(source):
            raise RuntimeError("The speech model archive is missing its expected files.")
        progress(92, "Installing speech model…")
        staging = target_root / f".{MODEL_NAME}.installing"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.move(str(source), str(staging))
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)

    (target_root / "MODEL_LICENSE.txt").write_text(
        f"{MODEL_NAME}\nLicence: {MODEL_LICENSE}\nSource: {MODEL_HOMEPAGE}\n\n"
        "The model is downloaded from the publisher above and used locally. "
        "TALCON never uploads audio.\n",
        encoding="utf-8",
    )
    progress(100, "Speech model installed.")
    return target


def download_kokoro(progress: Progress | None = None,
                    target_root: Path | None = None) -> Path:
    """Download the optional Kokoro-82M neural voice files (~118 MB).

    The quantised int8 model is used: it is the only version that is practical
    on a 4-core laptop CPU.  Files are downloaded next to the Vosk model and
    stay local; nothing is uploaded.
    """
    progress = progress or (lambda percent, text: None)
    target = (target_root or MODELS_DIR) / KOKORO_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(KOKORO_FILES):
        destination = target / name
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        progress(int(index * 100 / len(KOKORO_FILES)), f"Downloading {name}…")
        request = urllib.request.Request(
            f"{KOKORO_RELEASE}/{name}",
            headers={"User-Agent": f"TALCON/{VERSION}"},
        )
        partial = destination.with_suffix(destination.suffix + ".part")
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        partial.replace(destination)
    (target / "MODEL_LICENSE.txt").write_text(
        "kokoro-v1.0.int8.onnx + voices-v1.0.bin\n"
        f"Licence: {KOKORO_LICENSE}\nSource: {KOKORO_HOMEPAGE}\n\n"
        "The model is downloaded from the publisher and used locally. "
        "TALCON never uploads audio.\n",
        encoding="utf-8",
    )
    progress(100, "Kokoro neural voice installed.")
    return target


def sherpa_model_is_ready(path: Path | str) -> bool:
    from .asr.sherpa_engine import model_ready

    return model_ready(path)


def download_sherpa(progress: Progress | None = None,
                    target_root: Path | None = None) -> Path:
    """Download the optional sherpa-onnx English streaming transducer."""
    progress = progress or (lambda percent, text: None)
    target_root = target_root or MODELS_DIR
    target = target_root / SHERPA_MODEL_NAME
    if sherpa_model_is_ready(target):
        progress(100, "sherpa-onnx streaming model already installed.")
        return target

    target_root.mkdir(parents=True, exist_ok=True)
    progress(0, f"Downloading the sherpa-onnx model ({SHERPA_SIZE_MB} MB)…")
    request = urllib.request.Request(
        SHERPA_URL, headers={"User-Agent": f"TALCON/{VERSION}"}
    )
    with tempfile.TemporaryDirectory(
        dir=str(target_root), prefix=".talcon-sherpa-"
    ) as temp_name:
        temp = Path(temp_name)
        archive_path = temp / f"{SHERPA_MODEL_NAME}.tar.bz2"
        with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while True:
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                received += len(chunk)
                if total:
                    progress(
                        min(82, int(received * 82 / total)),
                        f"Downloading… {received // (1024 * 1024)} of "
                        f"{max(1, total // (1024 * 1024))} MB",
                    )
        progress(86, "Verifying and extracting the sherpa-onnx model…")
        extract_dir = temp / "extract"
        extract_dir.mkdir()
        with tarfile.open(archive_path, "r:bz2") as archive:
            try:
                archive.extractall(extract_dir, filter="data")
            except TypeError:  # Python older than 3.12 has no filter argument
                archive.extractall(extract_dir)
        source = extract_dir / SHERPA_MODEL_NAME
        if not source.is_dir():
            # Some releases nest the folder one level deeper.
            candidates = [
                item for item in extract_dir.iterdir()
                if item.is_dir() and sherpa_model_is_ready(item)
            ]
            source = candidates[0] if candidates else source
        if not sherpa_model_is_ready(source):
            raise RuntimeError(
                "The sherpa-onnx archive is missing its transducer files."
            )
        staging = target_root / f".{SHERPA_MODEL_NAME}.installing"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.move(str(source), str(staging))
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)

    (target / "MODEL_LICENSE.txt").write_text(
        f"{SHERPA_MODEL_NAME}\nLicence: {SHERPA_LICENSE}\n"
        f"Source: {SHERPA_HOMEPAGE}\n\n"
        "The model is downloaded from the publisher and used locally. "
        "TALCON never uploads audio.\n",
        encoding="utf-8",
    )
    progress(100, "sherpa-onnx streaming model installed.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install offline speech models for TALCON")
    parser.add_argument("--target", default=None, help="models directory (default: project models/)")
    parser.add_argument("--kokoro", action="store_true",
                        help="install the Kokoro neural voice (the only speech engine) instead of Vosk")
    parser.add_argument("--sherpa", action="store_true",
                        help="install the optional sherpa-onnx streaming recognition model")
    args = parser.parse_args(argv)

    def report(percent: int, text: str) -> None:
        print(f"[{percent:3d}%] {text}", flush=True)

    try:
        target = Path(args.target) if args.target else None
        if args.kokoro:
            path = download_kokoro(report, target)
        elif args.sherpa:
            path = download_sherpa(report, target)
        else:
            path = download(report, target)
    except Exception as exc:
        print(f"Model installation failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Speech model ready: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
