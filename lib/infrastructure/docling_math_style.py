"""Reject unsupported OCR boldface; never edit a source glyph or LaTeX output."""
from __future__ import annotations

from collections import Counter
import re
import unicodedata


MATH_STYLE_POLICY = "source_font_program_pdfium_bold_ast_v1"
_BOLD = {"mathbf", "boldsymbol", "bm", "textbf"}
_FONT_WRAPPERS = {"mathbb", "mathrm", "mathit", "mathsf", "mathtt", "mathcal",
                  "mathscr", "mathfrak", "mathnormal", "textrm", "textit", "text"}
_SPACING = {",", ";", ":", "!", " ", "quad", "qquad", "enspace", "thinspace"}


def _normalized(text):
    return unicodedata.normalize("NFKC", text)


def _style_tokens(latex):
    """Track explicit bold scopes with the upstream parser, not a macro regex."""
    from pylatexenc.latex2text import LatexNodes2Text
    from pylatexenc.latexwalker import (
        LatexCharsNode, LatexCommentNode, LatexGroupNode, LatexMacroNode,
        get_default_latex_context_db, LatexWalker,
    )
    from pylatexenc.macrospec import MacroSpec

    context = get_default_latex_context_db()
    context.add_context_category(
        "source-bold-style", macros=[MacroSpec(name, "{") for name in _BOLD], prepend=True)
    nodes, position, length = LatexWalker(
        latex, latex_context=context, tolerant_parsing=False).get_latex_nodes()
    if position or length != len(latex):
        raise ValueError("incomplete_latex_style_parse")
    tokens, commands, reasons = [], [], []
    exact_order_supported = True
    decoder = LatexNodes2Text()

    def emit(text, active, position, *, syntax=False):
        for char in _normalized(text):
            if char.isspace() or syntax and char in "_^":
                continue
            tokens.append({"text": char, "bold_commands": list(active), "latex_position": position})

    def visit(values, active=()):
        nonlocal exact_order_supported
        for node in values:
            if isinstance(node, LatexCharsNode):
                emit(node.chars, active, node.pos, syntax=True)
            elif isinstance(node, LatexCommentNode):
                continue
            elif isinstance(node, LatexGroupNode):
                visit(node.nodelist, active)
            elif isinstance(node, LatexMacroNode):
                name = node.macroname
                args = node.nodeargd.argnlist if node.nodeargd else []
                children = [arg for arg in args if arg is not None]
                if name in _BOLD:
                    if len(children) != 1:
                        raise ValueError("missing_bold_macro_argument")
                    command_id = len(commands)
                    commands.append({"command": name, "latex_position": node.pos})
                    visit(children, active + (command_id,))
                elif children:
                    if name not in _FONT_WRAPPERS:
                        exact_order_supported = False
                        if active:
                            reasons.append("unsupported_bold_math_structure")
                    elif active:
                        # Nested font selection can override an outer command.
                        # Do not infer package-dependent font selection.
                        reasons.append("nested_bold_font_selection_requires_review")
                    visit(children, active)
                else:
                    decoded = decoder.latex_to_text(node.latex_verbatim())
                    if not decoded.strip() and name not in _SPACING:
                        exact_order_supported = False
                        if active:
                            reasons.append("unknown_bold_symbol")
                    emit(decoded, active, node.pos)
            elif getattr(node, "nodelist", None) is not None:
                exact_order_supported = False
                visit(node.nodelist, active)
            else:
                exact_order_supported = False
                if active:
                    reasons.append("unsupported_bold_latex_node")

    visit(nodes)
    return tokens, commands, reasons, exact_order_supported


def validate_math_style(glyphs, latex, *, glyph_style_proofs=None):
    """Check only explicit bold requests against uniquely bound source glyphs.

    Full ordered source equality can disambiguate repeated variables. Otherwise
    a target must occur exactly once in both source and recognized expression.
    Neither extraction order alone nor the heaviest same-name glyph is proof.
    Font-program declarations take precedence over PDFium's estimated weight.
    """
    result = {"policy": MATH_STYLE_POLICY, "review_required": False, "reasons": [],
              "bold_commands": [], "targets": [], "latex_modified": False,
              "mathematical_correctness_certified": False}
    if not isinstance(latex, str):
        result.update(review_required=True, reasons=["invalid_latex_style_input"])
        return result
    if not re.search(r"\\(?:mathbf|boldsymbol|bm|textbf)\b", latex):
        return result
    if len(latex) > 16000:
        result.update(review_required=True, reasons=["latex_style_input_too_large"])
        return result
    from pylatexenc.latexwalker import LatexWalkerError
    try:
        tokens, commands, reasons, ordered = _style_tokens(latex)
    except (ValueError, TypeError, IndexError, LatexWalkerError) as exc:
        result.update(review_required=True, reasons=["latex_style_parse_failed"],
                      parse_error=type(exc).__name__)
        return result
    result["bold_commands"] = commands
    result["reasons"].extend(reasons)
    targets = [(i, token) for i, token in enumerate(tokens) if token["bold_commands"]]
    if commands and not targets:
        result["reasons"].append("empty_or_unknown_bold_target")
    if not targets:
        result["review_required"] = bool(result["reasons"])
        return result
    if (any(type(g.get("char_index")) is not int or not isinstance(g.get("text"), str) for g in glyphs)
            or len({g["char_index"] for g in glyphs}) != len(glyphs)):
        result.update(review_required=True, reasons=["invalid_source_style_glyphs"])
        return result
    source = [(char, glyph) for glyph in sorted(glyphs, key=lambda g: g["char_index"])
              for char in _normalized(glyph["text"]) if not char.isspace()]
    source_text = "".join(char for char, _ in source)
    recognized = "".join(token["text"] for token in tokens)
    exact = ordered and source_text == recognized
    counts = Counter(token["text"] for token in tokens)
    proofs = glyph_style_proofs or {}
    for position, token in targets:
        hits = ([source[position][1]] if exact else
                [glyph for char, glyph in source if char == token["text"]])
        check = dict(token, source_char_index=None, supported=False)
        result["targets"].append(check)
        if len(hits) != 1 or not exact and counts[token["text"]] != 1:
            check["reason"] = "bold_source_character_not_unique"
        else:
            glyph = hits[0]
            index = glyph["char_index"]
            check.update(source_char_index=index, font_weight=glyph.get("font_weight"),
                         alignment_method="exact_complete_ordered_sequence" if exact else "unique_source_symbol")
            proof = proofs.get(index)
            if glyph.get("generated"):
                check["reason"] = "source_style_glyph_generated"
            elif proof is not None:
                check["source_font_proof"] = proof
                if (proof.get("pdfium_char_index") != index or not proof.get("font_program_sha256")
                        or not proof.get("source_sha256")
                        or _normalized(glyph["text"]) not in {
                            _normalized(proof.get("comparison_text", "")),
                            _normalized(proof.get("pdfium_original_text", ""))}):
                    check["reason"] = "invalid_source_font_style_binding"
                elif not isinstance(proof.get("source_weight"), dict):
                    check["reason"] = "source_font_program_weight_unknown"
                elif proof["source_weight"].get("supports_bold") is True:
                    check["supported"] = True
                    check["weight_source"] = "embedded_font_program"
                elif proof["source_weight"].get("supports_bold") is False:
                    check["reason"] = "source_font_program_does_not_support_bold"
                else:
                    check["reason"] = "source_font_program_weight_unknown"
            else:
                weight = glyph.get("font_weight")
                if type(weight) is not int or not 0 < weight <= 1000:
                    check["reason"] = "source_font_weight_unknown"
                elif weight >= 700:
                    check["supported"] = True
                    check["weight_source"] = "FPDFText_GetFontWeight"
                else:
                    check["reason"] = "source_font_weight_does_not_support_bold"
        if not check["supported"]:
            result["reasons"].append(check["reason"])
    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    result["review_required"] = bool(result["reasons"])
    return result
