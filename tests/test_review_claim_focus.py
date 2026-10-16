"""Focus metadata selects literal spans; it never labels statements as errors."""
from copy import deepcopy

import pytest

from lib.domain.review_claim_focus import claim_review_focus_units
from lib.domain.review_target_units import target_units


def units(*texts):
    return [{"id": f"m3u{index:04d}", "text": text} for index, text in enumerate(texts, 1)]


def test_english_cues_keep_case_original_order_and_word_boundaries():
    text = ("ONLY must cannot can't CAN’T never always require requires depend on "
            "depends\ton necessarily; onlyness mustache requirements dependable.")
    focus = claim_review_focus_units(units(text))
    assert focus["truncated"] is True
    assert [m["text"] for m in focus["units"][0]["markers"]] == [
        "ONLY", "must", "cannot", "can't", "CAN’T", "never", "always", "require"]
    tail = claim_review_focus_units(units("requires depend on depends\ton necessarily onlyness mustache requirements dependable"))
    assert [m["text"] for m in tail["units"][0]["markers"]] == [
        "requires", "depend on", "depends\ton", "necessarily"]
    assert tail["truncated"] is False


def test_chinese_literal_cues_and_codepoint_offsets_preserve_raw_input():
    original = units("😀必须先检查；只能选择一种。\r\n不可能、不能、只有、一律、永远、依赖、才可、才能。")
    before = deepcopy(original)
    focus = claim_review_focus_units(original)
    assert [m["text"] for m in focus["units"][0]["markers"]] == [
        "必须", "只能", "不可能", "不能", "只有", "一律", "永远", "依赖"]
    assert focus["units"][0]["markers"][0]["start"] == 1
    assert focus["truncated"] is True
    assert original == before
    for row in focus["units"]:
        raw = next(u["text"] for u in original if u["id"] == row["unit_id"])
        for marker in row["markers"]:
            assert raw[marker["start"]:marker["end"]] == marker["text"]
    assert [m["text"] for m in claim_review_focus_units(units("才可，才能"))["units"][0]["markers"]] == ["才可", "才能"]


def test_sensible_only_if_advice_negation_and_quotation_are_cues_not_errors():
    focus = claim_review_focus_units(units(
        "A final output check requires an output. Enable caching only if desired.",
        "It is not always wrong; the policy says ‘must’. You mustn't invent evidence.",
        "可以试试缓存，不必改变任务。你很有才能。"))
    assert set(focus) == {"units", "truncated"}
    assert [m["text"] for m in focus["units"][0]["markers"]] == ["requires", "only"]
    assert [m["text"] for m in focus["units"][1]["markers"]] == ["always", "must", "mustn't"]
    assert [m["text"] for m in focus["units"][2]["markers"]] == ["才能"]
    assert all(set(row) == {"unit_id", "markers"} for row in focus["units"])
    assert all(set(m) == {"text", "start", "end"} for row in focus["units"] for m in row["markers"])


@pytest.mark.parametrize("protected", [
    "`must only`", "```text\nnever requires\n```", "~~~text\nonly\n~~~",
    "    cannot depend on", "\t必须才能", r"$\text{only must}$",
    r"\[\text{never requires}\]", r"\(\text{必须才能}\)",
    "<code>only must</code>", "<pre>cannot requires</pre>",
])
def test_code_and_math_are_skipped_without_changing_prose_offsets(protected):
    raw = "Before always.\n" + protected + "\nAfter only if needed."
    focus = claim_review_focus_units(units(raw))
    markers = focus["units"][0]["markers"]
    assert [m["text"] for m in markers] == ["always", "only"]
    assert markers[-1]["start"] == raw.rindex("only")
    assert focus["truncated"] is False


def test_duplicates_are_positions_not_deduplicated_text_and_units_keep_order():
    focus = claim_review_focus_units(units("No cues.", "only only", "必须", "No cues."))
    assert [row["unit_id"] for row in focus["units"]] == ["m3u0002", "m3u0003"]
    assert focus["units"][0]["markers"] == [
        {"text": "only", "start": 0, "end": 4},
        {"text": "only", "start": 5, "end": 9}]


def test_exact_limits_are_not_truncation_but_any_omitted_cue_is():
    eight = claim_review_focus_units(units(" ".join(["only"] * 8)))
    assert eight["truncated"] is False
    nine = claim_review_focus_units(units(" ".join(["only"] * 9)))
    assert nine["truncated"] is True
    assert len(nine["units"][0]["markers"]) == 8
    sixty_four = units(*(["must"] * 64))
    assert claim_review_focus_units(sixty_four)["truncated"] is False
    assert claim_review_focus_units(sixty_four + [{"id": "last", "text": "no cue"}])["truncated"] is False
    overflow = claim_review_focus_units(sixty_four + [{"id": "last", "text": "only"}])
    assert len(overflow["units"]) == 64
    assert overflow["truncated"] is True


def test_empty_or_marker_free_units_do_not_consume_metadata_slots():
    many = units(*(["ordinary text"] * 100), "only")
    focus = claim_review_focus_units(many)
    assert [row["unit_id"] for row in focus["units"]] == ["m3u0101"]
    assert focus["truncated"] is False
    assert claim_review_focus_units([]) == {"units": [], "truncated": False}


def test_multiword_match_text_has_a_fixed_size_bound():
    raw = "depends" + " " * 10000 + "on only"
    focus = claim_review_focus_units(units(raw))
    assert [m["text"] for m in focus["units"][0]["markers"]] == ["only"]


def test_canonical_current_units_are_accepted_and_no_fields_or_source_are_added():
    canonical = target_units({"content": "Only if useful.\n\n必须保留原文。", "reasoning_content": "This cannot be measured yet."}, 7)
    original = deepcopy(canonical)
    focus = claim_review_focus_units(canonical)
    assert canonical == original
    assert [row["unit_id"] for row in focus["units"]] == [u["id"] for u in canonical]
    assert all(row["unit_id"].startswith("m7u") for row in focus["units"])
    assert focus == claim_review_focus_units(canonical)


@pytest.mark.parametrize("invalid", [
    None, {}, "text", [None], [{}], [{"id": "", "text": "only"}],
    [{"id": "x" * 129, "text": "only"}], [{"id": 3, "text": "only"}],
    [{"id": "m3u0001", "text": None}],
    [{"id": "same", "text": "only"}, {"id": "same", "text": "must"}],
])
def test_malformed_or_duplicate_identity_fails_before_metadata_is_emitted(invalid):
    with pytest.raises(ValueError, match="invalid_claim_review_focus_units"):
        claim_review_focus_units(invalid)
