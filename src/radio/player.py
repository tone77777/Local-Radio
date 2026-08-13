from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Optional

import requests

from radio.config import Config
from radio.db import Database, Show
from radio.encoder import PersistentEncoder
from radio.metadata import IcecastMetadata
from radio.playlist import Playlist
from radio.source import build_feed_command, resolve_source
from radio.state import ShowInfo, state

log = logging.getLogger("radio")


class RadioPlayer:
    """One playlist engine: persistent Icecast output + sequenced show feeds."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.encoder = PersistentEncoder(config)
        self.metadata = IcecastMetadata(config)
        self.playlist = Playlist(
            db,
            station_slug=config.station_slug,
            random_start=config.random_start,
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._skip = threading.Event()
        self._reload = threading.Event()
        self._feed_procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._skip.clear()
        self._reload.clear()
        self._thread = threading.Thread(target=self._run, name="radio-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._skip.set()
        self._reload.set()
        self._kill_feeders()
        self.encoder.stop()
        if self._thread:
            self._thread.join(timeout=10)

    def skip(self) -> None:
        log.info("Skip requested")
        self._skip.set()
        self._kill_feeders()

    def reload_playlist(self) -> None:
        """Stop the current show and start a fresh playlist cycle from the DB."""
        log.info("Playlist reload requested")
        self._reload.set()
        self._skip.set()
        self._kill_feeders()

    def _kill_feeders(self) -> None:
        with self._lock:
            procs = list(self._feed_procs)
            self._feed_procs.clear()
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
        for proc in procs:
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

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

    def _run(self) -> None:
        state.update(
            status="starting",
            running=True,
            message="Waiting for Icecast",
            started_at=time.time(),
            last_error=None,
            active_station_slug=self.config.station_slug,
        )
        state.set_active_station(self.config.station_slug)
        if not self._wait_for_icecast():
            msg = "Icecast did not become ready in time"
            log.error(msg)
            state.update(status="error", running=False, last_error=msg, message=msg)
            return

        try:
            self.encoder.start()
        except Exception as exc:
            msg = f"Failed to start persistent encoder: {exc}"
            log.exception(msg)
            state.update(status="error", running=False, last_error=msg, message=msg)
            return

        state.update(ffmpeg_running=True, icecast_connected=True, status="playing")

        while not self._stop.is_set():
            try:
                self._reload.clear()
                for show in self.playlist.iter_cycle():
                    if self._stop.is_set() or self._reload.is_set():
                        break
                    self._skip.clear()
                    self._play_show(show)
                    if self._stop.is_set() or self._reload.is_set():
                        break
                    self.encoder.write_silence(0.35)
                if self._reload.is_set() and not self._stop.is_set():
                    log.info("Restarting playlist cycle after hard reload")
                    state.update(message="Playlist reloaded from database")
            except Exception as exc:
                msg = str(exc)
                log.exception("Playlist loop error: %s", msg)
                state.update(status="error", last_error=msg, message=msg)
                if self._stop.is_set():
                    break
                time.sleep(5)

        self.encoder.stop()
        state.update(
            status="stopped",
            running=False,
            ffmpeg_running=False,
            icecast_connected=False,
            message="Player stopped",
        )

    def _play_show(self, show: Show) -> None:
        self.encoder.ensure_running()
        state.update(status="resolving", message=f"Resolving {show.url}")
        log.info("Starting show id=%s url=%s", show.id, show.url)

        try:
            resolved = resolve_source(show.url)
        except Exception as exc:
            log.error("Skipping show; metadata/resolve failed: %s", exc)
            state.update(last_error=str(exc), message=f"Skipped (resolve failed): {show.url}")
            time.sleep(1)
            return

        # Prefer DB label when present for title hint, but trust yt-dlp for artist/title.
        artist = resolved.artist
        title = resolved.title
        if show.label and title == "Unknown":
            title = show.label

        state.update(
            status="playing",
            current_show=ShowInfo(artist=artist, title=title, url=show.url),
            message=f"Playing: {artist} — {title}",
            last_error=None,
        )

        started = self._feed_show(show.url)
        if not started:
            return

        self._publish_metadata_when_ready(artist, title)
        failed = self._wait_for_feeders()

        if self._skip.is_set() and not self._stop.is_set():
            log.info("Show skipped: %s", title)
            state.update(message=f"Skipped: {title}")
        elif failed:
            log.warning("Show failed: %s", title)
            state.update(message=f"Failed: {title}", last_error=f"Feed failed for {show.url}")
        else:
            log.info("Show finished: %s", title)
        self.db.mark_played(show.id)

    def _feeders_alive(self) -> bool:
        with self._lock:
            procs = list(self._feed_procs)
        return any(p.poll() is None for p in procs)

    def _publish_metadata_when_ready(self, artist: str, title: str) -> None:
        """Update Now Playing only after this show's feed is stably producing audio.

        Important: the Icecast mount often stays up between shows (persistent encoder),
        so mount_exists() alone is not enough — that caused metadata to flip to the next
        track before its audio was actually flowing (or even when the feed 403'd).
        """
        stable_since: float | None = None
        deadline = time.time() + 45
        while time.time() < deadline and not self._stop.is_set() and not self._skip.is_set():
            if not self._feeders_alive():
                log.error("Feed died before metadata publish (%s - %s)", artist, title)
                state.update(
                    metadata_ok=False,
                    metadata_detail="Feed died before metadata publish",
                )
                return
            now = time.time()
            if stable_since is None:
                stable_since = now
            # Require a short healthy feed window so we don't stamp metadata for a
            # show that immediately 403s while the previous mount is still live.
            if (now - stable_since) >= 2.5 and self.metadata.mount_exists():
                result = self.metadata.update_now_playing(artist, title, wait=False)
                state.update(
                    metadata_ok=result.ok,
                    metadata_detail=result.detail,
                    icecast_connected=True,
                )
                return
            time.sleep(0.25)
        if not self._skip.is_set() and not self._stop.is_set():
            log.error("Mount/feed not ready; metadata not sent for %s - %s", artist, title)
            state.update(
                metadata_ok=False,
                metadata_detail="Mount/feed not ready",
            )

    def _feed_show(self, url: str) -> bool:
        ytdlp_cmd, ffmpeg_cmd = build_feed_command(url, self.config.play_duration_seconds)
        log.info(
            "Feeding show into persistent encoder (duration=%s)",
            self.config.play_duration_seconds or "full",
        )
        try:
            ytdlp = subprocess.Popen(
                ytdlp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            # Binary PCM to the keep-alive FIFO FD. Parent keeps its own copy of
            # the FD open so Icecast encoder does not EOF between shows.
            ffmpeg = subprocess.Popen(
                ffmpeg_cmd,
                stdin=ytdlp.stdout,
                stdout=self.encoder.write_fd(),
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as exc:
            log.error("Failed to start feed pipeline: %s", exc)
            state.update(last_error=str(exc), message="Feed start failed")
            return False

        if ytdlp.stdout:
            ytdlp.stdout.close()

        with self._lock:
            self._feed_procs = [ytdlp, ffmpeg]
            self._ffmpeg_err = []
            self._ytdlp_err = []

        threading.Thread(
            target=self._pump_stderr_bytes,
            args=(ffmpeg, "[FFMPEG-IN]", "_ffmpeg_err"),
            name="ffmpeg-in-log",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stderr_bytes,
            args=(ytdlp, "[YTDLP]", "_ytdlp_err"),
            name="ytdlp-log",
            daemon=True,
        ).start()
        return True

    def _wait_for_feeders(self) -> bool:
        """Wait for current feed processes. Returns True if the show feed failed."""
        with self._lock:
            procs = list(self._feed_procs)
        while procs and not self._stop.is_set() and not self._skip.is_set():
            alive = [p for p in procs if p.poll() is None]
            if not alive:
                break
            time.sleep(0.2)

        with self._lock:
            finished = list(self._feed_procs)
            self._feed_procs.clear()
            ffmpeg_err = list(getattr(self, "_ffmpeg_err", []))
            ytdlp_err = list(getattr(self, "_ytdlp_err", []))

        if self._skip.is_set() or self._stop.is_set():
            self._kill_feeders()
            return False

        ffmpeg_code: int | None = None
        ytdlp_code: int | None = None
        for proc in finished:
            code = proc.poll()
            name = str(proc.args[0] if proc.args else "feed")
            if "ffmpeg" in name:
                ffmpeg_code = code
                if code not in (0, None):
                    log.warning("FFmpeg feed exited with code %s", code)
                    for line in ffmpeg_err[-15:]:
                        log.warning("[FFMPEG-IN] %s", line)
            elif "yt-dlp" in name:
                ytdlp_code = code
                if code not in (0, None):
                    # Expected when FFmpeg hits -t and closes the pipe; only loud-log
                    # if FFmpeg also failed.
                    broken_pipe = any("Broken pipe" in line for line in ytdlp_err)
                    if ffmpeg_code not in (0, None) or not broken_pipe:
                        log.warning("yt-dlp exited with code %s", code)
                        for line in ytdlp_err[-15:]:
                            log.warning("[YTDLP] %s", line)
                    else:
                        log.debug("yt-dlp exited %s after FFmpeg closed pipe (normal for -t)", code)

        # Success = decode FFmpeg finished cleanly. yt-dlp often exits 1 with
        # Broken pipe once FFmpeg stops reading after the duration limit.
        if ffmpeg_code == 0:
            return False
        if ffmpeg_code in (None,) and ytdlp_code == 0:
            return False
        return True

    @staticmethod
    def _pump_stderr(proc: subprocess.Popen, prefix: str) -> None:
        RadioPlayer._pump_stderr_bytes(proc, prefix, None)

    def _pump_stderr_bytes(
        self,
        proc: subprocess.Popen,
        prefix: str,
        bucket_attr: str | None = None,
    ) -> None:
        if proc.stderr is None:
            return
        for raw in proc.stderr:
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", "replace").rstrip()
            else:
                line = str(raw).rstrip()
            if not line:
                continue
            log.debug("%s %s", prefix, line)
            if bucket_attr:
                with self._lock:
                    bucket = getattr(self, bucket_attr, None)
                    if isinstance(bucket, list):
                        bucket.append(line)
                        if len(bucket) > 40:
                            del bucket[:-40]
