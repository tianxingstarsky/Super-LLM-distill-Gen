"""Bounded, page-aware text evidence for model-assisted document reading."""
from pathlib import Path

from lib.doc2corpus import import_text
from lib.infrastructure.document_vision import MAX_PAGES


def text_parts(path: Path):
    """Keep PDF pages separate. Empty/image-only pages must not become facts."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("document_pdf_encrypted")
            if len(reader.pages) > MAX_PAGES:
                raise ValueError("document_vision_page_limit")
            for index, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                # Blank pages are harmless; image pages need real image input.
                has_images = bool(page.images)
                contents = page.get_contents()
                has_drawing = bool(contents is not None and contents.get_data().strip())
                yield {"location": f"page:{index}", "text": text,
                       "requires_vision": not text.strip() and (has_images or has_drawing)}
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("document_pdf_parse_failed") from error
    else:
        yield {"location": "document", "text": import_text(path), "requires_vision": False}
