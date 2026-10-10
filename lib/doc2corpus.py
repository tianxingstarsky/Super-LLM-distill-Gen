"""文档 → 继续预训练（CPT）语料：完整知识无损注入层（零 LLM 成本）。

定位（两段式配方）：
  知识注入层（本模块）：文档原文清洗+分块 → {"text"} 纯文本语料，
    与 minimind/LLaMA-Factory(--stage pt) 等 CPT 流程直接对接；
  表达层（doc2data，后续批次）：基于文档的问答/指令 SFT 数据，CPT 之后使用。

原则：CPT 语料必须保留全部原始知识（清洗只做无损规范化），去重只去重复段落。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, Dict, Iterator, List

SUPPORTED_EXTS = {".md", ".txt", ".tex", ".latex", ".pdf", ".docx"}


# ── 导入层 ──────────────────────────────────────────────────────────────────
def _decode_text(data: bytes) -> str:
    """UTF-8 优先；失败再按 GB18030（中文 Windows 常见）解码。

    旧实现用 ``errors="replace"``：GBK 文档会被静默替换成 U+FFFD 乱码并写进
    "无损" CPT 语料。这里改为显式解码，两种编码都不成立时抛错而不是产出坏数据。
    """
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError("invalid_text_encoding_utf8_gb18030")


def import_text(path: str | pathlib.Path, *, source_processing_version: int = 2) -> str:
    if type(source_processing_version) is not int or source_processing_version not in {1, 2}:
        raise ValueError("invalid_source_processing_version")
    path = pathlib.Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的文件类型 {ext}（支持 {sorted(SUPPORTED_EXTS)}）")
    if ext in (".md", ".txt"):
        return _decode_text(path.read_bytes())
    if ext in (".tex", ".latex"):
        from lib.infrastructure.latex_document import parse_latex

        return parse_latex(_decode_text(path.read_bytes()))
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        import docx

        if source_processing_version >= 2:
            from lib.infrastructure.docx_document import document_text, open_document

            return document_text(open_document(path))
        # An older submitted run keeps the same chunks and checkpoint identities.
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    raise AssertionError("unreachable")


def clean_text(text: str) -> str:
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
def _legacy_chunk_text(text: str, target_chars: int = 2000, overlap: int = 0) -> List[str]:
    """按段落边界分块，目标长度 target_chars；Markdown 标题作为硬边界。
    段落合并直到接近目标长度；overlap>0 时块间回退 N 字符（连续上下文）。
    """
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    paragraphs = [p.rstrip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for para in paragraphs:
        if len(para) > target_chars:
            # A PDF extraction or code block can be one very long paragraph.
            # Keep every character in order instead of letting the workflow
            # quarantine the entire block as oversized.
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            stride = target_chars - min(max(0, overlap), target_chars - 1)
            start = 0
            while start < len(para):
                chunks.append(para[start:start + target_chars])
                if start + target_chars >= len(para):
                    break
                start += stride
            continue
        is_heading = para.lstrip().startswith("#")  # Markdown 标题硬边界
        if buf and (is_heading or buf_len + len(para) > target_chars):
            chunks.append("\n\n".join(buf))
            if overlap > 0 and buf:
                tail = buf[-1][-overlap:]
                buf = [tail] if tail else []
                buf_len = len(tail)
            else:
                buf, buf_len = [], 0
        buf.append(para)
        buf_len += len(para)
    if buf:
        chunks.append("\n\n".join(buf))
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
    paragraph, index = [], 0
    while index < len(lines):
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


def chunk_text(text: str, target_chars: int = 2000, overlap: int = 0, *,
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
        return _legacy_chunk_text(text, target_chars, overlap)
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
                        and not (_HEADING.match(previous) or any(
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
def chunk_hash(chunk: str) -> str:
    norm = re.sub(r"\s+", "", chunk)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def doc_to_corpus(
    path: str | pathlib.Path,
    target_chars: int = 2000,
    overlap: int = 0,
    manifest: set[str] | None = None,
) -> Dict[str, Any]:
    """单文档 → 语料条目列表 + 统计。manifest 为跨文档全局查重集合。"""
    manifest = manifest if manifest is not None else set()
    latex_source = pathlib.Path(path).suffix.lower() in {".tex", ".latex"}
    raw = import_text(path).strip() if latex_source else clean_text(import_text(path))
    if not raw:
        return {"entries": [], "stats": {"file": str(path), "chunks": 0, "kept": 0, "dups": 0, "chars": 0}}

    entries: List[Dict[str, str]] = []
    dups = 0
    if latex_source:
        from lib.infrastructure.latex_document import chunk_latex

        if overlap:
            raise ValueError("latex_overlap_not_supported")
        chunks = chunk_latex(raw, target_chars)
    else:
        chunks = chunk_text(raw, target_chars, overlap)
    for i, chunk in enumerate(chunks):
        # Whitespace can change verbatim/code semantics in TeX documents.
        # Preserve the existing normalized identity for all legacy formats.
        h = (hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
             if latex_source else chunk_hash(chunk))
        if h in manifest:
            dups += 1
            continue
        manifest.add(h)
        entries.append({
            "text": chunk,
            "source": pathlib.Path(path).name,
            "chunk_id": f"{pathlib.Path(path).name}#{i}",
        })
    stats = {
        "file": str(path),
        "chunks": len(entries) + dups,
        "kept": len(entries),
        "dups": dups,
        "chars": len(raw),
    }
    return {"entries": entries, "stats": stats}


def write_corpus_jsonl(entries: List[Dict[str, str]], out_path: str | pathlib.Path) -> int:
    """写 CPT 语料（minimind/LLaMA-Factory --stage pt 兼容的 {"text"} 行）。"""
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(entries)
