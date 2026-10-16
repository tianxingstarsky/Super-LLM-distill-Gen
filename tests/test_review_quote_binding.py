"""Presentation-only candidate quotes retain a continuous original text span."""
import pytest

from lib.domain.review_quote_binding import bind_candidate_quote


def binding(text, quote, *, version=1):
    return bind_candidate_quote(text, quote, field="content",
        response_path=["claim_checks", 0, "quote"], version=version)


@pytest.mark.parametrize("text,quote", [
    ("**If you later want to move it earlier** — say, before inspection.",
     "If you later want to move it earlier — say, before inspection."),
    ("The **pool** is not ready.", "The pool is not ready."),
    ("The *pool* is not ready.", "The pool is not ready."),
    ("The __pool__ is not ready.", "The pool is not ready."),
    ("The _pool_ is not ready.", "The pool is not ready."),
    ("The **pool** and *reader* cannot proceed.", "The pool and reader cannot proceed."),
    ("**确认停机**，再检查。", "确认停机，再检查。"),
    ("**The *pool* and reader** cannot proceed.", "The pool and reader cannot proceed."),
])
def test_paired_emphasis_only_keeps_continuous_original_span(text, quote):
    result = binding(text, quote)
    assert result["match"] == "markdown_emphasis"
    assert text[result["source_start"]:result["source_end"]] == result["source_text"]
    assert result["quote"] == quote and result["field"] == "content"
    assert result["response_path"] == ["claim_checks", 0, "quote"]


def test_exact_match_has_priority_even_when_an_emphasized_alternative_exists():
    text = "The **pool** is ready. The pool is ready."
    result = binding(text, "The pool is ready.")
    assert result["match"] == "exact"
    assert result["source_start"] == text.index("The pool is ready.")
    assert result["source_text"] == result["quote"]
    assert binding(text, "pool", version=None)["match"] == "exact"


@pytest.mark.parametrize("text,quote", [
    ("The **pool** is not ready.", "The pool is ready."),
    ("The **pool** is not ready.", "The pool is still not ready."),
    ("A **first**. Then **second**.", "A first. second."),
    ("The  **pool** is ready.", "The pool is ready."),
    ("The **pool**, is ready.", "The pool is ready."),
    ("The **pool is ready.", "The pool is ready."),
    ("The ***pool*** is ready.", "The pool is ready."),
    (r"The \**pool** is ready.", "The pool is ready."),
    ("The `**pool** is ready` example.", "pool is ready"),
    ("The ``**pool** is ready`` example.", "pool is ready"),
    ("```python\n**pool** is ready\n```", "pool is ready"),
    ("~~~\n**pool** is ready\n~~~", "pool is ready"),
    ("```\n**pool** is ready", "pool is ready"),
    ("    **pool** is ready", "pool is ready"),
    ("<code>**pool** is ready</code>", "pool is ready"),
    ("$**pool** is ready$", "pool is ready"),
    ("$$**pool** is ready$$", "pool is ready"),
    (r"\(**pool** is ready\)", "pool is ready"),
    (r"\[**pool** is ready\]", "pool is ready"),
    ("x**y**z is ready.", "xyz is ready."),
    ("2 ** 3 ** 4", "2  3  4"),
    ("snake_case_name is ready", "snakecasename is ready"),
])
def test_rewriting_stitching_code_math_and_ambiguous_markup_remain_invalid(text, quote):
    with pytest.raises(ValueError, match="fabricated_claim_review_quote"):
        binding(text, quote)


def test_absent_version_preserves_strict_literal_matching():
    with pytest.raises(ValueError, match="fabricated_claim_review_quote"):
        binding("The **pool** is ready.", "The pool is ready.", version=None)


@pytest.mark.parametrize("version", [True, False, 0, 2, "1", []])
def test_policy_version_is_strict(version):
    with pytest.raises(ValueError, match="invalid_claim_review_quote_matching_version"):
        binding("literal", "literal", version=version)


def test_reference_check_uses_distinct_response_path_without_changing_raw_quote():
    result = bind_candidate_quote("The **pool** is ready.", "The pool is ready.", field="reasoning_content",
        response_path=["reference_checks", 2, "quote"], version=1)
    assert result["response_path"] == ["reference_checks", 2, "quote"]
    assert result["field"] == "reasoning_content"
