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
