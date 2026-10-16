"""Opted-in punctuation segments keep exact raw spans and full-turn context."""
from copy import deepcopy

import pytest

from lib.domain.review_target_units import target_units, validate_target_units


def texts(fields):
    return [unit["text"] for unit in target_units(fields, 3, segmentation_version=2)]


def assert_coverage(fields, units):
    assert [unit["id"] for unit in units] == [f"m3u{i:04d}" for i in range(1, len(units) + 1)]
    for field, text in fields.items():
        covered, previous = set(), 0
        for unit in (u for u in units if u["field"] == field):
            assert previous <= unit["start"] < unit["end"] <= len(text)
            assert text[unit["start"]:unit["end"]] == unit["text"]
            covered.update(range(unit["start"], unit["end"]))
            previous = unit["end"]
        assert all(i in covered for i, char in enumerate(text) if not char.isspace())


def test_short_english_sentences_semicolons_and_relation_commas_are_separate():
    fields = {"content": "State A exists. State B is new; choose a method, and run it, BUT inspect the output, so report a limit, therefore keep uncertainty, whereas ordinary commas, lists, remain."}
    assert texts(fields) == [
        "State A exists.", "State B is new;", "choose a method,", "and run it,",
        "BUT inspect the output,", "so report a limit,", "therefore keep uncertainty,",
        "whereas ordinary commas, lists, remain."]
    assert_coverage(fields, target_units(fields, 3, segmentation_version=2))


def test_chinese_sentences_semicolons_and_relation_commas_preserve_connectors():
    fields = {"content": "甲已经存在。乙尚未生成；需要检查，而策略可以先选择，但是结果须后验，所以先记录，因此再处理，否则等待，却不能混淆。真的？当然！"}
    assert texts(fields) == [
        "甲已经存在。", "乙尚未生成；", "需要检查，", "而策略可以先选择，", "但是结果须后验，",
        "所以先记录，", "因此再处理，", "否则等待，", "却不能混淆。", "真的？", "当然！"]
    assert_coverage(fields, target_units(fields, 3, segmentation_version=2))


def test_only_if_and_ordinary_conditions_are_not_risk_keyword_boundaries():
    raw = "Use caching only if the input is unchanged, otherwise compute again. If it is available, reuse it; if not, wait. 只有条件满足，才可复用；否则重新计算。"
    fields = {"content": raw}
    assert texts(fields) == [
        "Use caching only if the input is unchanged, otherwise compute again.",
        "If it is available, reuse it;", "if not, wait.",
        "只有条件满足，才可复用；", "否则重新计算。"]
    assert fields["content"] == raw
    assert_coverage(fields, target_units(fields, 3, segmentation_version=2))


@pytest.mark.parametrize("block", [
    "```python\nif ready: call(); wait()  # one. two, and three\n\nprint('甲；乙。因此')\n```",
    "~~~text\nOne. Two; Three, and four.\n~~~",
    "    one(); two()\n\n    three = 'one. two; three, and four'\n",
    "\tone(); two()\n\n\tthree()\n",
    r"$$x=1; y=2.\text{one, and two。}$$",
    r"\[x=1; y=2.\text{one, and two。}\]",
    "<pre>one. two; three, and four。</pre>",
])
def test_code_and_formula_blocks_remain_whole_including_indented_blank_lines(block):
    fields = {"content": "Before.\n\n" + block + "\n\nAfter; done."}
    units = target_units(fields, 3, segmentation_version=2)
    assert any(block.rstrip("\n") in unit["text"] for unit in units)
    assert_coverage(fields, units)


def test_inline_code_math_and_quoted_sentence_endings_preserve_raw_spans():
    fields = {"content": "Keep `one. two; three, and four` whole. Keep $x=1; y=2$ whole; then say \"Done.\" Next (finished!). 结束。” 后续。"}
    assert texts(fields) == [
        "Keep `one. two; three, and four` whole.", "Keep $x=1; y=2$ whole;",
        'then say "Done."', "Next (finished!).", "结束。”", "后续。"]
    assert_coverage(fields, target_units(fields, 3, segmentation_version=2))


def test_numbers_lists_and_common_abbreviations_are_not_split_as_sentences():
    fields = {"content": "Dr. A checks 2.5 units. There are 5.\n1. First item; next.\n2. Second item.\nA. Another item."}
    assert texts(fields) == [
        "Dr. A checks 2.5 units.", "There are 5.", "1. First item;", "next.",
        "2. Second item.", "A. Another item."]


def test_serialized_nonprose_fields_remain_one_unit_and_field_order_is_stable():
    fields = {"reasoning_content": "甲。乙；丙，而丁。", "tool_calls": '[{"arguments":"a. b; c, and d"}]', "content": "One. Two."}
    units = target_units(fields, 3, segmentation_version=2)
    assert [u["text"] for u in units if u["field"] == "tool_calls"] == [fields["tool_calls"]]
    assert [u["field"] for u in units] == ["reasoning_content"] * 4 + ["tool_calls"] + ["content"] * 2
    assert_coverage(fields, units)


def test_legacy_none_retains_exact_frozen_expected_units():
    fields = {"content": "  One. Two; Three, and four.\n\n- 甲。乙；丙，而丁。\n", "reasoning_content": "Reason. More reason."}
    expected = [
        {"id": "m3u0001", "field": "content", "start": 2, "end": 28, "text": "One. Two; Three, and four."},
        {"id": "m3u0002", "field": "content", "start": 30, "end": 41, "text": "- 甲。乙；丙，而丁。"},
        {"id": "m3u0003", "field": "reasoning_content", "start": 0, "end": 20, "text": "Reason. More reason."},
    ]
    assert target_units(fields, 3) == target_units(fields, 3, segmentation_version=None) == expected
    assert validate_target_units(expected, fields, 3) == expected
    assert validate_target_units(expected, fields, 3, segmentation_version=None) == expected
    assert len(target_units(fields, 3, segmentation_version=2)) > len(expected)


def test_v2_does_not_inherit_the_legacy_500_character_cut_inside_abbreviation():
    sentence = "A continuous clause " * 30 + "with Dr. Green still speaking."
    assert texts({"content": sentence + " Next sentence."}) == [sentence, "Next sentence."]


def test_v2_is_deterministic_immutable_and_preserves_unicode_offsets():
    fields = {"content": " \t😀甲。\r\n乙；\r\n丙，而丁。\r\n", "thinking": "Maybe? Then act!"}
    original = deepcopy(fields)
    units = target_units(fields, 3, segmentation_version=2)
    assert fields == original
    assert units == target_units(fields, 3, segmentation_version=2)
    assert validate_target_units(units, fields, 3, segmentation_version=2) == units
    assert_coverage(fields, units)


@pytest.mark.parametrize("mutation", [
    lambda us: us.pop(), lambda us: us.reverse(), lambda us: us.append(deepcopy(us[0])),
    lambda us: us[0].update(id="m1u0001"), lambda us: us[0].update(field="thinking"),
    lambda us: us[0].update(start=1), lambda us: us[0].update(end=True),
    lambda us: us[0].update(text="invented"), lambda us: us[0].update(extra=True),
])
def test_validator_rejects_changed_identity_span_text_order_or_fields(mutation):
    fields = {"content": "One. Two; Three."}
    units = target_units(fields, 3, segmentation_version=2)
    mutation(units)
    with pytest.raises(ValueError, match="invalid_review_target_units"):
        validate_target_units(units, fields, 3, segmentation_version=2)


def test_validator_cannot_mix_legacy_and_v2_or_apply_wrong_turn():
    fields = {"content": "One. Two."}
    old = target_units(fields, 3)
    fine = target_units(fields, 3, segmentation_version=2)
    for units, version, index in [(old, 2, 3), (fine, None, 3), (fine, 2, 4)]:
        with pytest.raises(ValueError, match="invalid_review_target_units"):
            validate_target_units(units, fields, index, segmentation_version=version)


@pytest.mark.parametrize("version", [True, False, 0, 1, 3, "2", 2.0, [], {}])
def test_invalid_segmentation_versions_fail_in_both_entry_points(version):
    with pytest.raises(ValueError, match="invalid_review_target_segmentation_version"):
        target_units({"content": "text"}, 3, segmentation_version=version)
    with pytest.raises(ValueError, match="invalid_review_target_segmentation_version"):
        validate_target_units([], {"content": "text"}, 3, segmentation_version=version)


def test_whitespace_only_fields_and_connector_prefixes_do_not_create_fake_units():
    assert target_units({"content": " \n\t"}, 3, segmentation_version=2) == []
    assert texts({"content": "First, android stays, sofa stays, butterscotch stays."}) == [
        "First, android stays, sofa stays, butterscotch stays."]
