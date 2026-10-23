"""Conservative, non-executing extraction of a self-contained TeX source.

Math, tables, macro definitions and unknown commands remain TeX source.  This is
not a TeX interpreter: no compiler, shell, network or file include is invoked.
Dependencies that could make an extracted document incomplete are explicit
errors, rather than silently producing a partial corpus.
"""
from __future__ import annotations

import re
from bisect import bisect_right


LATEX_EXTENSIONS = frozenset({".tex", ".latex"})
_LITERAL_ENVIRONMENTS = frozenset({"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted"})
_EXTERNAL_COMMANDS = frozenset({
    "input", "include", "includegraphics", "bibliography", "addbibresource",
    "lstinputlisting", "verbatiminput", "import", "subimport", "inputminted",
    "includepdf", "externaldocument", "openin", "openout", "read", "write",
    "directlua", "directgdef", "directedef", "luaexec", "scantokens",
})
_UNSUPPORTED_SYNTAX = frozenset({"catcode", "endlinechar", "escapechar"})
_LAYOUT_ARGUMENTS = {
    "documentclass": 1, "usepackage": 1, "RequirePackage": 1,
    "geometry": 1, "pagestyle": 1, "thispagestyle": 1,
    "fancyhead": 1, "fancyfoot": 1, "fancyhf": 1, "lhead": 1, "chead": 1, "rhead": 1,
    "lfoot": 1, "cfoot": 1, "rfoot": 1,
    "setlength": 2, "addtolength": 2,
    "newlength": 1, "pagenumbering": 1,
    "vspace": 1, "hspace": 1, "linespread": 1,
}
_LAYOUT_NO_ARGUMENTS = frozenset({
    "newpage", "clearpage", "cleardoublepage", "pagebreak", "nopagebreak",
    "smallskip", "medskip", "bigskip", "noindent", "centering", "raggedright",
})
_HEADINGS = {"part": 1, "chapter": 1, "section": 2, "subsection": 3,
             "subsubsection": 4, "paragraph": 5, "subparagraph": 6}
_DEFINITION_COMMANDS = frozenset({"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
                                 "DeclareMathOperator", "newenvironment", "renewenvironment", "provideenvironment"})
_PROTECTED_ENVIRONMENTS = _LITERAL_ENVIRONMENTS | frozenset({
    "equation", "equation*", "align", "align*", "aligned", "alignat", "alignat*",
    "gather", "gather*", "gathered", "multline", "multline*", "flalign", "flalign*",
    "eqnarray", "eqnarray*", "displaymath", "math", "split", "array",
    "tabular", "tabular*", "tabularx", "longtable", "table", "table*", "figure", "figure*",
    "cases", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix",
    "tikzpicture", "picture",
})


def _command(source: str, index: int) -> tuple[str, int]:
    """Read one control sequence; an escaped slash never starts another one."""
    start = index + 1
    end = start
    while end < len(source) and (source[end].isalpha() or source[end] == "@"):
        end += 1
    if end == start:
        end = min(len(source), start + 1)
    return source[start:end], end


def _space(source: str, index: int) -> int:
    while index < len(source) and source[index].isspace():
        index += 1
    return index


def _group(source: str, index: int, opening="{", closing="}") -> tuple[str, int] | None:
    index = _space(source, index)
    if index >= len(source) or source[index] != opening:
        return None
    start, depth = index + 1, 1
    index += 1
    while index < len(source):
        if source[index] == "\\":
            _, index = _command(source, index)
            continue
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return source[start:index], index + 1
        index += 1
    raise ValueError("latex_unbalanced_group")


def _literal_end(source: str, index: int, command: str, end: int) -> int | None:
    if command == "verb":
        if end < len(source) and source[end] == "*":
            end += 1
        if end >= len(source) or source[end].isspace():
            raise ValueError("latex_invalid_verbatim")
        finish = source.find(source[end], end + 1)
        if finish < 0 or "\n" in source[end:finish]:
            raise ValueError("latex_invalid_verbatim")
        return finish + 1
    if command == "begin":
        group = _group(source, end)
        if group and group[0] in _LITERAL_ENVIRONMENTS | {"comment"}:
            closing = "\\end{" + group[0] + "}"
            finish = source.find(closing, group[1])
            if finish < 0:
                raise ValueError("latex_invalid_verbatim")
            return finish + len(closing)
    return None


def _definition_end(source: str, command: str, end: int) -> int | None:
    if command in _DEFINITION_COMMANDS:
        position = end + int(end < len(source) and source[end] == "*")
        name = _group(source, position)
        if name:
            position = name[1]
        else:
            position = _space(source, position)
            if position >= len(source) or source[position] != "\\":
                return None
            _, position = _command(source, position)
        for _ in range(2):
            optional = _group(source, position, "[", "]")
            if optional:
                position = optional[1]
        for _ in range(2 if command.endswith("environment") else 1):
            body = _group(source, position)
            if body is None:
                return None
            position = body[1]
        return position
    if command in {"def", "gdef", "edef", "xdef"}:
        position = _space(source, end)
        if position >= len(source) or source[position] != "\\":
            return None
        _, position = _command(source, position)
        # Parameter specifications precede the replacement body's first group.
        while position < len(source) and source[position] not in "{\n":
            position += 1
        body = _group(source, position)
        return body[1] if body else None
    return None


def _environment_end(source: str, command: str, end: int) -> int | None:
    if command != "begin":
        return None
    opening = _group(source, end)
    if not opening or opening[0] not in _PROTECTED_ENVIRONMENTS:
        return None
    environment, depth, position = opening[0], 1, opening[1]
    while position < len(source):
        if source[position] != "\\":
            position += 1
            continue
        token, next_position = _command(source, position)
        literal = (_literal_end(source, position, token, next_position)
                   or _definition_end(source, token, next_position))
        if literal is not None:
            position = literal
            continue
        if token in {"begin", "end"}:
            group = _group(source, next_position)
            if group and group[0] == environment:
                depth += 1 if token == "begin" else -1
                if depth == 0:
                    return group[1]
        position = next_position
    raise ValueError("latex_unclosed_environment")


def _opaque_end(source: str, index: int, command: str, end: int) -> int | None:
    return (_literal_end(source, index, command, end)
            or _definition_end(source, command, end)
            or _environment_end(source, command, end))


def _without_comments(source: str) -> str:
    pieces, index = [], 0
    while index < len(source):
        if source[index] == "\\":
            command, end = _command(source, index)
            literal = _literal_end(source, index, command, end)
            if literal is not None:
                # The comment environment has no document content. Literal
                # environments and inline verbs retain percent signs exactly.
                group = _group(source, end) if command == "begin" else None
                if group and group[0] == "comment":
                    pieces.append("\n")
                else:
                    pieces.append(source[index:literal])
                index = literal
                continue
            pieces.append(source[index:end])
            index = end
            continue
        if source[index] == "%":
            finish = source.find("\n", index)
            # TeX joins lines at a comment; do not insert spurious word breaks.
            index = len(source) if finish < 0 else finish + 1
            continue
        pieces.append(source[index])
        index += 1
    return "".join(pieces)


def _validate_dependencies(source: str) -> None:
    index = 0
    while index < len(source):
        if source[index] != "\\":
            index += 1
            continue
        command, end = _command(source, index)
        literal = _literal_end(source, index, command, end)
        if literal is not None:
            index = literal
            continue
        if command in _EXTERNAL_COMMANDS:
            raise ValueError("latex_external_dependencies")
        if command in _UNSUPPORTED_SYNTAX:
            raise ValueError("latex_unsupported_syntax")
        index = end


def _validate_groups(source: str) -> None:
    depth, index = 0, 0
    while index < len(source):
        if source[index] == "\\":
            command, end = _command(source, index)
            index = _literal_end(source, index, command, end) or end
            continue
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("latex_unbalanced_group")
        index += 1
    if depth:
        raise ValueError("latex_unbalanced_group")


def _render(source: str) -> str:
    pieces, metadata, pending_metadata, index = [], {}, set(), 0
    printed_metadata = False

    def metadata_text(keys):
        content = ["# " + metadata["title"]] if "title" in keys and metadata.get("title") else []
        content.extend(metadata[key] for key in ("author", "date") if key in keys and metadata.get(key))
        return "\n\n".join(content)

    while index < len(source):
        if source[index] != "\\":
            pieces.append(source[index])
            index += 1
            continue
        command, end = _command(source, index)
        literal = _opaque_end(source, index, command, end)
        if literal is not None:
            pieces.append(source[index:literal])
            index = literal
            continue
        starred = end < len(source) and source[end] == "*"
        after = end + int(starred)
        if command in {"title", "author", "date"}:
            group = _group(source, end)
            if group:
                # Definitions affect subsequent title displays, never earlier
                # ones. Keep the sequence when a source contains several works.
                metadata[command] = group[0]
                pending_metadata.add(command)
                index = group[1]
                continue
        if command in _HEADINGS:
            optional = _group(source, after, "[", "]")
            group = _group(source, optional[1] if optional else after)
            if group:
                pieces.append("\n\n" + "#" * _HEADINGS[command] + " " + group[0] + "\n\n")
                index = group[1]
                continue
        if command == "maketitle":
            pieces.append("\n\n" + metadata_text(metadata) + "\n\n")
            printed_metadata = True
            pending_metadata.clear()
            index = end
            continue
        if command in _LAYOUT_ARGUMENTS:
            optional = _group(source, after, "[", "]")
            position, matched = optional[1] if optional else after, True
            for _ in range(_LAYOUT_ARGUMENTS[command]):
                group = _group(source, position)
                if group is None:
                    matched = False
                    break
                position = group[1]
            if matched:
                index = position
                continue
        if command in _LAYOUT_NO_ARGUMENTS:
            pieces.append("\n")
            index = after
            continue
        pieces.append(source[index:end])
        index = end
    rendered = "".join(pieces)
    if pending_metadata:
        # Fragments often omit \maketitle. Preserve their metadata once; if
        # earlier metadata was already displayed, retain later changes at end.
        fallback = metadata_text(pending_metadata)
        if fallback:
            rendered = (rendered + "\n\n" + fallback if printed_metadata
                        else fallback + "\n\n" + rendered)
    return rendered


def parse_latex(source: str) -> str:
    """Extract self-contained source while keeping formulas and unexpanded macros.

    The document environment, when present, must form one complete pair.
    Standalone fragments are supported. We intentionally never expand macros.
    """
    if not isinstance(source, str):
        raise ValueError("latex_invalid_source")
    source = _without_comments(source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n"))
    _validate_dependencies(source)
    _validate_groups(source)
    # Protect examples containing document markers inside literal environments.
    markers, index = [], 0
    while index < len(source):
        if source[index] != "\\":
            index += 1
            continue
        command, end = _command(source, index)
        literal = _opaque_end(source, index, command, end)
        if literal is not None:
            index = literal
            continue
        if command in {"begin", "end"}:
            group = _group(source, end)
            if group and group[0] == "document":
                markers.append((command, index, group[1]))
        index = end
    if markers:
        if len(markers) != 2 or [marker[0] for marker in markers] != ["begin", "end"]:
            raise ValueError("latex_invalid_document_environment")
        start, finish = markers
        # Macro definitions in the preamble remain in the corpus so their use
        # in equations is meaningful. Layout/package commands are removed.
        if source[finish[2]:].strip():
            # TeX ignores material after \end{document}; do not silently turn
            # it into training facts when it contains additional content.
            raise ValueError("latex_content_after_document")
        source = source[:start[1]] + "\n\n" + source[start[2]:finish[1]]
    rendered = _render(source)
    rendered = rendered.strip()
    _protected_spans(rendered)
    return rendered


def _argument_end(source: str, command: str, end: int) -> int:
    position = end + int(end < len(source) and source[end] == "*")
    if not command or not command[0].isalpha():
        return end
    optional = _group(source, position, "[", "]")
    if optional:
        position = optional[1]
    while True:
        group = _group(source, position)
        if group is None:
            break
        position = group[1]
    return position


def _protected_spans(source: str, *, protect_commands=False) -> list[tuple[int, int]]:
    spans, index = [], 0
    while index < len(source):
        if source[index] == "\\":
            command, end = _command(source, index)
            finish = _opaque_end(source, index, command, end)
            if finish is None and command in {"[", "("}:
                closing = "\\" + ("]" if command == "[" else ")")
                position = end
                while position < len(source):
                    if source[position] == "\\":
                        token, next_position = _command(source, position)
                        if source[position:next_position] == closing:
                            finish = next_position
                            break
                        position = next_position
                    else:
                        position += 1
                if finish is None:
                    raise ValueError("latex_unclosed_math")
            if finish is None and protect_commands:
                # Keep unknown macro invocations and their arguments intact;
                # their definitions may be retained in the document preamble.
                finish = _argument_end(source, command, end)
            if finish is not None:
                spans.append((index, finish))
                index = finish
                continue
            index = end
            continue
        if source[index] == "$":
            delimiter = "$$" if source.startswith("$$", index) else "$"
            position, finish = index + len(delimiter), None
            while position < len(source):
                if source[position] == "\\":
                    _, position = _command(source, position)
                    continue
                if source.startswith(delimiter, position):
                    finish = position + len(delimiter)
                    break
                position += 1
            if finish is None:
                raise ValueError("latex_unclosed_math")
            spans.append((index, finish))
            index = finish
            continue
        index += 1
    return spans


def chunk_latex(text: str, target_chars: int = 2000) -> list[str]:
    """Bound prose chunks without splitting a formula, table or macro definition.

    An indivisible block larger than the target remains complete. Callers can
    then apply their explicit maximum-size policy; no half formula is emitted.
    """
    if type(target_chars) is not int or target_chars < 1:
        raise ValueError("invalid_latex_chunk_size")
    spans = _protected_spans(text, protect_commands=True)
    begins = [span[0] for span in spans]

    def containing(position: int, *, include_end=False):
        selected = bisect_right(begins, position) - 1
        if selected < 0:
            return None
        begin, end = spans[selected]
        if begin < position < end or (include_end and begin < position == end):
            return begin, end
        return None

    chunks, start = [], 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text):
            break
        finish = min(len(text), start + target_chars)
        crossed = containing(finish)
        if crossed:
            begin, end = crossed
            finish = begin if begin > start else end
        # Prefer a paragraph or word boundary, provided it is outside atoms.
        if finish < len(text) and not containing(finish, include_end=True):
            positions = [match.start() for match in re.finditer(r"\n\s*\n|\s+", text[start:finish])]
            for offset in reversed(positions):
                cut = start + offset
                if cut > start and not containing(cut):
                    finish = cut
                    break
        chunk = text[start:finish].strip()
        if chunk:
            chunks.append(chunk)
        start = finish
    return chunks
