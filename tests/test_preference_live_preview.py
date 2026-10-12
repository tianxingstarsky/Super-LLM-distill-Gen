"""Preference previews expose saved evidence without generating new labels."""
from copy import deepcopy
from contextlib import nullcontext

import pytest

from lib.presentation.streamlit import workflow_live_artifacts_view as view
from lib.domain.workflow_targets import training_record


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These read-only presentation tests do not start a TCP server."""
    yield None


def pair():
    def check(score, reason):
        return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
                "correctness": score, "reason": reason,
                "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")}}

    return {"id": "saved-pair", "status": "eligible",
            "prompt": [{"role": "user", "content": "What is 2+2?"}],
            "chosen": [{"role": "assistant", "content": "4"}],
            "rejected": [{"role": "assistant", "content": "5"}],
            "preference": {"dimension": "correctness", "minimum_gap": 2,
                           "chosen": check(5, "Correct <script>alert(1)</script>"),
                           "rejected": check(1, "Wrong arithmetic")},
            "rlaif": {"criterion": "correctness", "judge": "jev",
                      "chosen_feedback": "Correct <script>alert(1)</script>",
                      "rejected_feedback": "Wrong arithmetic"}}


def test_rlaif_projection_shows_saved_score_rank_feedback_and_dimensions_without_mutation():
    source = pair()
    original = deepcopy(source)
    markup = view._result_html("preference", source, "rlaif")
    assert 'data-target="rlaif"' in markup
    assert "候选 1 · 排名 1" in markup and "候选 2 · 排名 2" in markup
    assert "<b>AI 评分</b> 5" in markup and "<b>AI 评分</b> 1" in markup
    assert "Wrong arithmetic" in markup and "&lt;script&gt;alert(1)&lt;/script&gt;" in markup
    assert "<script>alert(1)</script>" not in markup
    assert "chosen 评分维度" in markup and "正确性 <b>5/5</b>" in markup
    assert "rejected 评分维度" in markup and "安全性 <b>1/5</b>" in markup
    assert source == original


def test_orpo_previews_actual_pair_with_clear_target_and_no_invented_score():
    source = pair()
    markup = view._result_html("preference", source, "orpo")
    assert 'data-target="orpo"' in markup and "ORPO 偏好对" in markup
    assert "更优回答 · chosen" in markup and "对照回答 · rejected" in markup
    assert "What is 2+2?" in markup and "AI 评分" not in markup
    assert view._sample_target("preference", source) == "dpo"


@pytest.mark.parametrize("target", ["dpo", "orpo"])
@pytest.mark.parametrize("empty_evidence", [None, {}])
def test_unscored_dpo_orpo_candidates_do_not_claim_a_preferred_response(target, empty_evidence):
    source = pair()
    source.pop("rlaif")
    source["preference"] = empty_evidence
    original = deepcopy(source)
    markup = view._result_html("preference", source, target, awaiting_review=True)
    assert f'data-target="{target}"' in markup and f"{target.upper()} 偏好候选" in markup
    assert "偏好候选 · 等待评分与修正" in markup
    assert "chosen / rejected 目前仅表示字段位置，尚未确认优劣。" in markup
    assert "候选回答 · chosen" in markup and "候选回答 · rejected" in markup
    assert "What is 2+2?" in markup and "更优回答" not in markup
    assert "<b>AI 评分</b>" not in markup and "排名" not in markup
    assert source == original


@pytest.mark.parametrize("target", ["dpo", "orpo"])
def test_scored_dpo_orpo_candidates_keep_the_saved_preference_even_with_later_review(target):
    markup = view._result_html("preference", pair(), target, awaiting_review=True)
    assert f'data-target="{target}"' in markup
    assert "更优回答 · chosen" in markup and "对照回答 · rejected" in markup
    assert "偏好候选 · 等待评分与修正" not in markup


def test_native_rlaif_record_keeps_its_explicit_scores_and_dimensions():
    source = {**training_record("rlaif", pair()), "_review_target": "rlaif"}
    markup = view._result_html("review", source)
    assert 'data-target="rlaif"' in markup and "<b>AI 评分</b> 5" in markup
    assert "chosen 评分维度" in markup and "安全性 <b>1/5</b>" in markup


def test_incomplete_native_rlaif_never_gets_an_invented_default_rank():
    source = {**training_record("rlaif", pair()), "_review_target": "rlaif"}
    source["responses"][0].pop("preference_rank")
    markup = view._result_html("review", source)
    assert "RLAIF 评分证据缺失或不一致" in markup
    assert "What is 2+2?" in markup and "Wrong arithmetic" in markup
    assert "<b>AI 评分</b>" not in markup and "候选 1 · 排名 1" not in markup


@pytest.mark.parametrize("stage,target", [("review", "rlaif"), ("jev", "rlaif"), ("trim", "orpo")])
def test_post_generation_rows_preserve_their_recorded_target(stage, target):
    source = {**pair(), "_review_target": target}
    assert view._sample_target(stage, source, "dpo") == target
    assert f'data-target="{target}"' in view._result_html(stage, source, "dpo")
    if target == "rlaif":
        assert "<b>AI 评分</b> 5" in view._result_html(stage, source)


def test_unscored_rlaif_candidate_waits_for_review_without_ranking_or_scores():
    source = pair()
    source.pop("rlaif")
    source.pop("preference")
    markup = view._result_html("preference", source, "rlaif", awaiting_review=True)
    assert "RLAIF 候选 · 等待评分与修正" in markup
    assert "原始候选对" in markup and "候选回答 · chosen" in markup
    assert "What is 2+2?" in markup and 'data-target="rlaif"' in markup
    assert "<b>AI 评分</b>" not in markup and "排名" not in markup
    assert "证据缺失或不一致" not in markup


@pytest.mark.parametrize("stage,awaiting_review", [("preference", False), ("review", True)])
def test_missing_rlaif_evidence_is_not_claimed_to_be_pending_after_review(stage, awaiting_review):
    source = {**pair(), "_review_target": "rlaif"}
    source.pop("rlaif")
    source.pop("preference")
    markup = view._result_html(stage, source, "rlaif", awaiting_review=awaiting_review)
    assert "RLAIF 评分证据缺失或不一致" in markup
    assert "原始候选对" in markup and "What is 2+2?" in markup
    assert "等待评分与修正" not in markup and "<b>AI 评分</b>" not in markup


@pytest.mark.parametrize("corrupt", [
    lambda row: row["rlaif"].update(chosen_feedback="A mismatched review"),
    lambda row: row["preference"]["chosen"]["scores"].update(correctness=1),
    lambda row: row.update(rlaif=[]),
])
def test_corrupt_rlaif_evidence_preserves_raw_candidates_without_fabricating_success(corrupt):
    source = pair()
    corrupt(source)
    markup = view._result_html("preference", source, "rlaif", awaiting_review=True)
    assert "评分证据缺失或不一致" in markup and "候选回答 · rejected" in markup
    assert "<b>AI 评分</b>" not in markup and "排名" not in markup


class PreviewUI:
    def __init__(self, target=None):
        self.session_state = {"ui_language": "zh"}
        if target:
            self.session_state["workflow-live-artifact-target:fixture"] = target
        self.selections, self.markup, self.tab_labels = [], [], []

    def selectbox(self, label, options, *, key, format_func):
        self.selections.append((label, options))
        return self.session_state.setdefault(key, options[0])

    def html(self, markup):
        self.markup.append(markup)

    def caption(self, _):
        pass

    def dataframe(self, *args, **kwargs):
        pass

    def tabs(self, labels):
        self.tab_labels = labels
        return [nullcontext() for _ in labels]


@pytest.mark.parametrize("targets,saved,expected", [
    (["dpo", "orpo", "rlaif"], None, "rlaif"),
    (["dpo", "orpo", "rlaif"], "orpo", "orpo"),
    (["dpo", "orpo", "rlaif"], "dpo", "dpo"),
    (["orpo"], "rlaif", "orpo"),
    (["dpo"], None, "dpo"),
])
def test_live_preview_selects_only_requested_outputs_and_reads_at_most_three_rows(monkeypatch, targets, saved, expected):
    ui, reads = PreviewUI(saved), []
    monkeypatch.setattr(view, "st", ui)

    class Application:
        def live_artifacts(self, run_id, **kwargs):
            reads.append((run_id, kwargs))
            return {"stages": [{"stage": "preference", "written": 1}],
                    "selected_stage": "preference", "rows": [pair()]}

    view.render_live_artifacts(Application(), "fixture", state={"status": "running"},
                               recipe={"version": 19, "targets": targets})
    assert reads == [("fixture", {"stage": None, "limit": 3})]
    assert set(ui.selections[-1][1]) == set(targets)
    assert ui.session_state["workflow-live-artifact-target:fixture"] == expected
    assert f'data-target="{expected}"' in ui.markup[-1]
    assert len(ui.tab_labels) == 1


def test_review_sft_row_does_not_become_a_preference_export(monkeypatch):
    ui = PreviewUI("rlaif")
    monkeypatch.setattr(view, "st", ui)

    class Application:
        def live_artifacts(self, run_id, **kwargs):
            return {"stages": [{"stage": "review", "written": 1}], "selected_stage": "review",
                    "rows": [{"id": "sft", "_review_target": "sft", "messages": [
                        {"role": "user", "content": "Question"}, {"role": "assistant", "content": "Answer"}]}]}

    view.render_live_artifacts(Application(), "fixture", state={"status": "completed"},
                               recipe={"version": 19, "targets": ["sft", "rlaif"], "review_repair": {}})
    assert len(ui.selections) == 1
    assert 'data-target="sft"' in ui.markup[-1]
