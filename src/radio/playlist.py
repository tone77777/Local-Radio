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
    """Sequential rotation through the active window, then repeat.

    Order matches the station Active window UI (newest → oldest). Skip advances
    to the next show down that list. The first cycle after start/restart begins
    at a random show; Play now starts at a chosen show.
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
        self._start_show_id: int | None = None

    def start_from_random(self) -> None:
        self._start_show_id = None
        self._randomize_next = True

    def start_from_show(self, show_id: int) -> None:
        """Next cycle begins at show_id, then continues in order."""
        self._randomize_next = False
        self._start_show_id = show_id

    def load(self) -> PlaylistSnapshot:
        station = self.db.get_station_by_slug(self.station_slug)
        if not station:
            raise RuntimeError(f"Station not found: {self.station_slug}")
        if not station.enabled:
            raise RuntimeError(f"Station disabled: {self.station_slug}")
        shows = self.db.recent_shows(station.id)
        if not shows:
            raise RuntimeError(f"No enabled shows for station: {self.station_slug}")
        # recent_shows is newest-first — keep that so Skip matches the UI list.
        return PlaylistSnapshot(station=station, shows=list(shows))

    def iter_cycle(self) -> Iterator[Show]:
        snap = self.load()
        shows = snap.shows
        offset = 0
        if self._start_show_id is not None:
            target = self._start_show_id
            self._start_show_id = None
            for i, show in enumerate(shows):
                if show.id == target:
                    offset = i
                    break
            else:
                raise RuntimeError(
                    f"Show id={target} is not in the active playlist window "
                    f"for station={snap.station.slug}"
                )
            shows = shows[offset:] + shows[:offset]
        elif self._randomize_next:
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
