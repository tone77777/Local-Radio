from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

from radio.config import Config

log = logging.getLogger("radio")


@dataclass
class MetadataResult:
    ok: bool
    artist: str
    title: str
    song: str
    detail: str
    mount_ready: bool


class IcecastMetadata:
    """Update Icecast Now Playing separately from the audio/FFmpeg pipeline.

    Designed so multiple stations/mounts can use the same helper later by
    passing different Config values (host/mount/credentials).
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    @property
    def _admin_auth(self) -> tuple[str, str]:
        return (self.config.icecast_admin_user, self.config.icecast_admin_password)

    def mount_exists(self) -> bool:
        url = f"{self.config.icecast_internal_base}/status-json.xsl"
        try:
            response = requests.get(url, timeout=3)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return False

        source = payload.get("icestats", {}).get("source")
        if not source:
            return False
        return True

    def wait_for_mount(self, timeout: float = 15.0, interval: float = 0.5) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.mount_exists():
                return True
            time.sleep(interval)
        return False

    def update_now_playing(
        self,
        artist: str,
        title: str,
        *,
        wait: bool = True,
        timeout: float = 15.0,
    ) -> MetadataResult:
        song = f"{artist} - {title}".strip(" -")
        mount_ready = self.mount_exists()
        if not mount_ready and wait:
            log.info("Waiting for Icecast mount %s before metadata update", self.config.icecast_mount)
            mount_ready = self.wait_for_mount(timeout=timeout)

        if not mount_ready:
            detail = "Source does not exist (mount not ready)"
            log.error("Icecast metadata update failed: %s", detail)
            return MetadataResult(
                ok=False,
                artist=artist,
                title=title,
                song=song,
                detail=detail,
                mount_ready=False,
            )

        url = f"{self.config.icecast_internal_base}/admin/metadata"
        params = {
            "mount": self.config.icecast_mount,
            "mode": "updinfo",
            "song": song,
        }
        # Build manually so we can log the encoded form clearly at debug.
        query = (
            f"mount={quote(self.config.icecast_mount, safe='/')}"
            f"&mode=updinfo&song={quote(song)}"
        )
        full_url = f"{url}?{query}"
        log.info("Updating Icecast Now Playing: %s", song)
        log.debug("Metadata URL: %s", full_url)

        try:
            response = requests.get(
                url,
                params=params,
                auth=self._admin_auth,
                timeout=5,
            )
        except requests.RequestException as exc:
            detail = f"Request failed: {exc}"
            log.error("Icecast metadata update failed: %s", detail)
            return MetadataResult(
                ok=False,
                artist=artist,
                title=title,
                song=song,
                detail=detail,
                mount_ready=True,
            )

        body = (response.text or "").strip()
        ok = response.status_code == 200 and "Source does not exist" not in body
        if ok:
            log.info("Icecast metadata update successful")
            detail = "Metadata updated"
        else:
            detail = body or f"HTTP {response.status_code}"
            log.error("Icecast metadata update failed: %s", detail)

        return MetadataResult(
            ok=ok,
            artist=artist,
            title=title,
            song=song,
            detail=detail,
            mount_ready=True,
        )
