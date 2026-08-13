from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Config:
    web_host: str
    web_port: int
    icecast_host: str
    icecast_port: int
    icecast_mount: str
    icecast_source_user: str
    icecast_source_password: str
    icecast_admin_user: str
    icecast_admin_password: str
    icecast_public_url: str
    audio_bitrate: str
    enable_test_stream: bool
    playback_enabled: bool
    station_slug: str
    play_duration_seconds: int | None
    log_dir: str
    log_level: str
    log_max_bytes: int
    database_path: str

    @property
    def stream_url(self) -> str:
        return f"{self.icecast_public_url.rstrip('/')}{self.icecast_mount}"

    @property
    def icecast_status_url(self) -> str:
        return f"{self.icecast_public_url.rstrip('/')}/status.xsl"

    @property
    def icecast_internal_base(self) -> str:
        return f"http://{self.icecast_host}:{self.icecast_port}"

    @property
    def icecast_source_url(self) -> str:
        return (
            f"icecast://{self.icecast_source_user}:{self.icecast_source_password}"
            f"@{self.icecast_host}:{self.icecast_port}{self.icecast_mount}"
        )

    @classmethod
    def from_env(cls) -> "Config":
        public = _env("ICECAST_PUBLIC_URL", "http://127.0.0.1:18080")
        duration_raw = _env("PLAY_DURATION_SECONDS", "90")
        if duration_raw in {"", "0", "full", "none"}:
            play_duration: int | None = None
        else:
            play_duration = int(duration_raw)
        return cls(
            web_host=_env("WEB_HOST", "0.0.0.0"),
            web_port=_env_int("WEB_PORT", 8080),
            icecast_host=_env("ICECAST_HOST", "icecast"),
            icecast_port=_env_int("ICECAST_PORT", 8000),
            icecast_mount=_env("ICECAST_MOUNT", "/test"),
            icecast_source_user=_env("ICECAST_SOURCE_USER", "source"),
            icecast_source_password=_env("ICECAST_SOURCE_PASSWORD", "hackme"),
            icecast_admin_user=_env("ICECAST_ADMIN_USER", "admin"),
            icecast_admin_password=_env("ICECAST_ADMIN_PASSWORD", "hackme"),
            icecast_public_url=public,
            audio_bitrate=_env("AUDIO_BITRATE", "192k"),
            enable_test_stream=_env("ENABLE_TEST_STREAM", "0") not in {"0", "false", "False"},
            playback_enabled=_env("PLAYBACK_ENABLED", "1") not in {"0", "false", "False"},
            station_slug=_env("STATION_SLUG", "test") or "test",
            play_duration_seconds=play_duration,
            log_dir=_env("LOG_DIR", "/app/logs"),
            log_level=_env("LOG_LEVEL", "INFO").upper() or "INFO",
            log_max_bytes=_env_int("LOG_MAX_BYTES", 2_000_000),
            database_path=_env("DATABASE_PATH", "/data/radio.db"),
        )
