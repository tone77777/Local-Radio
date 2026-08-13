from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    mount TEXT NOT NULL DEFAULT '/test',
    enabled INTEGER NOT NULL DEFAULT 1,
    playlist_limit INTEGER,
    play_duration_seconds INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    label TEXT,
    created_at TEXT NOT NULL,
    last_played_at TEXT,
    play_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(station_id, url)
);

CREATE INDEX IF NOT EXISTS idx_shows_station_created
    ON shows(station_id, created_at DESC);
"""

SEED_TEST_URLS = [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "Me at the zoo"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Rick Astley — Never Gonna Give You Up"),
    ("https://www.youtube.com/watch?v=9bZkp7q19f0", "PSY — Gangnam Style"),
    ("https://www.youtube.com/watch?v=kJQP7kiw5Fk", "Luis Fonsi — Despacito"),
    ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "Big Buck Bunny"),
    ("https://www.youtube.com/watch?v=LXb3EKWsInQ", "Costa Rica in 4K"),
]

SEED_SPARE_URLS = [
    ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "Big Buck Bunny"),
    ("https://www.youtube.com/watch?v=LXb3EKWsInQ", "Costa Rica in 4K"),
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_play_duration(raw: str) -> int | None:
    """Return clip length in seconds, or None for a full show."""
    value = raw.strip().lower()
    if value in {"", "full", "none", "0"}:
        return None
    if value.isdigit():
        seconds = int(value)
        return seconds if seconds > 0 else None
    raise ValueError(f"Invalid clip length: {raw!r} (use a number of seconds or 'full')")


@dataclass
class Station:
    id: int
    name: str
    slug: str
    mount: str
    enabled: bool
    playlist_limit: Optional[int]
    play_duration_seconds: Optional[int]
    notes: Optional[str]
    created_at: str
    show_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Station":
        keys = row.keys()
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            mount=row["mount"],
            enabled=bool(row["enabled"]),
            playlist_limit=row["playlist_limit"],
            play_duration_seconds=(
                row["play_duration_seconds"]
                if "play_duration_seconds" in keys and row["play_duration_seconds"] is not None
                else None
            ),
            notes=row["notes"],
            created_at=row["created_at"],
            show_count=int(row["show_count"]) if "show_count" in keys else 0,
        )


@dataclass
class Show:
    id: int
    station_id: int
    url: str
    enabled: bool
    label: Optional[str]
    created_at: str
    last_played_at: Optional[str]
    play_count: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Show":
        return cls(
            id=row["id"],
            station_id=row["station_id"],
            url=row["url"],
            enabled=bool(row["enabled"]),
            label=row["label"],
            created_at=row["created_at"],
            last_played_at=row["last_played_at"],
            play_count=int(row["play_count"] or 0),
        )


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
        self.seed_if_empty()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stations)")}
        if "play_duration_seconds" not in cols:
            conn.execute("ALTER TABLE stations ADD COLUMN play_duration_seconds INTEGER")

    def seed_if_empty(self) -> None:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM stations").fetchone()["c"]
            if count:
                return
            now = _utcnow()
            cur = conn.execute(
                """
                INSERT INTO stations (name, slug, mount, enabled, playlist_limit, notes, created_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    "Test",
                    "test",
                    "/test",
                    5,
                    "Default development station (test tone mount).",
                    now,
                ),
            )
            test_id = cur.lastrowid
            for url, label in SEED_TEST_URLS:
                conn.execute(
                    """
                    INSERT INTO shows (station_id, url, enabled, label, created_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (test_id, url, label, now),
                )

            cur = conn.execute(
                """
                INSERT INTO stations (name, slug, mount, enabled, playlist_limit, notes, created_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    "Spare",
                    "spare",
                    "/spare",
                    5,
                    "Second station placeholder for multi-station work.",
                    now,
                ),
            )
            spare_id = cur.lastrowid
            for url, label in SEED_SPARE_URLS:
                conn.execute(
                    """
                    INSERT INTO shows (station_id, url, enabled, label, created_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (spare_id, url, label, now),
                )

    def list_stations(self) -> list[Station]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, COUNT(sh.id) AS show_count
                FROM stations s
                LEFT JOIN shows sh ON sh.station_id = s.id
                GROUP BY s.id
                ORDER BY s.id ASC
                """
            ).fetchall()
        return [Station.from_row(r) for r in rows]

    def get_station(self, station_id: int) -> Optional[Station]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, COUNT(sh.id) AS show_count
                FROM stations s
                LEFT JOIN shows sh ON sh.station_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (station_id,),
            ).fetchone()
        return Station.from_row(row) if row else None

    def get_station_by_slug(self, slug: str) -> Optional[Station]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, COUNT(sh.id) AS show_count
                FROM stations s
                LEFT JOIN shows sh ON sh.station_id = s.id
                WHERE s.slug = ?
                GROUP BY s.id
                """,
                (slug,),
            ).fetchone()
        return Station.from_row(row) if row else None

    def create_station(
        self,
        *,
        name: str,
        slug: str,
        mount: str = "/test",
        playlist_limit: Optional[int] = 5,
        play_duration_seconds: Optional[int] = None,
        notes: str = "",
        enabled: bool = True,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO stations (
                    name, slug, mount, enabled, playlist_limit, play_duration_seconds, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    slug,
                    mount,
                    1 if enabled else 0,
                    playlist_limit,
                    play_duration_seconds,
                    notes or None,
                    _utcnow(),
                ),
            )
            return int(cur.lastrowid)

    def update_station(
        self,
        station_id: int,
        *,
        name: str,
        slug: str,
        mount: str,
        playlist_limit: Optional[int],
        play_duration_seconds: Optional[int],
        notes: str,
        enabled: bool,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE stations
                SET name = ?, slug = ?, mount = ?, playlist_limit = ?,
                    play_duration_seconds = ?, notes = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    name,
                    slug,
                    mount,
                    playlist_limit,
                    play_duration_seconds,
                    notes or None,
                    1 if enabled else 0,
                    station_id,
                ),
            )

    def delete_station(self, station_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM stations WHERE id = ?", (station_id,))

    def list_shows(self, station_id: int, *, enabled_only: bool = False) -> list[Show]:
        sql = """
            SELECT * FROM shows
            WHERE station_id = ?
        """
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at DESC, id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, (station_id,)).fetchall()
        return [Show.from_row(r) for r in rows]

    def recent_shows(self, station_id: int, limit: Optional[int] = None) -> list[Show]:
        """Enabled shows for a station, newest first, optionally limited (last N)."""
        station = self.get_station(station_id)
        if not station:
            return []
        effective = limit if limit is not None else station.playlist_limit
        shows = self.list_shows(station_id, enabled_only=True)
        if effective is not None and effective > 0:
            return shows[:effective]
        return shows

    def get_show(self, show_id: int) -> Optional[Show]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
        return Show.from_row(row) if row else None

    def add_show(
        self,
        station_id: int,
        url: str,
        *,
        label: str = "",
        enabled: bool = True,
        created_at: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO shows (station_id, url, enabled, label, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    station_id,
                    url.strip(),
                    1 if enabled else 0,
                    label.strip() or None,
                    created_at or _utcnow(),
                ),
            )
            return int(cur.lastrowid)

    def import_urls(
        self,
        station_id: int,
        text: str,
        *,
        created_at: str | None = None,
    ) -> dict[str, int]:
        """Import one URL per line. Blank lines and # comments are ignored."""
        stamp = created_at or _utcnow()
        added = 0
        duplicates = 0
        invalid = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not (line.startswith("http://") or line.startswith("https://")):
                invalid += 1
                continue
            try:
                self.add_show(station_id, line, created_at=stamp)
                added += 1
            except sqlite3.IntegrityError:
                duplicates += 1
        return {"added": added, "duplicates": duplicates, "invalid": invalid}

    def update_show(
        self,
        show_id: int,
        *,
        url: str,
        label: str,
        enabled: bool,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE shows
                SET url = ?, label = ?, enabled = ?
                WHERE id = ?
                """,
                (url.strip(), label.strip() or None, 1 if enabled else 0, show_id),
            )

    def set_show_enabled(self, show_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE shows SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, show_id),
            )

    def delete_show(self, show_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM shows WHERE id = ?", (show_id,))

    def mark_played(self, show_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE shows
                SET play_count = play_count + 1,
                    last_played_at = ?
                WHERE id = ?
                """,
                (_utcnow(), show_id),
            )
