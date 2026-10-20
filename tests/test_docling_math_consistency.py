from lib.infrastructure.docling_math_consistency import validate_math_characters


def test_greek_upsilon_is_not_latin_v():
    result = validate_math_characters("dv", "dv", r"d_{\upsilon}")
    assert result["reasons"] == ["formula_variable_or_digit_changed"]


def test_radical_must_be_in_both_source_character_layers():
    result = validate_math_characters("dk", "√dk", r"\sqrt{d_k}")
    assert result["reasons"] == ["native_glyph_character_mismatch"]


def test_missing_product_terms_are_not_complete_formula():
    result = validate_math_characters("q·k=∑i=1dkqiki", "q·k=∑i=1dkqiki",
                                      r"q\cdot k=\sum_{i=1}^{d_k}")
    assert result["reasons"] == ["formula_variable_or_digit_changed"]


def test_format_commands_do_not_count_as_variables():
    result = validate_math_characters("WiQ∈Rdmodel×dk", "WiQ∈Rdmodel×dk",
                                      r"W_i^Q\in\mathbb{R}^{d_{\mathrm{model}}\times d_k}")
    assert not result["review_required"]


def test_inventory_does_not_claim_mathematical_verification():
    # Deliberately not detected by an inventory check: exponent attachment.
    result = validate_math_characters("a2b", "a2b", r"ab^2")
    assert not result["review_required"]
    assert "verified" not in result
