"""Bounded JSON token reader for one-time migration of large review histories."""
from __future__ import annotations

import json


class JSONStream:
    def __init__(self, handle, *, chunk_size=65536, max_value_chars=16_000_000):
        self.handle, self.chunk_size, self.max_value_chars = handle, chunk_size, max_value_chars
        self.buffer, self.eof = "", False
        self.decoder = json.JSONDecoder()

    def _refill(self):
        chunk = self.handle.read(self.chunk_size)
        self.buffer += chunk
        self.eof = not chunk
        if len(self.buffer) > self.max_value_chars:
            raise ValueError("review_record_too_large")

    def _space(self):
        while True:
            self.buffer = self.buffer.lstrip(" \n\r\t")
            if self.buffer or self.eof:
                return
            self._refill()

    def _take(self, token):
        self._space()
        if not self.buffer.startswith(token):
            raise ValueError("invalid_review_json")
        self.buffer = self.buffer[len(token):]

    def value(self):
        self._space()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer)
                if end == len(self.buffer) and not self.eof:
                    self._refill()
                    continue
                self.buffer = self.buffer[end:]
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("invalid_review_json") from None
                self._refill()

    def keys(self):
        """Yield object keys. The caller must consume each associated value."""
        self._take("{")
        self._space()
        if self.buffer.startswith("}"):
            self._take("}")
            return
        seen = set()
        while True:
            key = self.value()
            if not isinstance(key, str) or key in seen:
                raise ValueError("invalid_review_json")
            seen.add(key)
            self._take(":")
            yield key
            self._space()
            if self.buffer.startswith("}"):
                self._take("}")
                return
            self._take(",")

    def array(self):
        self._take("[")
        self._space()
        if self.buffer.startswith("]"):
            self._take("]")
            return
        while True:
            yield self.value()
            self._space()
            if self.buffer.startswith("]"):
                self._take("]")
                return
            self._take(",")

    def finish(self):
        self._space()
        if self.buffer:
            raise ValueError("invalid_review_json")
