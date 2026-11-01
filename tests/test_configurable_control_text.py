"""Configurable control views stay explicit, reversible and independent."""
from copy import deepcopy

import pytest

from lib.contexts.datasets.infrastructure.control_text import ControlTextAdapter, SCHEMA


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
