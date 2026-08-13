from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("radio")


@dataclass
class ResolvedSource:
    url: str
    artist: str
    title: str
    extractor: str = ""


def resolve_source(url: str, timeout: float = 60.0) -> ResolvedSource:
    """Use yt-dlp to resolve title/artist without downloading media."""
    cmd = [
        "yt-dlp",
        "-j",
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    log.info("Resolving source metadata for %s", url)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"yt-dlp metadata timed out for {url}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "yt-dlp failed").strip()
        raise RuntimeError(f"yt-dlp metadata failed: {err[:400]}")

    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError("yt-dlp returned no metadata")
    data = json.loads(line[0])
    artist = (
        data.get("artist")
        or data.get("uploader")
        or data.get("channel")
        or data.get("creator")
        or "Unknown"
    )
    title = data.get("track") or data.get("title") or "Unknown"
    extractor = str(data.get("extractor") or data.get("extractor_key") or "")
    return ResolvedSource(url=url, artist=str(artist), title=str(title), extractor=extractor)


def build_feed_command(
    url: str,
    duration_seconds: Optional[int] = None,
) -> list[list[str]]:
    """Return [yt-dlp_cmd, ffmpeg_decode_cmd] writing PCM to stdout (pipe:1)."""
    ytdlp = [
        "yt-dlp",
        "-f",
        "bestaudio/best",
        "-o",
        "-",
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    ffmpeg = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-re",
        "-i",
        "pipe:0",
        "-vn",
    ]
    if duration_seconds and duration_seconds > 0:
        ffmpeg.extend(["-t", str(duration_seconds)])
    ffmpeg.extend(
        [
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "pipe:1",
        ]
    )
    return [ytdlp, ffmpeg]
