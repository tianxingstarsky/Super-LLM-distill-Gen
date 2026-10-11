"""The research guide is composable, isolated and safe for director context."""
import json

from lib.domain.dialogue_behavior_patterns import dialogue_behavior_guide


def test_guide_is_bounded_serializable_and_not_a_quota():
    guide = dialogue_behavior_guide()
    encoded = json.dumps(guide, ensure_ascii=False, allow_nan=False)
    assert len(encoded) < 16_000
    assert guide["version"] == 1
    assert guide["catalog_type"] == "composable_design_aids"
    assert guide["exhaustive"] is False
    assert guide["patterns_are_mandatory"] is False
    assert guide["fixed_behavior_quotas"] is False
    assert all("weight" not in pattern and "quota" not in pattern for pattern in guide["patterns"])


def test_patterns_reference_known_composable_dimensions():
    guide = dialogue_behavior_guide()
    dimensions = {item["id"] for item in guide["dimensions"]}
    patterns = guide["patterns"]
    assert len(dimensions) == len(guide["dimensions"])
    assert len({item["id"] for item in patterns}) == len(patterns)
    assert all(set(item["dimensions"]) <= dimensions for item in patterns)
    assert any(len(item["dimensions"]) > 1 for item in patterns)
    for item in patterns:
        assert item["behavior"] and item["initiator_example"] and item["responder_action"]
        assert "answer" not in item


def test_actions_include_requests_repair_and_results_not_only_information_questions():
    guide = dialogue_behavior_guide()
    ids = {item["id"] for item in guide["patterns"]}
    assert {
        "practical_request", "joint_problem_solving", "negotiate_constraints",
        "repair_misunderstanding", "self_correction", "continue_from_result",
        "revise_goal_or_constraint", "report_attempt_or_outcome", "close_completed_interaction",
    } <= ids
    assert any(not item["initiator_example"].endswith("？") for item in guide["patterns"])
    assert any("停止" in item for item in guide["stop_guidance"])
    assert any("不是必须" in item for item in guide["stop_guidance"])


def test_question_and_answer_actors_have_distinct_information_boundaries():
    roles = dialogue_behavior_guide()["role_boundaries"]
    assert set(roles) == {"director", "grounded_initiator", "blind_initiator", "responder", "reviewer"}
    assert "没有隐藏资料" in roles["blind_initiator"]["source_access"]
    assert "只有" in roles["grounded_initiator"]["source_access"]
    assert "不同时" in roles["grounded_initiator"]["output_boundary"]
    assert "工具记忆" in roles["responder"]["source_access"]
    assert "不假装用户" in roles["responder"]["output_boundary"]
    assert "停止理由" in roles["reviewer"]["output_boundary"]


def test_each_call_is_an_independent_deep_copy():
    original = dialogue_behavior_guide()
    mutated = dialogue_behavior_guide()
    mutated["patterns"][0]["dimensions"].append("invented")
    mutated["dimensions"][0]["design_options"].clear()
    mutated["role_boundaries"]["blind_initiator"]["source_access"] = "private evidence"
    mutated["references"][0]["url"] = "changed"
    mutated["principles"].clear()
    assert dialogue_behavior_guide() == original


def test_primary_research_metadata_contains_no_source_body_or_demographic_rule():
    guide = dialogue_behavior_guide()
    assert len(guide["references"]) == 4
    for reference in guide["references"]:
        assert reference["url"].startswith("https://")
        assert set(reference) <= {"id", "title", "url", "author_paper_url", "design_use"}
    assert any("不" in item and "人口特征" in item for item in guide["principles"])
    assert "teacher_context_pack" not in json.dumps(guide, ensure_ascii=False)
    assert "source_line_ranges" not in json.dumps(guide, ensure_ascii=False)
