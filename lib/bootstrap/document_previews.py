"""Connect document preview to the local source tree."""
from pathlib import Path

from lib.application.document_preview_service import DocumentPreviewApplication
from lib.infrastructure.document_preview_driver import FilesystemDocumentPreviewDriver


def document_preview_application(workspace_id: str = "default", root: Path | None = None) -> DocumentPreviewApplication:
    return DocumentPreviewApplication(FilesystemDocumentPreviewDriver(workspace_id, root))
