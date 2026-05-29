"""Unified logger factory used across the monorepo.

All loggers in the project must be created via :func:`get_logger` so that
formatting, rotation, and handler-duplication behavior stay consistent with
convention v1.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Union

from . import config


_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(
    name: str,
    log_file: Union[str, Path, None] = None,
    level: str = "INFO",
) -> logging.Logger:
    """Return a configured logger with console + (optional) rotating file output.

    Calling this function twice with the same ``name`` returns the same logger
    without adding duplicate handlers. ``propagate`` is set to ``False`` so log
    records do not bubble to the root logger.

    Args:
        name: Logger name (commonly the module name or job slug).
        log_file: Either an absolute path, a relative path resolved under
            :data:`common.config.LOG_DIR`, or ``None`` for console-only.
        level: Log level name (e.g. ``"INFO"``, ``"DEBUG"``). Invalid names
            fall back to ``INFO``.

    Returns:
        A :class:`logging.Logger` ready for use.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(lvl)
    logger.propagate = False

    fmt = logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT)

    sh = logging.StreamHandler()
    sh.setLevel(lvl)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file is not None:
        path = Path(log_file)
        if not path.is_absolute():
            path = config.LOG_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)

        fh = RotatingFileHandler(
            filename=str(path),
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        fh.setLevel(lvl)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
