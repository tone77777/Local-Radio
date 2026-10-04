from __future__ import annotations

import logging
import queue
import subprocess
import threading
from pathlib import Path

from radio.config import Config
from radio.db import Database, Show
from radio.source import resolve_source

log = logging.getLogger("radio")


class ShowCache:
    """Background, disk-backed cache for remote show audio."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.cache_dir = Path(config.cache_dir)
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._queued: set[int] = set()
        self._cancelled: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._current_show_id: int | None = None

    def start(self) -> None:
        if not self.config.cache_enabled:
            log.info("Show cache disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._reconcile()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="show-cache",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Show cache ready at %s (limit=%.1f GB)",
            self.cache_dir,
            self.config.cache_max_bytes / 1_000_000_000,
        )

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
        if self._thread:
            self._thread.join(timeout=10)
        self._thread = None

    def enqueue(self, show_id: int, *, force: bool = False) -> bool:
        if not self.config.cache_enabled or self._stop.is_set():
            return False
        show = self.db.get_show(show_id)
        if show is None:
            return False
        if not force and self.path_for(show) is not None:
            return False
        with self._lock:
            if show_id in self._queued or self._current_show_id == show_id:
                return False
            self._cancelled.discard(show_id)
            self._queued.add(show_id)
        self.db.update_show_cache(show_id, status="queued")
        self._queue.put(show_id)
        log.info("Cache queued show id=%s", show_id)
        return True

    def prefetch(self, shows: list[Show]) -> int:
        count = 0
        limit = max(0, self.config.cache_prefetch_count)
        for show in shows[:limit]:
            if self.enqueue(show.id):
                count += 1
        return count

    def path_for(self, show: Show) -> Path | None:
        if show.cache_status != "ready" or not show.cache_path:
            return None
        path = Path(show.cache_path)
        try:
            path.relative_to(self.cache_dir)
        except ValueError:
            log.warning("Ignoring cache path outside cache directory: %s", path)
            return None
        if not path.is_file():
            self.db.update_show_cache(show.id, status="uncached")
            return None
        return path

    def mark_used(self, show_id: int) -> None:
        self.db.touch_show_cache(show_id)

    def remove(self, show_id: int) -> bool:
        with self._lock:
            self._cancelled.add(show_id)
            proc = self._proc if self._current_show_id == show_id else None
        if proc and proc.poll() is None:
            proc.terminate()

        removed = False
        show = self.db.get_show(show_id)
        if show and show.cache_path:
            removed = self._safe_unlink(Path(show.cache_path)) or removed
        removed = self._remove_show_files(show_id) or removed
        if show:
            self.db.update_show_cache(show_id, status="uncached")
        log.info("Cache removed show id=%s", show_id)
        return removed

    def stats(self) -> dict[str, int | bool | str]:
        shows = self.db.cached_shows_oldest_first()
        size = sum(show.cache_size_bytes or 0 for show in shows)
        return {
            "enabled": self.config.cache_enabled,
            "directory": str(self.cache_dir),
            "ready": len(shows),
            "queued": self._queue.qsize(),
            "size_bytes": size,
            "max_bytes": self.config.cache_max_bytes,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            show_id = self._queue.get()
            if show_id is None:
                return
            with self._lock:
                self._queued.discard(show_id)
                self._current_show_id = show_id
                cancelled = show_id in self._cancelled
            try:
                show = self.db.get_show(show_id)
                if show is None or cancelled or show.cache_status != "queued":
                    continue
                self._download(show)
            except Exception as exc:
                with self._lock:
                    cancelled = show_id in self._cancelled
                if not cancelled and self.db.get_show(show_id):
                    detail = str(exc)[:500]
                    self.db.update_show_cache(show_id, status="failed", error=detail)
                    log.warning("Cache failed show id=%s: %s", show_id, detail)
            finally:
                with self._lock:
                    self._current_show_id = None
                    self._proc = None
                    self._cancelled.discard(show_id)

    def _download(self, show: Show) -> None:
        self.db.update_show_cache(show.id, status="downloading")
        log.info("Caching show id=%s url=%s", show.id, show.url)
        resolved = resolve_source(show.url)
        self._remove_show_files(show.id)

        output_template = str(self.cache_dir / f"{show.id}.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f",
            "bestaudio/best",
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--restrict-filenames",
            "-o",
            output_template,
            show.url,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        with self._lock:
            self._proc = proc
        _, stderr = proc.communicate()

        with self._lock:
            cancelled = show.id in self._cancelled
        if cancelled or self._stop.is_set():
            self._remove_show_files(show.id)
            if self.db.get_show(show.id):
                self.db.update_show_cache(show.id, status="uncached")
            return
        if proc.returncode != 0:
            raise RuntimeError((stderr or f"yt-dlp exited {proc.returncode}").strip())

        path = self._downloaded_path(show.id)
        if path is None or path.stat().st_size < 1024:
            raise RuntimeError("download completed without a valid audio file")
        if self.db.get_show(show.id) is None:
            self._safe_unlink(path)
            return

        size = path.stat().st_size
        self.db.update_show_cache(
            show.id,
            status="ready",
            path=str(path),
            size_bytes=size,
            artist=resolved.artist,
            title=resolved.title,
        )
        log.info(
            "Cache ready show id=%s size=%.1f MB path=%s",
            show.id,
            size / 1_000_000,
            path,
        )
        self._enforce_quota(protect_id=show.id)

    def _enforce_quota(self, *, protect_id: int) -> None:
        shows = self.db.cached_shows_oldest_first()
        total = sum(show.cache_size_bytes or 0 for show in shows)
        for show in shows:
            if total <= self.config.cache_max_bytes:
                break
            if show.id == protect_id:
                continue
            size = show.cache_size_bytes or 0
            self.remove(show.id)
            total -= size
            log.info("Cache evicted show id=%s to enforce storage limit", show.id)

    def _reconcile(self) -> None:
        valid_ids = self.db.all_show_ids()
        for path in self.cache_dir.iterdir():
            if path.suffix in {".part", ".ytdl"}:
                self._safe_unlink(path)
                continue
            try:
                show_id = int(path.name.split(".", 1)[0])
            except ValueError:
                continue
            if show_id not in valid_ids:
                self._safe_unlink(path)
        for show in self.db.cached_shows_oldest_first():
            self.path_for(show)
        self._enforce_quota(protect_id=-1)

    def _downloaded_path(self, show_id: int) -> Path | None:
        candidates = [
            path
            for path in self.cache_dir.glob(f"{show_id}.*")
            if path.suffix not in {".part", ".ytdl"} and path.is_file()
        ]
        return max(candidates, key=lambda path: path.stat().st_size) if candidates else None

    def _remove_show_files(self, show_id: int) -> bool:
        removed = False
        for path in self.cache_dir.glob(f"{show_id}.*"):
            removed = self._safe_unlink(path) or removed
        return removed

    def _safe_unlink(self, path: Path) -> bool:
        try:
            path.relative_to(self.cache_dir)
            path.unlink(missing_ok=True)
            return True
        except (OSError, ValueError) as exc:
            log.warning("Failed removing cache file %s: %s", path, exc)
            return False
