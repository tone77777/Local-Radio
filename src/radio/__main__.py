from __future__ import annotations

import logging
import signal
import sys
from typing import Optional, Union

from radio.config import Config
from radio.db import Database
from radio.logging_setup import setup_logging
from radio.player import RadioPlayer
from radio.test_stream import TestStream
from radio.web.app import create_app

log = logging.getLogger("radio")

Engine = Optional[Union[RadioPlayer, TestStream]]


def main() -> int:
    config = Config.from_env()
    setup_logging(config)
    log.info(
        "Local Radio starting (web=%s:%s log_level=%s playback=%s test_tone=%s station=%s)",
        config.web_host,
        config.web_port,
        config.log_level,
        config.playback_enabled,
        config.enable_test_stream,
        config.station_slug,
    )

    db = Database(config.database_path)
    db.init()
    stations = db.list_stations()
    log.info("Database ready at %s (%s station(s))", config.database_path, len(stations))

    engine: Engine = None
    player: Optional[RadioPlayer] = None

    if config.playback_enabled:
        if config.enable_test_stream:
            log.warning("PLAYBACK_ENABLED=1 takes precedence; test tone disabled")
        player = RadioPlayer(config, db)
        player.start()
        engine = player
    elif config.enable_test_stream:
        tone = TestStream(config)
        tone.start()
        engine = tone
    else:
        log.info("No audio engine enabled (set PLAYBACK_ENABLED=1 or ENABLE_TEST_STREAM=1)")

    app = create_app(config, db, player=player)

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("Signal %s received, shutting down", signum)
        if engine:
            engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    app.run(host=config.web_host, port=config.web_port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
