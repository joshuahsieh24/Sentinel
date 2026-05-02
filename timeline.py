"""
Network event timeline replay.
# Network event timeline modeled on baseline.pcap captured in Cisco Packet Tracer
# Simulation Mode. Production would consume live PCAP via scapy or a managed network tap.
"""
import json
import time
from pathlib import Path


class TimelinePlayer:
    def __init__(self, path: str | Path):
        self._events: list[dict] = json.loads(Path(path).read_text())
        self._loop_ms: int = max(e["ts_offset_ms"] for e in self._events) + 200
        self._start: float = time.monotonic()
        self._last_pos: int = -1  # last ts_offset_ms that was emitted

    def tick(self) -> list[dict]:
        """Return all events due since the last tick, handling loop wraparound."""
        elapsed = int((time.monotonic() - self._start) * 1000)
        pos = elapsed % self._loop_ms
        due: list[dict] = []

        if self._last_pos == -1:
            # First tick: emit nothing, just set cursor
            self._last_pos = pos
            return due

        if pos >= self._last_pos:
            # Normal case: emit events in (last_pos, pos]
            for evt in self._events:
                if self._last_pos < evt["ts_offset_ms"] <= pos:
                    due.append(evt)
        else:
            # Wraparound: emit events in (last_pos, loop_ms) + [0, pos]
            for evt in self._events:
                ts = evt["ts_offset_ms"]
                if ts > self._last_pos or ts <= pos:
                    due.append(evt)

        self._last_pos = pos
        return due
