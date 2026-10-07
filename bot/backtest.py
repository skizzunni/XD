from datetime import date

from .data import Calendar, checksum, code_hash, read_ticks, TRANSFORMATION_VERSION
from .engine import Engine
from .index import TickIndex


class ReplayFailure(ValueError):
    def __init__(self, message, engine):
        super().__init__(message)
        self.engine = engine


def replay(ticks_path, calendar_path, config, start=None, end=None):
    calendar = Calendar(calendar_path)
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    index = TickIndex()
    engine = Engine(config, calendar, start, end, tick_index=index)
    engine.calendar_checksum = checksum(calendar_path)
    count = 0
    try:
        for tick in read_ticks(ticks_path):
            if end and tick.timestamp.date() > end:
                break
            engine.process(tick)
            count += 1
        if not count:
            raise ValueError("No ticks executed")
        engine.finish()
    except ValueError as exc:
        engine.log(
            engine.last_tick or next(iter(calendar.sessions.values())).open,
            "run",
            "RUN_FAILED",
            detail=str(exc),
        )
        raise ReplayFailure(str(exc), engine) from exc
    finally:
        index.close()
        engine.tick_index = None
    return engine


def provenance(ticks_path, calendar_path, config):
    return {
        "code_hash": code_hash(),
        "tick_checksum": checksum(ticks_path),
        "calendar_checksum": checksum(calendar_path),
        "transformation_version": TRANSFORMATION_VERSION,
        "config": config.to_dict(),
        "ticks_path": str(ticks_path),
        "calendar_path": str(calendar_path),
    }
