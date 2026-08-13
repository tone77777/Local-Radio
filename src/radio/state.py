from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ShowInfo:
    artist: str = "Local Radio"
    title: str = "Test tone"
    url: str = "test://sine-440hz"


@dataclass
class StationPlayback:
    status: str = "idle"
    running: bool = False
    current_show: ShowInfo = field(default_factory=ShowInfo)
    message: str = "Not playing"
    metadata_ok: bool | None = None
    metadata_detail: str | None = None
    last_error: str | None = None
    updated_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "running": self.running,
            "current_show": asdict(self.current_show),
            "message": self.message,
            "metadata_ok": self.metadata_ok,
            "metadata_detail": self.metadata_detail,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


@dataclass
class AppState:
    status: str = "stopped"
    running: bool = False
    ffmpeg_running: bool = False
    icecast_connected: bool = False
    metadata_ok: bool | None = None
    metadata_detail: str | None = None
    current_show: ShowInfo = field(default_factory=ShowInfo)
    active_station_slug: str | None = None
    started_at: float | None = None
    last_error: str | None = None
    message: str = "Idle"
    # Per-station now playing (keyed by station slug)
    station_playback: dict[str, StationPlayback] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        uptime = None
        if self.started_at is not None:
            uptime = int(time.time() - self.started_at)
        return {
            "status": self.status,
            "running": self.running,
            "ffmpeg_running": self.ffmpeg_running,
            "icecast_connected": self.icecast_connected,
            "metadata_ok": self.metadata_ok,
            "metadata_detail": self.metadata_detail,
            "current_show": asdict(self.current_show),
            "active_station_slug": self.active_station_slug,
            "started_at": self.started_at,
            "uptime_seconds": uptime,
            "last_error": self.last_error,
            "message": self.message,
            "stations": {
                slug: playback.snapshot() for slug, playback in self.station_playback.items()
            },
        }


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = AppState()

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._state, key, value)

            slug = self._state.active_station_slug
            if slug:
                playback = self._state.station_playback.get(slug)
                if playback is None:
                    playback = StationPlayback()
                    self._state.station_playback[slug] = playback
                if "status" in kwargs:
                    playback.status = self._state.status
                if "running" in kwargs:
                    playback.running = self._state.running
                if "current_show" in kwargs:
                    playback.current_show = self._state.current_show
                if "message" in kwargs:
                    playback.message = self._state.message
                if "metadata_ok" in kwargs:
                    playback.metadata_ok = self._state.metadata_ok
                if "metadata_detail" in kwargs:
                    playback.metadata_detail = self._state.metadata_detail
                if "last_error" in kwargs:
                    playback.last_error = self._state.last_error
                playback.updated_at = time.time()

    def set_active_station(self, slug: str) -> None:
        with self._lock:
            self._state.active_station_slug = slug
            if slug not in self._state.station_playback:
                self._state.station_playback[slug] = StationPlayback(
                    status="starting",
                    running=True,
                    message="Starting",
                    updated_at=time.time(),
                )

    def station_snapshot(self, slug: str) -> dict[str, Any]:
        with self._lock:
            playback = self._state.station_playback.get(slug)
            if playback is None:
                return StationPlayback().snapshot()
            snap = playback.snapshot()
            snap["is_active"] = self._state.active_station_slug == slug
            snap["ffmpeg_running"] = (
                self._state.ffmpeg_running if self._state.active_station_slug == slug else False
            )
            snap["icecast_connected"] = (
                self._state.icecast_connected if self._state.active_station_slug == slug else False
            )
            return snap

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.snapshot()


state = StateStore()
