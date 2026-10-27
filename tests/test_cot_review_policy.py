"""Sampling buys fewer reviews without fabricating approvals or changing the body."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.domain.cot_review_policy import cot_review_plan
from lib.contexts.workflows.contracts import training_record
from lib.domain.workflow_generation import validate_node_generation, style_for_sample
from lib.domain.workflow_report import report_sample
from lib.bootstrap.creation_drafts import creation_draft_application
from test_cot_style_transfer import replacement
from test_integrated_reflection import recipe
from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING, ANSWER, score


def config(percent=1, mode="sampled"):
    value = recipe("faithful_restyle")
    value["node_generation"]["cot"]["review_policy"] = {"mode": mode, "sample_percent": percent}
    return value


def test_unselected_keeps_full_text_no_fake_judge_and_stable_resume_selection():
    settings, sample = config(), conversation()
    plan = cot_review_plan(settings["node_generation"]["cot"], sample)
    assert not plan["selected"]
    original = deepcopy(sample)
    port = MemoryGenerationPort([replacement()])
    row, = GenerateCot(port, settings).generate_styled(sample, {"instruction": "自然表达。"})
    assert len(port.calls) == 1 and row["status"] == "eligible"
    assert row["style_transfer"]["semantic_status"] == "not_assessed" and "judge" not in row
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER and sample == original
    assert cot_review_plan(settings["node_generation"]["cot"], {**sample, "id": "retry-id"}) == plan
    body = training_record("cot", row)
    assert set(body) == {"question", "reasoning", "answer"}
    projected = report_sample("cot", row, body, released=True, position=0)
    assert projected["provenance"]["cot_review_policy"]["semantic_status"] == "not_assessed"
    assert projected["reviews"]["fact"] == []


@pytest.mark.parametrize("mode", ["sampled", "strict"])
def test_selected_original_defect_is_still_rejected_without_resampling(mode):
    class RejectPort(MemoryGenerationPort):
        def assess(self, key, task, candidate, **options):
            self.calls.append((key, "assessment", deepcopy(candidate)))
            assert ("只因段落次序" in options["instruction"]) == (mode == "sampled")
            return {**score(), "keep": False, "reason": "原答案遗漏必要条件。"}
    port = RejectPort([replacement()])
    row, = GenerateCot(port, config(100, mode)).generate_styled(conversation(), {"instruction": "自然表达。"})
    assert len(port.calls) == 2 and row["status"] == "quarantined"
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert row["cot_review_policy"]["semantic_status"] == "not_accepted"


def test_prior_rejection_spends_nothing_and_protocol_errors_still_block_unselected():
    original = conversation()
    original["judge"] = {**score(), "keep": False, "reason": "已确认事实错误。"}
    port = MemoryGenerationPort()
    row, = GenerateCot(port, config()).generate_styled(original, {"instruction": "自然表达。"})
    assert not port.calls and row["reason"] == "cot_original_previously_rejected"
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    port = MemoryGenerationPort([{**replacement(), "reasoning_trace": {}}])
    row, = GenerateCot(port, config()).generate_styled(conversation(), {"instruction": "自然表达。"})
    assert len(port.calls) == 1 and row["status"] == "quarantined"
    assert row["style_transfer"]["semantic_status"] == "not_accepted"


def test_policy_scope_validation_and_old_recipe_reuse_remains_strict(tmp_path, monkeypatch):
    from lib.presentation.streamlit import workflow_generation_settings as controls
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
    normalized = validate_node_generation(config()["node_generation"])
    assert normalized["cot"]["review_policy"]["sample_percent"] == 1
    assert style_for_sample(normalized["cot"], "sample")["instruction"]
    for invalid in ({"mode": "unknown", "sample_percent": 20}, {"mode": "sampled", "sample_percent": True},
                    {"mode": "sampled", "sample_percent": 0}, {"mode": "sampled", "sample_percent": 101}):
        with pytest.raises(ValueError, match="invalid_cot_review_policy"):
            validate_node_generation({"cot": {**normalized["cot"], "review_policy": invalid}})
    with pytest.raises(ValueError, match="invalid_cot_review_policy"):
        validate_node_generation({"cot": {**normalized["cot"], "answer_mode": "joint"}})
    old = {**recipe("faithful_restyle"), "sources": [], "targets": ["cot"], "brief": "Restyle."}
    values = recipe_to_draft(old, "Copied", "w", [])["values"]
    assert values["workflow-generation-review-mode:w:cot"] == "strict"
    storage = creation_draft_application(tmp_path)
    storage.replace(values)
    assert creation_draft_application(tmp_path).load() == values
    monkeypatch.setattr(controls, "st", SimpleNamespace(session_state={"workflow-form-draft:w": values}))
    assert controls.generation_snapshot("w", ["cot"])["cot"]["review_policy"]["mode"] == "strict"
    new_values = {"workflow-generation-enabled:w:cot": True,
                  "workflow-generation-answer-mode:w:cot": "faithful_restyle"}
    controls.st.session_state = {"workflow-form-draft:w": new_values}
    assert controls.generation_snapshot("w", ["cot"])["cot"]["review_policy"] == {"mode": "sampled", "sample_percent": 20}


def test_published_report_does_not_present_sampling_as_individual_approval(tmp_path):
    from test_workflow_report import fixture_run, ready_page, RID
    port = MemoryGenerationPort([replacement()])
    row, = GenerateCot(port, config()).generate_styled(conversation(), {"instruction": "自然表达。"})
    app, _, _, _ = fixture_run(tmp_path, [row])
    page = ready_page(app)
    assert page["rows"][0]["provenance"]["cot_review_policy"]["semantic_status"] == "not_assessed"
    # The existing report renderer displays a distinct, bilingual notice.
    from lib.infrastructure.workflow_report import render_report_html
    zh = render_report_html(app.overview(RID), page, language="zh")
    en = render_report_html(app.overview(RID), page, language="en")
    assert "本条未抽中 CoT 内容核验" in zh and "Not selected for CoT content review" in en
