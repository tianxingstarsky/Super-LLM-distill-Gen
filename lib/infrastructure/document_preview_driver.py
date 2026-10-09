"""Read a bounded snapshot from the local source tree with the real document parser."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import zipfile

from lib import workspace
from lib.application.document_preview_service import (
    MAX_DOCX_EXPANDED_BYTES, MAX_PREVIEW_BYTES, MAX_PREVIEW_CHARS, MAX_PREVIEW_PAGES,
)
from lib.application.local_input_service import MAX_UPLOAD_BYTES
from lib.doc2corpus import SUPPORTED_EXTS, chunk_text, import_text
from lib.infrastructure.local_input_cache import _assert_unlinked


class FilesystemDocumentPreviewDriver:
    def __init__(self, workspace_id: str = "default", root: Path | None = None):
        self.workspace_id = workspace_id
        self.root = root

    def _resolve(self, source: str, suffixes: frozenset[str]) -> Path:
        if not isinstance(source, str) or not source:
            raise ValueError("invalid_preview_source")
        base = workspace.folder(self.workspace_id, self.root).absolute()
        path = Path(source)
        if not path.is_absolute():
            raise ValueError("preview_source_outside_cache")
        _assert_unlinked(base)
        _assert_unlinked(path)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(base.resolve(strict=True)):
            raise ValueError("preview_source_outside_cache")
        relative = resolved.relative_to(base.resolve(strict=True))
        if any(part in {".dataforge", ".git", ".venv", "node_modules", "__pycache__"}
               for part in relative.parts):
            raise ValueError("preview_source_outside_cache")
        if resolved.is_relative_to(workspace.out(self.workspace_id, self.root).resolve()):
            raise ValueError("preview_source_outside_cache")
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("invalid_preview_source")
        if resolved.suffix.lower() not in suffixes:
            raise ValueError("unsupported_preview_type")
        if info.st_size > MAX_UPLOAD_BYTES:
            raise ValueError("preview_source_upload_limit")
        return resolved

    def describe(self, source: str, suffixes: frozenset[str]) -> dict:
        path = self._resolve(source, suffixes)
        info = path.stat()
        relative = path.relative_to(workspace.folder(self.workspace_id, self.root).resolve())
        parts = relative.parts
        label = (f"{path.name} · {parts[1][:8]}"
                 if len(parts) == 3 and parts[0] == "uploads" and len(parts[1]) == 64
                 else str(relative))
        return {"path": str(path), "label": label, "name": path.name, "size": info.st_size,
                "version": (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)}

    @staticmethod
    def _preflight(path: Path) -> None:
        if path.suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ValueError("preview_encrypted_pdf")
            if len(reader.pages) > MAX_PREVIEW_PAGES:
                raise ValueError("preview_page_limit")
        elif path.suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                files = archive.infolist()
                if (len(files) > 256 or sum(item.file_size for item in files) > MAX_DOCX_EXPANDED_BYTES
                        or any(item.flag_bits & 1 for item in files)):
                    raise ValueError("preview_docx_expansion_limit")

    def preview(self, source: str, chunk_chars: int) -> dict:
        row = self.describe(source, frozenset(SUPPORTED_EXTS))
        if row["size"] > MAX_PREVIEW_BYTES:
            raise ValueError("preview_file_limit")
        path = Path(row["path"])
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                    or (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino) != row["version"]):
                raise ValueError("preview_source_changed")
            data = handle.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("preview_file_limit")
        if self.describe(source, frozenset(SUPPORTED_EXTS))["version"] != row["version"]:
            raise ValueError("preview_source_changed")
        # Parse the exact bytes read, rather than reopening a mutable source path.
        with tempfile.TemporaryDirectory(prefix="dataforge-document-preview-") as temporary:
            snapshot = Path(temporary) / ("source" + path.suffix.lower())
            snapshot.write_bytes(data)
            try:
                self._preflight(snapshot)
                text = import_text(snapshot).replace("\r\n", "\n").strip()
            except (ImportError, ModuleNotFoundError) as error:
                raise ValueError("preview_parser_unavailable") from error
            except ValueError as error:
                if str(error).startswith("preview_"):
                    raise
                if str(error) in {"latex_external_dependencies", "latex_unsupported_syntax"}:
                    raise ValueError("preview_" + str(error)) from None
                if str(error).startswith("latex_"):
                    raise ValueError("preview_latex_invalid_source") from None
                raise ValueError("preview_parse_failed") from error
            except Exception as error:
                raise ValueError("preview_parse_failed") from error
        if len(text) > MAX_PREVIEW_CHARS:
            raise ValueError("preview_text_limit")
        if path.suffix.lower() in {".tex", ".latex"}:
            from lib.infrastructure.latex_document import chunk_latex

            chunks = chunk_latex(text, chunk_chars)
        else:
            chunks = chunk_text(text, chunk_chars)
        return {**row, "sha256": hashlib.sha256(data).hexdigest(), "chunk_chars": chunk_chars,
                "characters": len(text), "chunk_count": len(chunks), "chunks": chunks,
                "signature": (row["path"], row["version"], chunk_chars)}
