from __future__ import annotations

import logging
import sqlite3
from typing import Any

from flask import Flask, jsonify, request

from radio.db import Database, Show, Station
from radio.source import resolve_source

log = logging.getLogger("radio")


def register_api(app: Flask, db: Database) -> None:
    @app.get("/api")
    def api_index():
        return jsonify(
            {
                "ok": True,
                "name": "Local Radio API",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api",
                        "detail": "This index",
                    },
                    {
                        "method": "POST",
                        "path": "/api/stations/<id_or_slug>/shows",
                        "detail": "Add a URL after yt-dlp can resolve it",
                        "body": {
                            "url": "https://www.youtube.com/watch?v=…",
                            "label": "(optional) override display name",
                            "enabled": True,
                        },
                    },
                    {
                        "method": "POST",
                        "path": "/api/stations/<id_or_slug>/shows/bulk",
                        "detail": "Bulk-add URLs (no yt-dlp). Accepts urls[] or shows[].",
                        "body": {
                            "urls": ["https://…", "https://…"],
                            "shows": [{"url": "https://…", "label": "optional"}],
                        },
                    },
                    {"method": "GET", "path": "/api/stations"},
                    {"method": "GET", "path": "/api/stations/<id>/shows"},
                    {"method": "GET", "path": "/api/status"},
                    {"method": "POST", "path": "/api/player/skip", "detail": "Skip to the next show"},
                    {
                        "method": "POST",
                        "path": "/api/player/delete-current",
                        "detail": "Delete the currently playing show from the playlist (does not skip)",
                    },
                    {
                        "method": "POST",
                        "path": "/api/shows/<id>/cache",
                        "detail": "Queue a show for background caching",
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/shows/<id>/cache",
                        "detail": "Remove a cached show",
                    },
                ],
            }
        )

    @app.post("/api/stations/<station_ident>/shows")
    def api_add_show(station_ident: str):
        station = _lookup_station(db, station_ident)
        if not station:
            return jsonify({"ok": False, "detail": "station not found"}), 404

        payload = request.get_json(silent=True) or {}
        url = str(payload.get("url") or "").strip()
        label = str(payload.get("label") or "").strip()
        enabled = payload.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.lower() not in {"0", "false", "no"}

        if not url:
            return jsonify({"ok": False, "detail": "url is required"}), 400
        if not (url.startswith("http://") or url.startswith("https://")):
            return jsonify({"ok": False, "detail": "url must start with http:// or https://"}), 400

        try:
            resolved = resolve_source(url, timeout=45)
        except RuntimeError as exc:
            log.info("API rejected URL (yt-dlp): %s", exc)
            return jsonify(
                {
                    "ok": False,
                    "detail": "yt-dlp could not process this URL",
                    "error": str(exc)[:400],
                }
            ), 422

        if not label:
            if resolved.artist and resolved.title:
                label = f"{resolved.artist} — {resolved.title}"
            else:
                label = resolved.title or ""

        try:
            show_id = db.add_show(station.id, url, label=label, enabled=bool(enabled))
        except sqlite3.IntegrityError:
            return jsonify(
                {
                    "ok": False,
                    "detail": "that URL is already on this station",
                    "source": _source_payload(resolved),
                }
            ), 409

        show = db.get_show(show_id)
        log.info(
            "API added show id=%s station=%s extractor=%s url=%s",
            show_id,
            station.slug,
            resolved.extractor,
            url,
        )
        return (
            jsonify(
                {
                    "ok": True,
                    "station": _station_payload(station),
                    "show": _show_payload(show) if show else {"id": show_id, "url": url, "label": label},
                    "source": _source_payload(resolved),
                }
            ),
            201,
        )

    @app.post("/api/stations/<station_ident>/shows/bulk")
    def api_bulk_add_shows(station_ident: str):
        station = _lookup_station(db, station_ident)
        if not station:
            return jsonify({"ok": False, "detail": "station not found"}), 404

        payload = request.get_json(silent=True) or {}
        items: list[dict[str, Any]] = []

        raw_shows = payload.get("shows")
        if isinstance(raw_shows, list):
            for entry in raw_shows:
                if isinstance(entry, str):
                    items.append({"url": entry})
                elif isinstance(entry, dict):
                    items.append(
                        {
                            "url": entry.get("url") or "",
                            "label": entry.get("label") or "",
                        }
                    )
                else:
                    items.append({"url": ""})

        raw_urls = payload.get("urls")
        if isinstance(raw_urls, list):
            for entry in raw_urls:
                if isinstance(entry, str):
                    items.append({"url": entry})
                elif isinstance(entry, dict):
                    items.append(
                        {
                            "url": entry.get("url") or "",
                            "label": entry.get("label") or "",
                        }
                    )
                else:
                    items.append({"url": ""})

        if not items:
            return jsonify(
                {
                    "ok": False,
                    "detail": "provide a non-empty urls[] and/or shows[] array",
                }
            ), 400

        result = db.import_show_items(station.id, items)
        log.info(
            "API bulk add station=%s added=%s duplicates=%s invalid=%s",
            station.slug,
            result["added"],
            result["duplicates"],
            result["invalid"],
        )
        status = 201 if result["added"] else 200
        return (
            jsonify(
                {
                    "ok": True,
                    "station": _station_payload(station),
                    "added": result["added"],
                    "duplicates": result["duplicates"],
                    "invalid": result["invalid"],
                    "shows": result["shows"],
                    "errors": result["errors"],
                }
            ),
            status,
        )


def _lookup_station(db: Database, ident: str) -> Station | None:
    ident = ident.strip()
    if ident.isdigit():
        station = db.get_station(int(ident))
        if station:
            return station
    return db.get_station_by_slug(ident.lower())


def _station_payload(station: Station) -> dict[str, Any]:
    return {
        "id": station.id,
        "name": station.name,
        "slug": station.slug,
        "mount": station.mount,
    }


def _show_payload(show: Show) -> dict[str, Any]:
    return {
        "id": show.id,
        "station_id": show.station_id,
        "url": show.url,
        "label": show.label,
        "enabled": show.enabled,
        "created_at": show.created_at,
        "play_count": show.play_count,
        "cache_status": show.cache_status,
        "cache_size_bytes": show.cache_size_bytes,
        "cached_at": show.cached_at,
        "cache_error": show.cache_error,
    }


def _source_payload(resolved: Any) -> dict[str, str]:
    return {
        "artist": resolved.artist,
        "title": resolved.title,
        "extractor": resolved.extractor,
    }
