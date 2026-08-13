# Local Radio

Self-hosted Mixcloud/YouTube radio via yt-dlp → FFmpeg → Icecast, with a small Flask status UI.

## Quick start

```bash
cp .env.example .env
docker compose up --build -d
```

| What | URL |
|------|-----|
| Status page | http://127.0.0.1:8080/ |
| Stations (editable) | http://127.0.0.1:8080/stations |
| Logs | http://127.0.0.1:8080/logs |
| JSON API | http://127.0.0.1:8080/api/status |
| Icecast stream | http://127.0.0.1:18080/test |
| Icecast status | http://127.0.0.1:18080/status.xsl |

On the NAS, set `ICECAST_PUBLIC_URL=http://192.168.1.119:18080` in `.env`.

Logging uses `LOG_LEVEL` (`DEBUG` / `INFO` / `WARNING` / `ERROR`) and rotates so on-disk logs stay near `LOG_MAX_BYTES` (default 2 MB).

Playlists live in SQLite (`DATABASE_PATH`, default `/data/radio.db`) with multiple stations. Each station can set its own clip length (blank = full show) and limit playback to the newest N shows.

## Milestone 0 (current)

- Icecast on host port **18080**
- Playlist playback from SQLite (`STATION_SLUG`, default `test`)
- Persistent FFmpeg encoder into Icecast (avoids dropping listeners between shows)
- yt-dlp → decode → encoder FIFO → Icecast
- Metadata updates on each show change
- Per-station clip length and skip/reload on the station page
- Flask status / stations / logs UI

`PLAY_DURATION_SECONDS` in `.env` is only a fallback when a station leaves clip length blank.

## Layout

- `icecast/` — Icecast image + config template
- `src/radio/` — Python app (player + web)
- `data/mixes.txt` — playlist (later)
- `logs/` — rotated app logs
