"""Lossless structural chunking and conservative text identities; no document I/O."""
from __future__ import annotations
import hashlib
import re
from typing import List

def normalize_plain_text(text: str) -> str:
    """Conservative normalization: keep numeric lines and code indentation."""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            lines.append("")
            continue
        lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


# ── 分块层 ──────────────────────────────────────────────────────────────────
def _split_legacy_paragraphs(text: str, target_chars: int = 2000, overlap: int = 0) -> List[str]:
    """按段落边界分块，目标长度 target_chars；Markdown 标题作为硬边界。
    段落合并直到接近目标长度；overlap>0 时块间回退 N 字符（连续上下文）。
    """
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    paragraphs = [p.rstrip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    paragraph_buffer: List[str] = []
    buffer_chars = 0
    for paragraph in paragraphs:
        if len(paragraph) > target_chars:
            # A PDF extraction or code block can be one very long paragraph.
            # Keep every character in order instead of letting the workflow
            # quarantine the entire block as oversized.
            if paragraph_buffer:
                chunks.append("\n\n".join(paragraph_buffer))
                paragraph_buffer, buffer_chars = [], 0
            stride = target_chars - min(max(0, overlap), target_chars - 1)
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start + target_chars])
                if start + target_chars >= len(paragraph):
                    break
                start += stride
            continue
        is_heading = paragraph.lstrip().startswith("#")  # Markdown 标题硬边界
        if paragraph_buffer and (is_heading or buffer_chars + len(paragraph) > target_chars):
            chunks.append("\n\n".join(paragraph_buffer))
            if overlap > 0 and paragraph_buffer:
                tail = paragraph_buffer[-1][-overlap:]
                paragraph_buffer = [tail] if tail else []
                buffer_chars = len(tail)
            else:
                paragraph_buffer, buffer_chars = [], 0
        paragraph_buffer.append(paragraph)
        buffer_chars += len(paragraph)
    if paragraph_buffer:
        chunks.append("\n\n".join(paragraph_buffer))
    return chunks


_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+\S")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SENTENCE_END = re.compile(r"[。！？!?；;][\"'”’」』）)]*|(?<=[.!?])(?=\s)")


def _table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = line.strip().strip("|").split("|")
    return bool(cells) and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)


def _document_blocks(text: str):
    """Keep fences and tables whole; headings do not require surrounding blanks."""
    lines = text.splitlines(keepends=True)
    # Merged-cell tables from the image reader use standard HTML. Let the
    # Markdown parser bind complete blocks so row spans survive chunking.
    html_tables={}
    if re.search(r"<table(?:\s|>)",text,re.I):
        from markdown_it import MarkdownIt
        html_tables={token.map[0]:token.map[1] for token in MarkdownIt("commonmark").parse(text)
                     if token.type=="html_block" and token.map
                     and re.match(r"\s*<table(?:\s|>)",token.content,re.I)
                     and re.search(r"</table>\s*$",token.content,re.I)}
    paragraph, index = [], 0
    while index < len(lines):
        if index in html_tables:
            if paragraph:
                yield "".join(paragraph),"prose"
                paragraph=[]
            end=html_tables[index]
            yield "".join(lines[index:end]),"atomic"
            index=end
            continue
        line = lines[index]
        fence = _FENCE.match(line)
        table = (index + 1 < len(lines) and "|" in line
                 and _table_separator(lines[index + 1]))
        heading = bool(_HEADING.match(line))
        if fence or table or heading:
            if paragraph:
                yield "".join(paragraph), "prose"
                paragraph = []
            if heading:
                yield line, "heading"
                index += 1
                continue
            first = index
            index += 1
            if fence:
                marker = fence[1]
                close = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*$")
                while index < len(lines):
                    current = lines[index]
                    index += 1
                    if close.fullmatch(current.rstrip("\r\n")):
                        break
            else:
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    index += 1
            yield "".join(lines[first:index]), "atomic"
            continue
        paragraph.append(line)
        index += 1
        if not line.strip():
            yield "".join(paragraph), "prose"
            paragraph = []
    if paragraph:
        yield "".join(paragraph), "prose"


def _split_prose(text: str, target_chars: int, overlap: int):
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        if end < len(text):
            window = text[start:end]
            minimum = max(overlap + 1, target_chars // 2)
            # A line, then a complete sentence, then a word is preferable to
            # an arbitrary character cut. Every separator remains in the text.
            for pattern in (r"\n", _SENTENCE_END, r"[ \t]+"):
                boundary = next((match.end() for match in reversed(list(re.finditer(pattern, window)))
                                 if match.end() >= minimum), None)
                if boundary is not None:
                    end = start + boundary
                    break
        yield text[start:end]
        if end == len(text):
            break
        start = end - overlap


def split_text_into_chunks(text: str, target_chars: int = 2000, overlap: int = 0, *,
               source_processing_version: int = 2) -> List[str]:
    """Split prose at structural boundaries without discarding source characters.

    The size is a target: fenced code and Markdown tables stay intact so their
    syntax and row/header relationships survive. The workflow's existing source
    block limit still quarantines unusually large indivisible blocks. Overlap is
    confined to prose and never crosses a heading, table, or code fence.
    """
    if type(source_processing_version) is not int or source_processing_version not in {1, 2}:
        raise ValueError("invalid_source_processing_version")
    if source_processing_version == 1:
        return _split_legacy_paragraphs(text, target_chars, overlap)
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    if not text.strip():
        return []
    overlap = min(max(0, overlap), target_chars - 1)
    chunks, buffer = [], ""
    for block, kind in _document_blocks(text):
        if buffer and (kind == "heading" or len(buffer) + len(block) > target_chars):
            chunks.append(buffer)
            buffer = ""
        if kind == "atomic" and len(block) > target_chars:
            chunks.append(block)
        elif len(block) > target_chars:
            chunks.extend(_split_prose(block, target_chars, overlap))
        else:
            if not buffer and kind == "prose" and overlap and chunks:
                # Do not carry raw code/table syntax into the next prose block.
                previous = chunks[-1]
                if (len(block) + overlap <= target_chars
                        and not (_HEADING.match(previous) or re.match(r"\s*<table(?:\s|>)",previous,re.I) or any(
                            _FENCE.match(line) or _table_separator(line) for line in previous.splitlines()))):
                    buffer = previous[-overlap:]
            buffer += block
    if buffer:
        chunks.append(buffer)
    # Separators between indivisible blocks are source text, not evidence
    # units. Attach them to adjacent content without creating empty requests.
    groups, leading = [], []
    for chunk in chunks:
        if chunk.strip():
            groups.append([*leading, chunk])
            leading = []
        elif groups:
            groups[-1].append(chunk)
        else:
            leading.append(chunk)
    return ["".join(group) for group in groups]


# ── 去重层 ──────────────────────────────────────────────────────────────────
def normalized_chunk_id(chunk: str) -> str:
    normalized = re.sub(r"\s+", "", chunk)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
