"""Streaming event inventory retaining only a bounded recent selection."""
from collections import deque
import json
from pathlib import Path


class FilesystemEventHistory:
    def __init__(self, path):
        self.path = Path(path)

    def snapshot(self, kind, limit):
        selected = deque(maxlen=limit)
        count, invalid = 0, 0
        kinds = set()
        latest_at = None
        try:
            handle = self.path.open(encoding="utf-8")
        except FileNotFoundError:
            return {"count": 0, "invalid": 0, "kinds": [], "latest_at": None, "events": []}
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if not isinstance(event, dict):
                    invalid += 1
                    continue
                count += 1
                event_kind = str(event.get("kind") or "")
                kinds.add(event_kind)
                latest_at = event.get("at")
                if kind is None or event_kind == kind:
                    selected.append(event)
        return {"count": count, "invalid": invalid, "kinds": sorted(kinds),
                "latest_at": latest_at, "events": list(reversed(selected))}
