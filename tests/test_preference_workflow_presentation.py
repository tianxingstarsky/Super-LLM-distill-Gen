import re

import pytest

from lib.presentation.streamlit.workflow_canvas import canvas_spec
from lib.presentation.streamlit.workflow_preference_details import preference_details_html, preference_route


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield None


@pytest.mark.parametrize("targets,label", [
    (["orpo"], "ORPO 偏好对"), (["rlaif"], "RLAIF AI 反馈"),
    (["orpo", "rlaif", "dpo"], "DPO · RLAIF · ORPO"),
])
def test_preference_targets_share_one_truthfully_named_stage(targets, label):
    spec = canvas_spec(targets, {}, "preference", {}, {}, recipe_version=20,
                       review_repair={"mode": "auto"})
    preference = [node for node in spec["nodes"] if node["id"] == "preference"]
    assert len(preference) == 1 and preference[0]["label"] == label
    assert "评分与修正确定偏好" in preference[0]["description"]
    assert any(node["id"] == "review" for node in spec["nodes"])
    assert not any(node["id"] in {"dpo", "orpo", "rlaif"} for node in spec["nodes"])


def test_legacy_preference_stage_keeps_its_own_review_description():
    assert "本节点生成并评审" in preference_route("preference")
    assert "评分与修正确定偏好" not in preference_route("preference")


def test_selected_format_explanation_names_only_real_selected_outputs_in_english():
    markup = preference_details_html(["rlaif", "orpo"], language="en")
    assert "rlaif.jsonl" in markup and "orpo.jsonl" in markup
    assert "dpo.jsonl" not in markup
    assert "dimension scores and AI feedback" in markup
    assert "for ORPO training" in markup
    assert not re.search(r"[\u4e00-\u9fff]", markup)
    assert preference_details_html(["cpt", "sft"]) == ""
