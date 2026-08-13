from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Iterator, Optional

from radio.db import Database, Show, Station

log = logging.getLogger("radio")


@dataclass
class PlaylistSnapshot:
    station: Station
    shows: list[Show]


class Playlist:
    """Sequential rotation with optional random start each cycle."""

    def __init__(
        self,
        db: Database,
        *,
        station_slug: str,
        random_start: bool = True,
    ) -> None:
        self.db = db
        self.station_slug = station_slug
        self.random_start = random_start
        self._start_offset = 0

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
        if self.random_start:
            self._start_offset = random.randrange(len(shows))
        else:
            self._start_offset = 0
        ordered = shows[self._start_offset :] + shows[: self._start_offset]
        log.info(
            "Playlist cycle station=%s shows=%s start_offset=%s first=%s",
            snap.station.slug,
            len(ordered),
            self._start_offset,
            ordered[0].label or ordered[0].url,
        )
        yield from ordered
