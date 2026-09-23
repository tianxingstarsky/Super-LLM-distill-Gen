"""Visual preference-form conversion must preserve hand-edited YAML."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lib.application.preference_service import preference_summary, update_preference_yaml


SOURCE = Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml"


def _form(text: str) -> dict:
    summary = preference_summary(text)
    return {key: summary[key] for key in (
        "default_share", "relative_tendencies", "templates_per_dim", "shuffle_per_batch",
        "correction_enabled", "correction_threshold", "correction_tagger", "cot_style",
    )}


def test_actual_config_summary_and_unchanged_form_are_exact_roundtrip():
    source = SOURCE.read_text(encoding="utf-8")
    summary = preference_summary(source)
    assert summary["default_share"] == 0.15
    assert summary["default_floor"] == 0.15
    assert sum(summary["weights"].values()) == pytest.approx(1.0)
    assert sum(summary["relative_tendencies"].values()) == pytest.approx(1.0)
    assert summary["correction_tagger"] == "unitag"
    assert summary["cot_style"] == "separated"
    assert update_preference_yaml(source, **_form(source)) == source


def test_form_updates_normalized_weights_and_preserves_comments_extra_fields_and_crlf():
    source = SOURCE.read_text(encoding="utf-8").replace("\n", "\r\n")
    source += "\r\ncustom_section:\r\n  untouched: yes  # 人工补充的字段\r\n"
    original_parsed = yaml.safe_load(source)
    updated = update_preference_yaml(
        source, default_share=0.20,
        relative_tendencies={"reasoning": 4, "context_use": 3, "tool_use": 2, "bilingual": 1},
        templates_per_dim=6, shuffle_per_batch=False,
        correction_enabled=False, correction_threshold=0.07,
        correction_tagger="ultrafeedback", cot_style="plain",
    )
    summary = preference_summary(updated)
    assert summary["weights"] == {"reasoning": 0.32, "context_use": 0.24,
                                  "tool_use": 0.16, "bilingual": 0.08, "default": 0.20}
    assert summary["templates_per_dim"] == 6 and summary["shuffle_per_batch"] is False
    assert summary["correction_enabled"] is False and summary["correction_threshold"] == 0.07
    assert summary["correction_tagger"] == "ultrafeedback" and summary["cot_style"] == "plain"
    assert "# 深度思考 / CoT / 反思" in updated
    assert "# 人工补充的字段" in updated
    assert updated.count("\n") == updated.count("\r\n")
    after = yaml.safe_load(updated)
    assert after["custom_section"] == original_parsed["custom_section"]
    assert after["sampling"]["default_floor"] == original_parsed["sampling"]["default_floor"]
    assert after["cot"]["think_tokens"] == original_parsed["cot"]["think_tokens"]
    assert SOURCE.read_text(encoding="utf-8").replace("\n", "\r\n") != updated


def test_zero_relative_tendency_stays_zero_after_normalization():
    source = SOURCE.read_text(encoding="utf-8")
    form = _form(source)
    form["default_share"] = 0.3
    form["relative_tendencies"] = {"reasoning": 3, "context_use": 1,
                                     "tool_use": 0, "bilingual": 0}
    summary = preference_summary(update_preference_yaml(source, **form))
    assert summary["weights"]["tool_use"] == 0
    assert summary["weights"]["bilingual"] == 0
    assert summary["weights"]["reasoning"] == pytest.approx(0.525)
    assert summary["weights"]["context_use"] == pytest.approx(0.175)
    assert sum(summary["weights"].values()) == pytest.approx(1.0)


@pytest.mark.parametrize("alteration,reason", [
    (lambda s: s.replace("reasoning: 0.30", "reasoning: 0.99"), "preference_weights_must_sum_to_one"),
    (lambda s: s.replace("default: 0.15", "default: 0.10"), "preference_weights_must_sum_to_one"),
    (lambda s: s.replace("templates_per_dim: 4", "templates_per_dim: 0"), "preference_invalid_templates_per_dim"),
    (lambda s: s.replace("shuffle_per_batch: true", "shuffle_per_batch: maybe"), "preference_invalid_shuffle"),
    (lambda s: s.replace("tagger: unitag", "tagger: unknown"), "preference_invalid_tagger"),
    (lambda s: s.replace("style: separated", "style: tags"), "preference_tags_require_model_tokens"),
    (lambda s: s + "\npreferences:\n  default: 1\n", "preference_duplicate_yaml_key"),
    (lambda s: "preferences: [broken", "preference_invalid_yaml"),
])
def test_invalid_existing_config_is_not_silently_rewritten(alteration, reason):
    source = alteration(SOURCE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match=reason):
        preference_summary(source)


def test_tags_mode_requires_real_tokens_but_preserves_them_when_present():
    source = SOURCE.read_text(encoding="utf-8").replace('think_tokens: ["", ""]',
                                                     'think_tokens: ["<think>", "</think>"]')
    updated = update_preference_yaml(source, **{**_form(source), "cot_style": "tags"})
    assert preference_summary(updated)["cot_style"] == "tags"
    assert 'think_tokens: ["<think>", "</think>"]' in updated


@pytest.mark.parametrize("field,value,reason", [
    ("default_share", 0.1, "preference_default_below_floor"),
    ("default_share", 1.0, "preference_invalid_default_share"),
    ("templates_per_dim", True, "preference_invalid_templates_per_dim"),
    ("correction_threshold", 1.5, "preference_invalid_correction_threshold"),
    ("correction_tagger", "unknown", "preference_invalid_tagger"),
    ("cot_style", "custom", "preference_invalid_cot_style"),
])
def test_invalid_form_values_raise_without_producing_yaml(field, value, reason):
    source = SOURCE.read_text(encoding="utf-8")
    form = _form(source)
    form[field] = value
    with pytest.raises(ValueError, match=reason):
        update_preference_yaml(source, **form)


def test_all_zero_relative_tendencies_are_rejected():
    source = SOURCE.read_text(encoding="utf-8")
    form = _form(source)
    form["relative_tendencies"] = {key: 0 for key in form["relative_tendencies"]}
    with pytest.raises(ValueError, match="preference_invalid_relative_tendencies"):
        update_preference_yaml(source, **form)


def test_yaml_aliases_are_rejected_before_replacing_anchor_values():
    source = SOURCE.read_text(encoding="utf-8")
    source = source.replace("  default: 0.15", "  default: &default_share 0.15")
    source = source.replace("  default_floor: 0.15", "  default_floor: *default_share")
    with pytest.raises(ValueError, match="preference_yaml_aliases_unsupported"):
        preference_summary(source)
