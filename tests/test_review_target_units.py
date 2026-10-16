"""Reviewer unit IDs bind every visible character to the current raw field."""
from copy import deepcopy

import pytest

from lib.domain.review_target_units import target_units, validate_target_units


def assert_coverage(fields, units):
    for field, text in fields.items():
        covered, previous_end = set(), 0
        for unit in (unit for unit in units if unit["field"] == field):
            assert previous_end <= unit["start"] < unit["end"] <= len(text)
            assert text[unit["start"]:unit["end"]] == unit["text"]
            covered.update(range(unit["start"], unit["end"]))
            previous_end = unit["end"]
        assert all(index in covered for index, char in enumerate(text) if not char.isspace())


def test_paragraphs_list_items_unicode_and_field_order_preserve_original_spans():
    fields = {"reasoning_content": "  判断😀。\r\n\r\n前提成立。\r\n- **保留否定**。\r\n- 保留数量 12。\r\n",
              "content": "回答。\n\n同样的回答。\n\n同样的回答。"}
    original = deepcopy(fields)
    units = target_units(fields, 3)
    assert [unit["text"] for unit in units] == [
        "判断😀。", "前提成立。", "- **保留否定**。", "- 保留数量 12。",
        "回答。", "同样的回答。", "同样的回答。"]
    assert [unit["id"] for unit in units] == [f"m3u{index:04d}" for index in range(1, 8)]
    assert fields == original
    assert validate_target_units(units, fields, 3) == units
    assert_coverage(fields, units)


@pytest.mark.parametrize("block", [
    "```python\n" + "x = 'Do not split. '  # 否定。\n" * 40 + "\nprint(x)\n```",
    "~~~text\n" + "literal.\n" * 80 + "\n- still code\n~~~",
    "    x = 1.\n\n" + "    y = 'Not a prose sentence.'\n" * 40,
    "$$\n" + r"x=1.0+\text{not. equal.}" * 40 + "\n\n$$",
    r"\[" + r"x=1.0+\text{not. equal.}" * 40 + r"\]",
    "<pre>" + "not. prose.\n\n" * 80 + "</pre>",
])
def test_code_and_math_with_internal_blank_lines_remain_in_one_unit(block):
    fields = {"content": "Before.\n\n" + block + "\n\nAfter."}
    units = target_units(fields, 1)
    assert any(block in unit["text"] for unit in units)
    assert_coverage(fields, units)


def test_long_prose_splits_only_on_sentence_boundaries_and_inline_code_is_intact():
    inline = "`" + "Do not split. " * 70 + "`"
    fields = {"content": ("A clear sentence. " * 100) + inline + ". More prose. " * 50}
    units = target_units(fields, 5)
    assert len(units) > 2
    assert any(inline in unit["text"] for unit in units)
    assert_coverage(fields, units)


def test_long_unsplittable_prose_and_serialized_fields_are_not_hard_truncated():
    fields = {"content": "很长但没有句末边界" * 200,
              "tool_calls": '[\n  {"arguments": "' + "keep. " * 200 + '"}\n]'}
    units = target_units(fields, 0)
    assert len(units) == 2
    assert [unit["text"] for unit in units] == list(fields.values())
    assert_coverage(fields, units)


@pytest.mark.parametrize("mutation", [
    lambda units: units.pop(),
    lambda units: units.reverse(),
    lambda units: units.append(deepcopy(units[0])),
    lambda units: units[0].update(id="m1u0001"),
    lambda units: units[0].update(field="reasoning_content"),
    lambda units: units[0].update(start=1),
    lambda units: units[0].update(end=True),
    lambda units: units[0].update(text="Fabricated assertion."),
    lambda units: units[0].update(extra="untrusted"),
])
def test_validator_rejects_missing_reordered_cross_turn_or_fabricated_units(mutation):
    fields = {"content": "First.\n\nSecond.", "reasoning_content": "Reason."}
    units = target_units(fields, 3)
    mutation(units)
    with pytest.raises(ValueError, match="invalid_review_target_units"):
        validate_target_units(units, fields, 3)


@pytest.mark.parametrize("fields,index", [
    ([], 1), ({1: "text"}, 1), ({"": "text"}, 1), ({"content": None}, 1),
    ({"content": "text"}, True), ({"content": "text"}, -1), ({"content": "text"}, "1"),
])
def test_invalid_fields_or_message_identity_fail_before_segmentation(fields, index):
    with pytest.raises(ValueError, match="invalid_review_target_fields"):
        target_units(fields, index)


def test_empty_and_whitespace_only_fields_have_no_visible_units():
    fields = {"content": " \n\t", "reasoning_content": ""}
    assert target_units(fields, 1) == []
    assert validate_target_units([], fields, 1) == []
