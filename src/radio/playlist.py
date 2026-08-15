from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Iterator

from radio.db import Database, Show, Station

log = logging.getLogger("radio")


@dataclass
class PlaylistSnapshot:
    station: Station
    shows: list[Show]


class Playlist:
    """Sequential rotation: oldest→newest within the active window, then repeat.

    The first cycle after start/restart begins at a random show, then continues
    in order (wrapping). Later cycles start from the first show again.
    """

    def __init__(
        self,
        db: Database,
        *,
        station_slug: str,
    ) -> None:
        self.db = db
        self.station_slug = station_slug
        self._randomize_next = True

    def start_from_random(self) -> None:
        self._randomize_next = True

    def load(self) -> PlaylistSnapshot:
        station = self.db.get_station_by_slug(self.station_slug)
        if not station:
            raise RuntimeError(f"Station not found: {self.station_slug}")
        if not station.enabled:
            raise RuntimeError(f"Station disabled: {self.station_slug}")
        shows = self.db.recent_shows(station.id)
        if not shows:
            raise RuntimeError(f"No enabled shows for station: {self.station_slug}")
        # recent_shows is newest-first; play oldest→newest within the window,
        # which matches “work through the current set” more naturally.
        shows = list(reversed(shows))
        return PlaylistSnapshot(station=station, shows=shows)

    def iter_cycle(self) -> Iterator[Show]:
        snap = self.load()
        shows = snap.shows
        offset = 0
        if self._randomize_next:
            offset = random.randrange(len(shows))
            self._randomize_next = False
            shows = shows[offset:] + shows[:offset]
        log.info(
            "Playlist cycle station=%s shows=%s start_offset=%s first=%s",
            snap.station.slug,
            len(shows),
            offset,
            shows[0].label or shows[0].url,
        )
        yield from shows
