"""Configurable control views stay explicit, reversible and independent."""
from copy import deepcopy

import pytest

from lib.contexts.datasets.infrastructure.control_text import ControlTextAdapter, SCHEMA
from lib.contexts.datasets.api import default_control_text_config, preview_control_text


def _configuration():
    return {"schema": SCHEMA, "enabled": True, "rules": [{
        "id": "verbosity", "matches": ["Desired oververbosity 5", "desired verbosity 5"],
        "marker": "[[SJ_OUTPUT_VERBOSITY:5]]", "replacement": None,
        "field": "output_verbosity", "observed_value": 5,
    }]}


def test_customized_literals_markers_and_replacements_preserve_other_text():
    configuration = _configuration()
    configuration["rules"][0].update(matches=["详尽程度=7"], marker="[[我的篇幅:7]]",
                                     replacement="按需展开", observed_value=7)
    adapter = ControlTextAdapter(configuration)
    source = "我先核对任务。详尽程度=7。这里仍是完整推理。"
    marked, edits = adapter.mark(source)
    adapted, metadata = adapter.apply(source)
    assert marked == "我先核对任务。[[我的篇幅:7]]。这里仍是完整推理。"
    assert adapted == "我先核对任务。按需展开。这里仍是完整推理。"
    assert source[edits[0]["start"]:edits[0]["end"]] == "详尽程度=7"
    assert edits[0]["rule_id"] == "verbosity"
    assert metadata["source_control_edits"] == edits
    assert metadata["control_text_replacements"] == 1
    assert adapter.apply(adapted) == (adapted, {"source_control_edits": [], "control_text_replacements": 0})


def test_global_off_is_identity_even_for_existing_markers_and_task_references():
    adapter = ControlTextAdapter({"schema": SCHEMA, "enabled": False})
    source = "Desired oververbosity 5. [[SJ_REASONING_EFFORT]] [[SJ_OUTPUT_VERBOSITY:5]]"
    assert not adapter.enabled
    assert adapter.mark(source) == (source, [])
    assert adapter.apply(source, task_contents=[source]) == (
        source, {"source_control_edits": [], "control_text_replacements": 0})


def test_disabled_rule_does_not_affect_conflicts_or_other_active_rule():
    configuration = _configuration()
    disabled = deepcopy(configuration["rules"][0])
    disabled.update(id="disabled", enabled=False, replacement="Desired oververbosity 5")
    configuration["rules"].append(disabled)
    adapter = ControlTextAdapter(configuration)
    marked, _ = adapter.mark("Desired oververbosity 5")
    assert marked == "[[SJ_OUTPUT_VERBOSITY:5]]"
    assert adapter.configuration["rules"][1] == disabled
    configuration["rules"][0]["enabled"] = False
    assert ControlTextAdapter(configuration).mark("Desired oververbosity 5") == ("Desired oververbosity 5", [])


def test_configuration_is_a_snapshot_and_marker_only_rules_work():
    configuration = _configuration()
    configuration["rules"].append({"id": "effort", "matches": [],
                                   "marker": "[[SJ_REASONING_EFFORT]]", "replacement": "思考已开启"})
    adapter = ControlTextAdapter(configuration)
    original = deepcopy(configuration)
    configuration["enabled"] = False
    configuration["rules"][0]["matches"].clear()
    returned = adapter.configuration
    returned["rules"].clear()
    assert adapter.configuration == original
    source = "[[SJ_REASONING_EFFORT]]。Desired oververbosity 5."
    adapted, metadata = adapter.apply(source)
    assert adapted == "思考已开启。[[SJ_OUTPUT_VERBOSITY:5]]."
    assert metadata["control_text_replacements"] == 1
    assert len(metadata["source_control_edits"]) == 1


def test_repeated_marking_and_alias_quotations_are_not_missed():
    adapter = ControlTextAdapter(_configuration())
    source = "Need maybe deliver concise. Desired oververbosity 5."
    marked, edits = adapter.mark(source)
    assert len(edits) == 1
    assert adapter.mark(marked) == (marked, [])
    for reasoning in (source, marked):
        for quote in ("desired verbosity 5", "[[SJ_OUTPUT_VERBOSITY:5]]"):
            with pytest.raises(ValueError, match="control_text_reference_in_task_content"):
                adapter.apply(reasoning, task_contents=["解释 " + quote])
    # A different numeric value is not a reference to the configured value.
    assert adapter.apply(source, task_contents=["解释 Desired oververbosity 50"])[0] == marked


def test_longest_literal_and_numeric_boundaries_are_deterministic():
    configuration = _configuration()
    configuration["rules"].append({"id": "shorter", "matches": ["Desired oververbosity"],
                                   "marker": "[[SHORT]]", "replacement": None})
    adapter = ControlTextAdapter(configuration)
    assert adapter.mark("Desired oververbosity 5.")[0] == "[[SJ_OUTPUT_VERBOSITY:5]]."
    only_full = ControlTextAdapter(_configuration())
    source = "Desired oververbosity 50; Desired oververbosity 5.0; desired verbosity 5e3; xDesired oververbosity 5"
    assert only_full.mark(source) == (source, [])


@pytest.mark.parametrize("change", ["duplicate_literal", "duplicate_marker", "nested_marker", "cascade", "empty_target"])
def test_conflicting_or_cascading_configuration_is_rejected(change):
    configuration = _configuration()
    first = configuration["rules"][0]
    second = {"id": "other", "matches": ["Another control"], "marker": "[[OTHER]]", "replacement": None}
    if change == "duplicate_literal":
        second["matches"] = [first["matches"][0]]
    elif change == "duplicate_marker":
        second["marker"] = first["marker"]
    elif change == "nested_marker":
        second["marker"] = "outer(" + first["marker"] + ")"
    elif change == "cascade":
        first["replacement"] = "Consider Another control again."
    else:
        first["replacement"] = ""
    configuration["rules"].append(second)
    with pytest.raises(ValueError, match="control_text"):
        ControlTextAdapter(configuration)


def test_empty_rule_list_is_a_noop():
    adapter = ControlTextAdapter({"schema": SCHEMA, "enabled": True, "rules": []})
    assert adapter.mark("Desired oververbosity 5") == ("Desired oververbosity 5", [])


def test_v2_mark_only_preserves_every_character_and_records_original_spans():
    configuration = default_control_text_config()
    configuration.update(schema="shujian.control-text-adaptation/v2")
    configuration.pop("annotation_tag")
    configuration.pop("prefix_char_limit")
    configuration.pop("review_suggestions")
    original = "我先核对。Desired oververbosity 5. [[SJ_REASONING_EFFORT]]\n完整推理仍在。"
    before = deepcopy(configuration)
    adapter = ControlTextAdapter(configuration)
    marked, annotations = adapter.mark(original)
    assert marked == original
    assert [annotation["original"] for annotation in annotations] == [
        "Desired oververbosity 5", "[[SJ_REASONING_EFFORT]]"]
    assert all(original[row["start"]:row["end"]] == row["original"] for row in annotations)
    adapted, receipt = adapter.apply(original)
    assert adapted == original
    assert receipt["source_control_edits"] == []
    assert receipt["control_text_replacements"] == 0
    assert receipt["source_control_annotations"] == annotations
    assert adapter.mark(marked) == (marked, annotations)
    assert adapter.apply(adapted) == (adapted, receipt)
    assert configuration == before


def test_v2_explicit_target_replaces_literal_and_legacy_marker_but_marking_does_not():
    configuration = default_control_text_config()
    configuration.update(schema="shujian.control-text-adaptation/v2")
    configuration.pop("annotation_tag")
    configuration.pop("prefix_char_limit")
    configuration.pop("review_suggestions")
    configuration["rules"][0]["replacement"] = "按需展开"
    original = "Desired oververbosity 5. [[SJ_OUTPUT_VERBOSITY:5]]。完整推理。"
    adapter = ControlTextAdapter(configuration)
    assert adapter.mark(original)[0] == original
    preview = preview_control_text(configuration, original)
    assert preview["marked_text"] == original
    assert preview["transformed_text"] == "按需展开. 按需展开。完整推理。"
    assert preview["receipt"]["control_text_replacements"] == 2
    assert len(preview["receipt"]["source_control_annotations"]) == 2
    assert all(row["replacement"] == "按需展开" for row in preview["receipt"]["source_control_edits"])
    with pytest.raises(ValueError, match="control_text_reference_in_task_content"):
        adapter.apply(original, task_contents=["解释 desired verbosity 5"])
    configuration["enabled"] = False
    disabled = preview_control_text(configuration, original, task_contents=[original])
    assert disabled["marked_text"] == disabled["transformed_text"] == original
    assert disabled["receipt"]["source_control_annotations"] == []
    assert disabled["match_count"] == 0


def test_v3_paired_tags_preserve_original_words_and_are_idempotent():
    configuration = default_control_text_config()
    assert configuration["schema"] == "shujian.control-text-adaptation/v3"
    assert configuration["annotation_tag"] == "reason-strength"
    assert configuration["prefix_char_limit"] == 512
    assert configuration["review_suggestions"] is True
    source = "我先核对。Desired oververbosity 5.\n[[SJ_REASONING_EFFORT]] 继续推敲，不遗漏条件。"
    original_configuration = deepcopy(configuration)
    adapter = ControlTextAdapter(configuration)
    marked, annotations = adapter.mark(source)
    expected = ("我先核对。<reason-strength>Desired oververbosity 5</reason-strength>.\n"
                "<reason-strength>[[SJ_REASONING_EFFORT]]</reason-strength> 继续推敲，不遗漏条件。")
    assert marked == expected
    assert marked.replace("<reason-strength>", "").replace("</reason-strength>", "") == source
    assert [row["original"] for row in annotations] == ["Desired oververbosity 5", "[[SJ_REASONING_EFFORT]]"]
    assert all(source[row["start"]:row["end"]] == row["original"] for row in annotations)
    assert annotations[0]["field"] == "output_verbosity"
    assert annotations[0]["observed_value"] == 5
    assert not annotations[0]["upstream_control_verified"]
    assert adapter.mark(marked)[0] == expected
    repeated, receipt = adapter.apply(marked)
    assert repeated == expected
    assert receipt["control_text_replacements"] == 0
    assert receipt["source_control_edits"] == []
    assert all(marked[row["start"]:row["end"]] == row["original"]
               for row in receipt["source_control_annotations"])
    assert preview_control_text(configuration, marked)["match_count"] == 2
    assert configuration == original_configuration


def test_v3_custom_tag_and_explicit_target_are_separate_views():
    configuration = default_control_text_config()
    configuration["annotation_tag"] = "my-control"
    configuration["rules"][0]["replacement"] = "按任务需要展开"
    adapter = ControlTextAdapter(configuration)
    source = "我先核对。Desired oververbosity 5. 后续完整推理。"
    marked = "我先核对。<my-control>Desired oververbosity 5</my-control>. 后续完整推理。"
    assert adapter.mark(source)[0] == marked
    expected = "我先核对。按任务需要展开. 后续完整推理。"
    assert adapter.apply(source)[0] == expected
    adapted, receipt = adapter.apply(marked)
    assert adapted == expected
    assert receipt["control_text_replacements"] == 1
    edit = receipt["source_control_edits"][0]
    assert marked[edit["start"]:edit["end"]] == edit["original"] == "<my-control>Desired oververbosity 5</my-control>"
    annotation = receipt["source_control_annotations"][0]
    assert marked[annotation["start"]:annotation["end"]] == annotation["original"] == "Desired oververbosity 5"


def test_v3_unknown_complete_tags_are_not_reinterpreted_or_counted():
    configuration = default_control_text_config()
    source = ("<reason-strength>other control</reason-strength> "
              "<reason-strength>Desired oververbosity 5 remains a quotation</reason-strength> "
              "Desired oververbosity 5")
    preview = preview_control_text(configuration, source)
    assert preview["match_count"] == 1
    assert preview["marked_text"] == source[:-len("Desired oververbosity 5")] + (
        "<reason-strength>Desired oververbosity 5</reason-strength>")


@pytest.mark.parametrize("source", [
    "<reason-strength>Desired oververbosity 5",
    "Desired oververbosity 5</reason-strength>",
    "<reason-strength><reason-strength>Desired oververbosity 5</reason-strength></reason-strength>",
])
def test_v3_malformed_pairs_fail_without_guessing_and_off_is_identity(source):
    configuration = default_control_text_config()
    with pytest.raises(ValueError, match="control_text_unbalanced_tags"):
        ControlTextAdapter(configuration).mark(source)
    configuration["enabled"] = False
    adapter = ControlTextAdapter(configuration)
    assert adapter.mark(source) == (source, [])
    assert adapter.apply(source)[0] == source


@pytest.mark.parametrize("tag", ["", "<reason-strength>", "reason-strength class=x", "1control", "x:y", None])
def test_v3_annotation_tag_requires_a_simple_name(tag):
    configuration = default_control_text_config()
    configuration["annotation_tag"] = tag
    with pytest.raises(ValueError, match="invalid_control_text_annotation_tag"):
        ControlTextAdapter(configuration)


def test_v3_disabled_rule_and_task_reference_protection_apply_to_paired_text():
    configuration = default_control_text_config()
    configuration["rules"][1]["enabled"] = False
    adapter = ControlTextAdapter(configuration)
    marked, _ = adapter.mark("Desired oververbosity 5 [[SJ_REASONING_EFFORT]]")
    assert marked == "<reason-strength>Desired oververbosity 5</reason-strength> [[SJ_REASONING_EFFORT]]"
    for source in ("Desired oververbosity 5", "<reason-strength>Desired oververbosity 5</reason-strength>"):
        for method in (adapter.mark, adapter.apply):
            with pytest.raises(ValueError, match="control_text_reference_in_task_content"):
                method(source, task_contents=["Explain desired verbosity 5"])
    assert adapter.mark("Desired oververbosity 50") == ("Desired oververbosity 50", [])


def test_v3_rejects_tokens_or_targets_that_inject_configured_tags():
    configuration = default_control_text_config()
    configuration["rules"][0]["replacement"] = "<reason-strength>another control</reason-strength>"
    with pytest.raises(ValueError, match="control_text_replacement_cascade"):
        ControlTextAdapter(configuration)
    configuration["rules"][0].update(replacement=None, matches=["<reason-strength>"])
    with pytest.raises(ValueError, match="control_text_rule_conflict"):
        ControlTextAdapter(configuration)


def test_v3_marks_only_complete_prefix_matches_and_leaves_later_words_literal():
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = 64
    phrase = "Desired oververbosity 5"
    source = phrase + ". " + "完整正文。" * 15 + phrase
    marked, annotations = ControlTextAdapter(configuration).mark(source)
    assert marked == "<reason-strength>" + phrase + "</reason-strength>" + source[len(phrase):]
    assert len(annotations) == 1
    late_only = "正文。" * 24 + phrase
    assert ControlTextAdapter(configuration).mark(late_only) == (late_only, [])


def test_v3_boundary_counts_source_characters_not_previously_inserted_tags():
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = 64
    configuration["rules"][0]["replacement"] = "按任务展开"
    phrase = "Desired oververbosity 5"
    source = "。" * (64 - len(phrase)) + phrase + ". 完整正文。"
    adapter = ControlTextAdapter(configuration)
    marked, _ = adapter.mark(source)
    assert "<reason-strength>" in marked
    assert adapter.mark(marked)[0] == marked
    assert adapter.apply(marked)[0] == adapter.apply(source)[0] == source.replace(phrase, "按任务展开")
    crossing = "。" + source
    assert adapter.mark(crossing) == (crossing, [])
    wrapped_crossing = "。" + marked
    assert adapter.mark(wrapped_crossing) == (wrapped_crossing, [])
    assert adapter.apply(wrapped_crossing)[0] == wrapped_crossing


def test_v3_late_tag_examples_are_not_validated_but_early_damage_is_rejected():
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = 64
    source = "正文。" * 24 + "<reason-strength>Desired oververbosity 5"
    assert ControlTextAdapter(configuration).mark(source) == (source, [])
    with pytest.raises(ValueError, match="control_text_unbalanced_tags"):
        ControlTextAdapter(configuration).mark("<reason-strength>Desired oververbosity 5" + "正文" * 64)


def test_v3_unknown_wrappers_cannot_extend_prefix_scope():
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = 64
    source = "<reason-strength>unknown</reason-strength>" * 2 + "Desired oververbosity 5"
    assert ControlTextAdapter(configuration).mark(source) == (source, [])


def test_v3_straddling_longest_literal_does_not_fall_back_to_shorter_rule():
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = 64
    configuration["rules"].append({"id": "short", "matches": ["Desired oververbosity"],
                                   "marker": "[[SHORT]]", "replacement": None})
    source = "。" * 43 + "Desired oververbosity 5"
    assert ControlTextAdapter(configuration).mark(source) == (source, [])


@pytest.mark.parametrize("limit", [63, 2049, True, "512", None])
def test_v3_prefix_limit_is_a_bounded_integer_and_off_remains_identity(limit):
    configuration = default_control_text_config()
    configuration["prefix_char_limit"] = limit
    with pytest.raises(ValueError, match="invalid_control_text_prefix_limit"):
        ControlTextAdapter(configuration)
    configuration["enabled"] = False
    source = "Desired oververbosity 5 <reason-strength>unfinished"
    assert ControlTextAdapter(configuration).mark(source) == (source, [])


def test_frozen_v1_and_v2_keep_full_text_scope():
    source = "正文。" * 200 + "Desired oververbosity 5"
    legacy = _configuration()
    assert ControlTextAdapter(legacy).mark(source)[0].endswith("[[SJ_OUTPUT_VERBOSITY:5]]")
    legacy["schema"] = "shujian.control-text-adaptation/v2"
    marked, annotations = ControlTextAdapter(legacy).mark(source)
    assert marked == source
    assert len(annotations) == 1


def test_review_suggestions_metadata_is_explicit_and_does_not_change_marking():
    configuration = default_control_text_config()
    expected = ControlTextAdapter(configuration).mark("Desired oververbosity 5")
    configuration.pop("review_suggestions")
    adapter = ControlTextAdapter(configuration)
    assert "review_suggestions" not in adapter.configuration
    assert adapter.mark("Desired oververbosity 5") == expected
    configuration["review_suggestions"] = False
    assert ControlTextAdapter(configuration).mark("Desired oververbosity 5") == expected
    configuration["review_suggestions"] = "true"
    with pytest.raises(ValueError, match="invalid_control_text_review_suggestions"):
        ControlTextAdapter(configuration)
