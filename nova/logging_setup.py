"""Rotating local logging plus a crash hook that never shows a raw traceback dialog."""
from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR, VERSION

LOG_PATH = LOGS_DIR / "nova.log"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # pythonw.exe has no console; only add stderr when one exists.
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    log = logging.getLogger("nova")
    log.info("NOVA %s starting on Python %s", VERSION, sys.version.split()[0])

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.getLogger("nova.crash").critical("Unhandled exception:\n%s", text)

    sys.excepthook = _hook
    return log
