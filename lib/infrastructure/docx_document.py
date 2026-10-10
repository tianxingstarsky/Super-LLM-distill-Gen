"""Read Word body blocks in their original order, including nested tables."""
from __future__ import annotations

from zipfile import BadZipFile, ZipFile


MAX_DOCX_ENTRIES = 5000
MAX_EXPANDED_DOCX_BYTES = 100 * 1024 * 1024


def open_document(path, *, max_expanded_bytes=None):
    """Bound archive expansion before the document library reads its XML parts."""
    import docx
    from docx.opc.exceptions import PackageNotFoundError
    from lxml.etree import XMLSyntaxError

    max_expanded_bytes = MAX_EXPANDED_DOCX_BYTES if max_expanded_bytes is None else max_expanded_bytes
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if (len(entries) > MAX_DOCX_ENTRIES
                    or sum(item.file_size for item in entries) > max_expanded_bytes):
                raise ValueError("document_docx_expansion_limit")
            if any(item.flag_bits & 1 for item in entries):
                raise ValueError("document_docx_parse_failed")
    except BadZipFile as error:
        raise ValueError("document_docx_parse_failed") from error
    # These are document-package failures from this single library call.
    # Errors in our subsequent processing are not swallowed as corrupt input.
    try:
        document = docx.Document(path)
    except (BadZipFile, PackageNotFoundError, XMLSyntaxError, KeyError, ValueError) as error:
        raise ValueError("document_docx_parse_failed") from error
    from docx.oxml.ns import qn

    if document.element.tag != qn("w:document") or document.element.body is None:
        raise ValueError("document_docx_parse_failed")
    return document


def block_text(block, parent) -> str:
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if block.tag == qn("w:p"):
        return Paragraph(block, parent).text
    if block.tag == qn("w:tbl"):
        return "\n".join("\t".join(
            "\n".join(block_text(child, cell) for child in cell._tc
                      if child.tag in {qn("w:p"), qn("w:tbl")})
            for cell in row.cells) for row in Table(block, parent).rows)
    return ""


def document_text(document) -> str:
    from docx.oxml.ns import qn

    return "\n".join(block_text(block, document) for block in document.element.body
                     if block.tag in {qn("w:p"), qn("w:tbl")})
