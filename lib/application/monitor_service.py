"""Command monitoring use case independent of storage and UI."""
from lib.application.monitor_ports import EventHistoryPort


class MonitorApplication:
    def __init__(self, port: EventHistoryPort):
        self._port = port

    def snapshot(self, kind=None, limit=100):
        return self._port.snapshot(kind, min(100, max(1, int(limit))))
