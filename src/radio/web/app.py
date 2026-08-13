from __future__ import annotations

import logging
import random
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from radio.config import Config
from radio.db import Database
from radio.logging_setup import clear_logs, filter_log_lines, read_recent_log
from radio.metadata import IcecastMetadata
from radio.player import RadioPlayer
from radio.state import ShowInfo, state

log = logging.getLogger("radio")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _format_added(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def create_app(config: Config, db: Database, player: RadioPlayer | None = None) -> Flask:
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    app = Flask(__name__, template_folder=str(template_dir), static_folder=str(static_dir))
    app.config["RADIO_CONFIG"] = config
    app.secret_key = config.icecast_admin_password or "local-radio-dev"
    app.jinja_env.filters["date_added"] = _format_added
    metadata = IcecastMetadata(config)

    @app.get("/")
    def index():
        snap = state.snapshot()
        stations = db.list_stations()
        return render_template(
            "index.html",
            state=snap,
            config=config,
            stream_url=config.stream_url,
            icecast_status_url=config.icecast_status_url,
            log_level=config.log_level,
            stations=stations,
            player_enabled=player is not None,
            play_duration_seconds=config.play_duration_seconds,
            station_slug=config.station_slug,
        )

    @app.get("/stations")
    def stations_index():
        stations = db.list_stations()
        playback = {s.slug: state.station_snapshot(s.slug) for s in stations}
        return render_template(
            "stations.html",
            stations=stations,
            playback=playback,
            active_station_slug=config.station_slug if player else None,
        )

    @app.post("/stations")
    def stations_create():
        name = (request.form.get("name") or "").strip()
        slug = (request.form.get("slug") or "").strip().lower()
        mount = (request.form.get("mount") or "").strip() or f"/{slug}"
        notes = (request.form.get("notes") or "").strip()
        limit_raw = (request.form.get("playlist_limit") or "").strip()
        playlist_limit = int(limit_raw) if limit_raw.isdigit() else None
        if not name or not _SLUG_RE.match(slug):
            flash("Name and a simple slug (e.g. test-2) are required.", "error")
            return redirect(url_for("stations_index"))
        if not mount.startswith("/"):
            mount = "/" + mount
        try:
            station_id = db.create_station(
                name=name,
                slug=slug,
                mount=mount,
                playlist_limit=playlist_limit,
                notes=notes,
            )
        except sqlite3.IntegrityError:
            flash("That slug already exists.", "error")
            return redirect(url_for("stations_index"))
        flash("Station created.", "ok")
        return redirect(url_for("station_detail", station_id=station_id))

    @app.get("/stations/<int:station_id>")
    def station_detail(station_id: int):
        station = db.get_station(station_id)
        if not station:
            flash("Station not found.", "error")
            return redirect(url_for("stations_index"))
        shows = db.list_shows(station_id)
        recent = db.recent_shows(station_id)
        station_state = state.station_snapshot(station.slug)
        is_active = bool(player) and station.slug == config.station_slug
        public = config.icecast_public_url.rstrip("/")
        return render_template(
            "station_detail.html",
            station=station,
            shows=shows,
            recent=recent,
            station_state=station_state,
            is_active=is_active,
            active_station_slug=config.station_slug if player else None,
            player_enabled=player is not None,
            stream_url=f"{public}{station.mount}",
            icecast_status_url=config.icecast_status_url,
        )

    @app.post("/stations/<int:station_id>")
    def station_update(station_id: int):
        station = db.get_station(station_id)
        if not station:
            flash("Station not found.", "error")
            return redirect(url_for("stations_index"))
        name = (request.form.get("name") or "").strip()
        slug = (request.form.get("slug") or "").strip().lower()
        mount = (request.form.get("mount") or "").strip() or station.mount
        notes = (request.form.get("notes") or "").strip()
        limit_raw = (request.form.get("playlist_limit") or "").strip()
        playlist_limit = int(limit_raw) if limit_raw.isdigit() else None
        enabled = request.form.get("enabled") == "1"
        if not name or not _SLUG_RE.match(slug):
            flash("Name and a valid slug are required.", "error")
            return redirect(url_for("station_detail", station_id=station_id))
        if not mount.startswith("/"):
            mount = "/" + mount
        try:
            db.update_station(
                station_id,
                name=name,
                slug=slug,
                mount=mount,
                playlist_limit=playlist_limit,
                notes=notes,
                enabled=enabled,
            )
        except sqlite3.IntegrityError:
            flash("That slug already exists.", "error")
            return redirect(url_for("station_detail", station_id=station_id))
        flash("Station saved.", "ok")
        return redirect(url_for("station_detail", station_id=station_id))

    @app.post("/stations/<int:station_id>/delete")
    def station_delete(station_id: int):
        station = db.get_station(station_id)
        if not station:
            flash("Station not found.", "error")
            return redirect(url_for("stations_index"))
        if station.slug == "test":
            flash("The test station is protected and cannot be deleted.", "error")
            return redirect(url_for("station_detail", station_id=station_id))
        db.delete_station(station_id)
        flash("Station deleted.", "ok")
        return redirect(url_for("stations_index"))

    @app.post("/stations/<int:station_id>/shows")
    def show_create(station_id: int):
        if not db.get_station(station_id):
            flash("Station not found.", "error")
            return redirect(url_for("stations_index"))
        url = (request.form.get("url") or "").strip()
        label = (request.form.get("label") or "").strip()
        if not url:
            flash("URL is required.", "error")
            return redirect(url_for("station_detail", station_id=station_id))
        try:
            db.add_show(station_id, url, label=label)
        except sqlite3.IntegrityError:
            flash("That URL is already on this station.", "error")
            return redirect(url_for("station_detail", station_id=station_id))
        flash("Show added.", "ok")
        return redirect(url_for("station_detail", station_id=station_id))

    @app.post("/stations/<int:station_id>/import")
    def shows_import(station_id: int):
        if not db.get_station(station_id):
            flash("Station not found.", "error")
            return redirect(url_for("stations_index"))
        text = request.form.get("urls") or ""
        # Stamp all imported rows with "now" so they share today's date added.
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        result = db.import_urls(station_id, text, created_at=stamp)
        flash(
            f"Import: {result['added']} added, {result['duplicates']} duplicates, {result['invalid']} invalid.",
            "ok" if result["added"] else "error",
        )
        return redirect(url_for("station_detail", station_id=station_id))

    @app.post("/shows/<int:show_id>/update")
    def show_update(show_id: int):
        show = db.get_show(show_id)
        if not show:
            flash("Show not found.", "error")
            return redirect(url_for("stations_index"))
        url = (request.form.get("url") or "").strip()
        label = (request.form.get("label") or "").strip()
        enabled = request.form.get("enabled") == "1"
        if not url:
            flash("URL is required.", "error")
            return redirect(url_for("station_detail", station_id=show.station_id))
        try:
            db.update_show(show_id, url=url, label=label, enabled=enabled)
        except sqlite3.IntegrityError:
            flash("That URL is already on this station.", "error")
            return redirect(url_for("station_detail", station_id=show.station_id))
        flash("Show saved.", "ok")
        return redirect(url_for("station_detail", station_id=show.station_id))

    @app.post("/shows/<int:show_id>/toggle")
    def show_toggle(show_id: int):
        show = db.get_show(show_id)
        if not show:
            flash("Show not found.", "error")
            return redirect(url_for("stations_index"))
        db.set_show_enabled(show_id, not show.enabled)
        return redirect(url_for("station_detail", station_id=show.station_id))

    @app.post("/shows/<int:show_id>/delete")
    def show_delete(show_id: int):
        show = db.get_show(show_id)
        if not show:
            flash("Show not found.", "error")
            return redirect(url_for("stations_index"))
        station_id = show.station_id
        db.delete_show(show_id)
        flash("Show deleted.", "ok")
        return redirect(url_for("station_detail", station_id=station_id))

    @app.get("/api/stations")
    def api_stations():
        return jsonify(
            [
                {
                    "id": s.id,
                    "name": s.name,
                    "slug": s.slug,
                    "mount": s.mount,
                    "enabled": s.enabled,
                    "playlist_limit": s.playlist_limit,
                    "show_count": s.show_count,
                    "notes": s.notes,
                }
                for s in db.list_stations()
            ]
        )

    @app.get("/api/stations/<int:station_id>/shows")
    def api_station_shows(station_id: int):
        station = db.get_station(station_id)
        if not station:
            return jsonify({"ok": False, "detail": "not found"}), 404
        recent_only = request.args.get("recent") == "1"
        shows = db.recent_shows(station_id) if recent_only else db.list_shows(station_id)
        return jsonify(
            {
                "station": {
                    "id": station.id,
                    "name": station.name,
                    "slug": station.slug,
                    "playlist_limit": station.playlist_limit,
                },
                "shows": [
                    {
                        "id": sh.id,
                        "url": sh.url,
                        "label": sh.label,
                        "enabled": sh.enabled,
                        "created_at": sh.created_at,
                        "last_played_at": sh.last_played_at,
                        "play_count": sh.play_count,
                    }
                    for sh in shows
                ],
            }
        )

    @app.get("/logs")
    def logs_page():
        level = (request.args.get("level") or "DEBUG").upper()
        raw = read_recent_log(config)
        text = filter_log_lines(raw, min_level=level) if level != "DEBUG" else raw
        return render_template(
            "logs.html",
            log_text=text or "(log is empty)",
            selected_level=level,
            config=config,
            log_level=config.log_level,
            auto_refresh=request.args.get("refresh", "1") != "0",
        )

    @app.post("/logs/clear")
    def logs_clear():
        clear_logs(config)
        level = (request.form.get("level") or "DEBUG").upper()
        refresh = request.form.get("refresh", "1")
        return redirect(url_for("logs_page", level=level, refresh=refresh))

    @app.post("/api/log/clear")
    def api_log_clear():
        clear_logs(config)
        return jsonify({"ok": True, "message": "Log cleared"})

    @app.post("/actions/test-now-playing")
    def action_test_now_playing():
        _push_random_now_playing(metadata)
        station_id = request.form.get("station_id")
        if station_id and station_id.isdigit():
            return redirect(url_for("station_detail", station_id=int(station_id)))
        return redirect(url_for("index"))

    @app.post("/actions/skip")
    def action_skip():
        station_id = request.form.get("station_id")
        redirect_target = (
            url_for("station_detail", station_id=int(station_id))
            if station_id and station_id.isdigit()
            else url_for("index")
        )
        if not player:
            flash("Playback engine is not running.", "error")
            return redirect(redirect_target)
        player.skip()
        flash("Skip requested.", "ok")
        return redirect(redirect_target)

    @app.post("/actions/reload-playlist")
    def action_reload_playlist():
        station_id = request.form.get("station_id")
        redirect_target = (
            url_for("station_detail", station_id=int(station_id))
            if station_id and station_id.isdigit()
            else url_for("index")
        )
        if not player:
            flash("Playback engine is not running.", "error")
            return redirect(redirect_target)
        if station_id and station_id.isdigit():
            station = db.get_station(int(station_id))
            if not station:
                flash("Station not found.", "error")
                return redirect(url_for("stations_index"))
            if station.slug != config.station_slug:
                flash(
                    f"Reload only applies to the on-air station ({config.station_slug}).",
                    "error",
                )
                return redirect(redirect_target)
        player.reload_playlist()
        flash("Playlist reload requested — loading latest shows from the database.", "ok")
        return redirect(redirect_target)

    @app.post("/api/player/skip")
    def api_player_skip():
        if not player:
            return jsonify({"ok": False, "detail": "playback engine not running"}), 400
        player.skip()
        return jsonify({"ok": True, "detail": "skip requested"})

    @app.post("/api/player/reload")
    def api_player_reload():
        if not player:
            return jsonify({"ok": False, "detail": "playback engine not running"}), 400
        player.reload_playlist()
        return jsonify({"ok": True, "detail": "playlist reload requested"})

    @app.post("/api/metadata/test")
    def api_metadata_test():
        result = _push_random_now_playing(metadata)
        return jsonify(
            {
                "ok": result.ok,
                "artist": result.artist,
                "title": result.title,
                "song": result.song,
                "detail": result.detail,
                "mount_ready": result.mount_ready,
            }
        ), (200 if result.ok else 502)

    @app.post("/api/metadata")
    def api_metadata():
        payload = request.get_json(silent=True) or {}
        artist = str(payload.get("artist") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not artist or not title:
            return jsonify({"ok": False, "detail": "artist and title are required"}), 400
        result = _apply_metadata(metadata, artist, title, url=str(payload.get("url") or "test://manual"))
        return jsonify(
            {
                "ok": result.ok,
                "artist": result.artist,
                "title": result.title,
                "song": result.song,
                "detail": result.detail,
                "mount_ready": result.mount_ready,
            }
        ), (200 if result.ok else 502)

    @app.get("/api/status")
    def api_status():
        snap = state.snapshot()
        mount_live = metadata.mount_exists()
        snap["icecast_connected"] = mount_live
        snap["log_level"] = config.log_level
        snap["station_count"] = len(db.list_stations())
        snap["links"] = {
            "stream": config.stream_url,
            "icecast_status": config.icecast_status_url,
            "logs": "/logs",
            "stations": "/stations",
            "api_log": "/api/log",
        }
        return jsonify(snap)

    @app.get("/api/log")
    def api_log():
        level = (request.args.get("level") or "DEBUG").upper()
        lines = request.args.get("lines")
        raw = read_recent_log(config)
        text = filter_log_lines(raw, min_level=level) if level != "DEBUG" else raw
        if lines:
            try:
                n = max(1, int(lines))
                text = "\n".join(text.splitlines()[-n:])
            except ValueError:
                pass
        return jsonify(
            {
                "log": text,
                "level_filter": level,
                "configured_level": config.log_level,
                "max_bytes": config.log_max_bytes,
            }
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app


def _push_random_now_playing(metadata: IcecastMetadata):
    artist = f"Test Artist {random.randint(1000, 9999)}"
    title = f"Test Track {random.randint(1000, 9999)}"
    return _apply_metadata(metadata, artist, title, url="test://random-metadata")


def _apply_metadata(metadata: IcecastMetadata, artist: str, title: str, *, url: str):
    result = metadata.update_now_playing(artist, title, wait=True)
    state.update(
        current_show=ShowInfo(artist=artist, title=title, url=url),
        metadata_ok=result.ok,
        metadata_detail=result.detail,
        icecast_connected=result.mount_ready,
        last_error=None if result.ok else result.detail,
    )
    return result
