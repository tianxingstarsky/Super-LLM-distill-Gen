"""Preview the promo on loopback and optionally save its browser recording.

Run ``python scripts/promo/serve.py --record`` and visit the printed URL.
Only files beneath assets/promo are served; recording always uses one fixed name.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


PROMO_ROOT = Path(__file__).resolve().parents[2] / "assets" / "promo"
RECORDING_NAME = "shujian-cube-promo.webm"
MAX_RECORDING_BYTES = 150 * 1024 * 1024
_WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"
_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}


class PromoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, root: Path, allow_record: bool = False):
        self.promo_root = root.resolve(strict=True)
        if not self.promo_root.is_dir():
            raise ValueError("Promo asset root must be a directory")
        self.allow_record = allow_record
        self.record_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), PromoHandler)
        self.expected_host = f"127.0.0.1:{self.server_port}"
        self.expected_origin = f"http://{self.expected_host}"


class PromoHandler(BaseHTTPRequestHandler):
    server: PromoServer
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def _one_header(self, name: str) -> str | None:
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def _trusted_host(self) -> bool:
        if self._one_header("Host") != self.server.expected_host:
            self._json(403, {"error": "Use the printed 127.0.0.1 preview URL"})
            return False
        return True

    def _headers(self, status: int, content_type: str, length: int):
        # Closing each response also avoids parsing a rejected upload as a new request.
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, status: int, payload: dict, *, content_range: str | None = None):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        if content_range:
            self.send_header("Content-Range", content_range)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _asset_path(self) -> Path | None:
        try:
            target = urlsplit(self.path)
            if target.scheme or target.netloc or target.fragment:
                return None
            path = unquote(target.path, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeError):
            return None
        if not path.startswith("/") or "\\" in path or ":" in path or "\x00" in path:
            return None
        parts = path[1:].split("/") if path != "/" else ["index.html"]
        if any(not part or part.startswith(".") for part in parts):
            return None
        candidate = self.server.promo_root.joinpath(*parts)
        if candidate.suffix.lower() not in _MIME_TYPES:
            return None
        # Path.resolve alone would allow a symlink to another file inside the root.
        current = self.server.promo_root
        for part in parts:
            current /= part
            if current.is_symlink():
                return None
        try:
            candidate.resolve(strict=True).relative_to(self.server.promo_root)
        except (OSError, ValueError, RuntimeError):
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _byte_range(value: str, size: int) -> tuple[int, int]:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match or size == 0 or not any(match.groups()):
            raise ValueError("Unsupported or empty byte range")
        left, right = match.groups()
        if not left:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError("Empty suffix range")
            return max(0, size - suffix), size - 1
        start = int(left)
        end = min(int(right), size - 1) if right else size - 1
        if start >= size or end < start:
            raise ValueError("Range outside file")
        return start, end

    def _serve_asset(self):
        if not self._trusted_host():
            return
        asset = self._asset_path()
        if asset is None:
            self._json(404, {"error": "Promo asset not found"})
            return
        headers_sent = False
        try:
            with asset.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                start, end = 0, size - 1
                range_value = self._one_header("Range")
                if self.headers.get_all("Range", []) and range_value is None:
                    self._json(416, {"error": "One byte range is supported"},
                               content_range=f"bytes */{size}")
                    return
                if range_value:
                    try:
                        start, end = self._byte_range(range_value, size)
                    except ValueError:
                        self._json(416, {"error": "Byte range unavailable"},
                                   content_range=f"bytes */{size}")
                        return
                length = max(0, end - start + 1)
                self._headers(206 if range_value else 200, _MIME_TYPES[asset.suffix.lower()], length)
                self.send_header("Accept-Ranges", "bytes")
                if range_value:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                headers_sent = True
                if self.command == "HEAD":
                    return
                source.seek(start)
                while length:
                    chunk = source.read(min(length, 64 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except OSError:
            # A disappeared asset before headers are sent is an ordinary missing file.
            if not headers_sent:
                self._json(404, {"error": "Promo asset unavailable"})

    def do_GET(self):
        self._serve_asset()

    def do_HEAD(self):
        self._serve_asset()

    def do_POST(self):
        if not self._trusted_host():
            return
        if self.path != "/__recording":
            self._json(404, {"error": "Unknown recording endpoint"})
            return
        if not self.server.allow_record:
            self._json(403, {"error": "Recording save is disabled; start with --record or download it"})
            return
        if self._one_header("Origin") != self.server.expected_origin:
            self._json(403, {"error": "Recording must come from this preview page"})
            return
        content_type = self._one_header("Content-Type")
        if not content_type or content_type.split(";", 1)[0].strip().lower() != "video/webm":
            self._json(415, {"error": "A video/webm recording is required"})
            return
        if self.headers.get_all("Transfer-Encoding", []):
            self._json(400, {"error": "Use a fixed Content-Length"})
            return
        raw_length = self._one_header("Content-Length")
        if raw_length is None or not re.fullmatch(r"[0-9]+", raw_length):
            self._json(411, {"error": "A single Content-Length is required"})
            return
        # Avoid parsing arbitrarily long attacker-supplied decimal strings.
        if len(raw_length) > 12 or int(raw_length) > MAX_RECORDING_BYTES:
            self._json(413, {"error": "Recording exceeds the 150 MiB limit"})
            return
        length = int(raw_length)
        if length < len(_WEBM_SIGNATURE):
            self._json(400, {"error": "Recording is empty or incomplete"})
            return
        if not self.server.record_lock.acquire(blocking=False):
            self._json(409, {"error": "A recording is already being saved"})
            return
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".recording-", suffix=".upload",
                                             dir=self.server.promo_root, delete=False) as destination:
                temporary = Path(destination.name)
                remaining = length
                first_chunk = True
                while remaining:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise ValueError("Recording upload ended early")
                    if first_chunk:
                        if not chunk.startswith(_WEBM_SIGNATURE):
                            raise ValueError("Recording is not a WebM container")
                        first_chunk = False
                    destination.write(chunk)
                    remaining -= len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self.server.promo_root / RECORDING_NAME)
            temporary = None
            self._json(200, {"saved": True, "url": RECORDING_NAME})
        except (ValueError, socket.timeout) as error:
            self._json(400, {"error": str(error)})
        except OSError:
            self._json(500, {"error": "Unable to save the recording"})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.server.record_lock.release()

    def log_message(self, format: str, *args):
        print(f"[promo] {self.address_string()} {format % args}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766, help="Loopback port (default: 8766)")
    parser.add_argument("--record", action="store_true", help="Enable saving the fixed WebM recording")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        server = PromoServer(args.port, PROMO_ROOT, args.record)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Unable to start promo preview: {error}\n")
    print(f"Promo preview: {server.expected_origin}/", flush=True)
    print(f"Recording save: {'enabled' if args.record else 'disabled (use --record to enable)'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
