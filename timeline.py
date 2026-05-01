import json
import time
from pathlib import Path
class TimelinePlayer:
    def __init__(self, path: str | Path):
        self._events: list[dict] = json.loads(Path(path).read_text())
        self._loop_ms: int = max(e["ts_offset_ms"] for e in self._events) + 200
        self._start: float = time.monotonic()
        self._last_pos: int = -1
    def tick(self) -> list[dict]:
        elapsed = int((time.monotonic() - self._start) * 1000)
        pos = elapsed % self._loop_ms
        due: list[dict] = []
        if self._last_pos == -1:
            self._last_pos = pos
            return due
        if pos >= self._last_pos:
            for evt in self._events:
                if self._last_pos < evt["ts_offset_ms"] <= pos:
                    due.append(evt)
        else:
            for evt in self._events:
                ts = evt["ts_offset_ms"]
                if ts > self._last_pos or ts <= pos:
                    due.append(evt)
        self._last_pos = pos
        return due
