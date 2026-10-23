"""Rejection checks for local OCR; these never repair or certify mathematics."""
from collections import Counter
import unicodedata


MATH_CONSISTENCY_POLICY = "native_glyph_inventory_latex_variables_v1"


def character_inventory(text):
    return Counter(char for char in unicodedata.normalize("NFKC", text) if not char.isspace())


def validate_math_characters(native_text, glyph_text, latex):
    """Catch incomplete source spans and changed variable identities.

    The mature LaTeX decoder is used only for a rejection check, never to infer
    the answer. Equal inventories do not prove correct indices or operators.
    """
    from pylatexenc.latex2text import LatexNodes2Text
    result = {"policy": MATH_CONSISTENCY_POLICY, "review_required": True, "reasons": []}
    if character_inventory(native_text) != character_inventory(glyph_text):
        result["reasons"].append("native_glyph_character_mismatch")
    try:
        decoded = LatexNodes2Text().latex_to_text(latex)
    except (ValueError, TypeError):
        result["reasons"].append("latex_symbol_decode_failed")
        return result

    def variables(text):
        return Counter(char for char in unicodedata.normalize("NFKC", text) if char.isalpha() or char.isdigit())

    original, recognized = variables(glyph_text), variables(decoded)
    result.update(source_symbols=dict(original), recognized_symbols=dict(recognized))
    if original != recognized:
        result["reasons"].append("formula_variable_or_digit_changed")
    result["review_required"] = bool(result["reasons"])
    return result
