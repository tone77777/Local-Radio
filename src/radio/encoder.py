from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from radio.config import Config

log = logging.getLogger("radio")

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16le


class PersistentEncoder:
    """Keep one FFmpeg process connected to Icecast; ingest PCM via a FIFO.

    This avoids tearing down the Icecast source between shows (the failure mode
    from the prototype where each new FFmpeg reconnect dropped listeners).
    """

    def __init__(self, config: Config, fifo_path: str = "/tmp/local-radio-audio.pcm") -> None:
        self.config = config
        self.fifo_path = Path(fifo_path)
        self._proc: Optional[subprocess.Popen[str]] = None
        self._write_fd: Optional[int] = None
        self._log_thread: Optional[threading.Thread] = None
        self._stop_logs = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self.fifo_path.exists():
            self.fifo_path.unlink()
        os.mkfifo(self.fifo_path)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-i",
            str(self.fifo_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            self.config.audio_bitrate,
            "-f",
            "mp3",
            "-content_type",
            "audio/mpeg",
            self.config.icecast_source_url,
        ]
        log.info("Starting persistent Icecast encoder → %s%s", self.config.icecast_host, self.config.icecast_mount)
        self._stop_logs.clear()
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._log_thread = threading.Thread(target=self._pump_logs, name="encoder-log", daemon=True)
        self._log_thread.start()

        # Opening the write end unblocks ffmpeg's FIFO read.
        # Keep this FD open for the lifetime of the encoder so show feeders
        # closing their write end does not EOF the Icecast encoder.
        self._write_fd = os.open(str(self.fifo_path), os.O_WRONLY)
        log.info("Persistent encoder FIFO open at %s", self.fifo_path)

    def stop(self) -> None:
        self._stop_logs.set()
        if self._write_fd is not None:
            try:
                os.close(self._write_fd)
            except OSError:
                pass
            self._write_fd = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self.fifo_path.exists():
            try:
                self.fifo_path.unlink()
            except OSError:
                pass

    def write_fd(self) -> int:
        if self._write_fd is None:
            raise RuntimeError("Encoder write FD is not open")
        return self._write_fd

    def write_silence(self, seconds: float = 0.4) -> None:
        if self._write_fd is None:
            return
        frames = int(SAMPLE_RATE * seconds)
        chunk = b"\x00" * (frames * CHANNELS * BYTES_PER_SAMPLE)
        try:
            os.write(self._write_fd, chunk)
        except OSError as exc:
            log.warning("Failed writing silence pad: %s", exc)

    def ensure_running(self) -> None:
        if not self.running:
            log.warning("Persistent encoder died; restarting")
            self.stop()
            time.sleep(1)
            self.start()

    def _pump_logs(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop_logs.is_set():
                break
            line = line.rstrip()
            if line:
                log.debug("[FFMPEG-OUT] %s", line)
