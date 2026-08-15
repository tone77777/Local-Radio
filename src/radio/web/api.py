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
                    {"method": "GET", "path": "/api/stations"},
                    {"method": "GET", "path": "/api/stations/<id>/shows"},
                    {"method": "GET", "path": "/api/status"},
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
    }


def _source_payload(resolved: Any) -> dict[str, str]:
    return {
        "artist": resolved.artist,
        "title": resolved.title,
        "extractor": resolved.extractor,
    }
