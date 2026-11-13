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
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": f"NOVA/{VERSION}"})
    with tempfile.TemporaryDirectory(dir=str(target_root), prefix=".nova-model-") as temp_name:
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
        "NOVA never uploads audio.\n",
        encoding="utf-8",
    )
    progress(100, "Speech model installed.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the offline Vosk speech model for NOVA")
    parser.add_argument("--target", default=None, help="models directory (default: project models/)")
    args = parser.parse_args(argv)

    def report(percent: int, text: str) -> None:
        print(f"[{percent:3d}%] {text}", flush=True)

    try:
        path = download(report, Path(args.target) if args.target else None)
    except Exception as exc:
        print(f"Model installation failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Speech model ready: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
