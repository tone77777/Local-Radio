from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Optional

import requests

from radio.config import Config
from radio.state import ShowInfo, state

log = logging.getLogger("radio")


class TestStream:
    """Publishes a continuous 440 Hz sine tone to Icecast via FFmpeg."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._proc: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="test-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill_ffmpeg()
        if self._thread:
            self._thread.join(timeout=5)

    def _kill_ffmpeg(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        state.update(ffmpeg_running=False)

    def _wait_for_icecast(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        url = f"{self.config.icecast_internal_base}/status-json.xsl"
        while time.time() < deadline and not self._stop.is_set():
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    log.info("Icecast is reachable at %s", url)
                    return True
            except requests.RequestException:
                pass
            time.sleep(1)
        return False

    def _mount_is_live(self) -> bool:
        url = f"{self.config.icecast_internal_base}/status-json.xsl"
        try:
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return False

        source = (
            payload.get("icestats", {})
            .get("source")
        )
        if source is None:
            return False
        if isinstance(source, dict):
            sources = [source]
        else:
            sources = list(source)

        mount = self.config.icecast_mount
        for item in sources:
            listenurl = str(item.get("listenurl", ""))
            if mount in listenurl or item.get("server_name"):
                # Match by mount path in listenurl when present
                if mount.lstrip("/") in listenurl or listenurl.endswith(mount):
                    return True
                # Single-source servers often omit path nuance
                if len(sources) == 1 and item.get("bitrate"):
                    return True
        return False

    def _run(self) -> None:
        state.update(
            status="starting",
            running=True,
            message="Waiting for Icecast",
            current_show=ShowInfo(
                artist="Local Radio",
                title="Test tone (440 Hz)",
                url="test://sine-440hz",
            ),
            started_at=time.time(),
            last_error=None,
        )

        if not self._wait_for_icecast():
            msg = "Icecast did not become ready in time"
            log.error(msg)
            state.update(status="error", running=False, last_error=msg, message=msg)
            return

        bitrate = self.config.audio_bitrate
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-f",
            "mp3",
            "-content_type",
            "audio/mpeg",
            self.config.icecast_source_url,
        ]
        log.info("Starting test stream to %s%s", self.config.icecast_host, self.config.icecast_mount)

        while not self._stop.is_set():
            state.update(status="connecting", message="Connecting FFmpeg to Icecast")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            state.update(ffmpeg_running=True, status="playing", message="Test tone streaming")

            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.rstrip()
                if line:
                    log.debug("[FFMPEG] %s", line)
                if self._stop.is_set():
                    break
                # Periodically refresh mount status
                if "Icecast" in line or "Output #0" in line:
                    state.update(icecast_connected=self._mount_is_live())

            code = self._proc.wait()
            state.update(ffmpeg_running=False, icecast_connected=False)
            if self._stop.is_set():
                break
            log.warning("FFmpeg exited with code %s; restarting in 3s", code)
            state.update(status="error", last_error=f"FFmpeg exited ({code})", message="Restarting test stream")
            time.sleep(3)

        state.update(status="stopped", running=False, message="Test stream stopped")
