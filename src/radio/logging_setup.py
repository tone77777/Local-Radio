from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from radio.config import Config

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(config: Config) -> logging.Logger:
    """Configure app logging with a hard rotating size limit (~LOG_MAX_BYTES total)."""
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "radio.log"

    level = LEVELS.get(config.log_level.upper(), logging.INFO)

    logger = logging.getLogger("radio")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Keep total on-disk size near LOG_MAX_BYTES: current + one backup.
    max_bytes = max(config.log_max_bytes // 2, 50_000)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=1,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Quiet noisy libraries unless we're debugging.
    logging.getLogger("werkzeug").setLevel(logging.WARNING if level > logging.DEBUG else logging.DEBUG)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger.debug(
        "Logging ready level=%s file=%s max_bytes_each=%s backup_count=1",
        config.log_level.upper(),
        log_path,
        max_bytes,
    )
    return logger


def read_recent_log(config: Config, max_bytes: int = 48_000) -> str:
    """Return the newest portion of radio.log (and backup if needed)."""
    log_dir = Path(config.log_dir)
    primary = log_dir / "radio.log"
    backup = log_dir / "radio.log.1"

    chunks: list[bytes] = []
    remaining = max_bytes

    if primary.exists():
        data = primary.read_bytes()
        chunks.append(data[-remaining:])
        remaining -= len(chunks[-1])

    if remaining > 0 and backup.exists():
        data = backup.read_bytes()
        chunks.insert(0, data[-remaining:])

    if not chunks:
        return ""
    return b"".join(chunks).decode("utf-8", errors="replace")


def clear_logs(config: Config) -> None:
    """Wipe rotated log files so debugging starts clean."""
    log_dir = Path(config.log_dir)
    for name in ("radio.log", "radio.log.1"):
        path = log_dir / name
        if path.exists():
            path.write_text("", encoding="utf-8")

    logger = logging.getLogger("radio")
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.acquire()
            try:
                if handler.stream:
                    handler.stream.seek(0)
                    handler.stream.truncate()
                    handler.stream.flush()
            finally:
                handler.release()

    logger.info("Log cleared")


def filter_log_lines(text: str, min_level: str = "DEBUG") -> str:
    """Filter log text to lines at or above min_level (best-effort by level name)."""
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_level = min_level.upper()
    if min_level not in order:
        return text
    threshold = order.index(min_level)

    kept: list[str] = []
    for line in text.splitlines():
        level_idx = None
        for name in order:
            if f" {name} " in f" {line} ":
                level_idx = order.index(name)
                break
        if level_idx is None or level_idx >= threshold:
            kept.append(line)
    return "\n".join(kept)
