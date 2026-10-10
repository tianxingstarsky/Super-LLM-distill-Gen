"""Human designs are immutable inputs, assessed before they become samples."""
from copy import deepcopy
import json

import pytest

from lib.domain.human_augmentation import (
    HUMAN_CHECK_FIELDS, human_design, parse_human_seeds, validate_human_augmentation,
)
from lib.domain.workflow_node_prompts import (
    NODE_PROMPT_IDS, VERSION_16_NODE_PROMPT_IDS, active_node_prompt_ids,
    snapshot_node_prompts, validate_node_prompt_snapshot,
)
from lib.domain.workflow_qa_director import validate_qa_director
from lib.infrastructure import training_workflow as engine


ANSWER = "检查设备前必须先断电。检查结束后记录结果。"
QUESTION = "检查设备前应该先做什么？"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model calls are injected; this suite never opens the shared port."""
    yield


def manual(**changes):
    return {"enabled": True, "seeds": [{"question": QUESTION, "answer": ANSWER}],
            "question_requirements": "使用自然口语，保留操作前的条件。",
            "answer_requirements": "简洁表达，保留安全前提。", **changes}


def director(**changes):
    return {"enabled": True, "planning_mode": "adaptive", "batch_size": 1,
            "human_augmentation": manual(), **changes}


class Writer:
    model = "offline-human-writer"

    def __init__(self, *, duplicate=False, fail_offset=None, change_answer=False):
        self.calls, self.usage = [], {"calls": 0}
        self.duplicate, self.fail_offset, self.change_answer = duplicate, fail_offset, change_answer

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "candidates" in data:
            if data["offset"] == self.fail_offset:
                raise RuntimeError("offline planned interruption")
            tasks = []
            for candidate in data["candidates"]:
                index = 0 if self.duplicate else data["offset"]
                question = ("准备检查设备，应先采取什么安全措施？", "设备检查开始前，有什么必须先完成？",
                            "设备还没有开始检查，这时应当先做什么？")[index % 3]
                tasks.append({"id": candidate["id"], "qa_type": "closed_book", "question": question,
                    "visible_context": "", "answer_policy": "answer", "guidance": "保留人工设计的安全前提。",
                    "evidence_quotes": [candidate["teacher_evidence"].split("。")[0] + "。"],
                    "dialogue_design": {"interaction_goal": "设备检查前确认安全措施", "user_intent": "安全开始检查"}})
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        if "qa_contract" in data and "source_context" in data:
            return json.dumps({"question": data["qa_contract"]["question"],
                "answer": "必须先通电。" if self.change_answer else "先断电，再开始检查。",
                "reasoning": "断电是检查操作前的安全条件。", "quotes": ["检查设备前必须先断电。"]}, ensure_ascii=False)
        raise AssertionError("Unexpected human-generation request")


class Reviewer:
    model = "offline-human-reviewer"

    def __init__(self, *, reject=False, inconsistent=False):
        self.calls, self.usage = [], {"calls": 0}
        self.reject, self.inconsistent = reject, inconsistent

    def chat(self, messages, **kwargs):
        data, prompt = json.loads(messages[1]["content"]), messages[0]["content"]
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if '"question_intent_preserved"' in prompt:
            keep = not self.reject and not any("先通电" in message.get("content", "")
                                               for message in data["learner_messages"])
            return json.dumps({"keep": True if self.inconsistent else keep,
                **{key: keep for key in HUMAN_CHECK_FIELDS}, "reason": "设计与资料一致。" if keep else "安全前提改变。"})
        if '"adherence"' in prompt:
            return json.dumps({"keep": True, "adherence": 5, "reason": "遵守普通问答契约。"})
        keep = "BAD_NEGATIVE" not in canonical_text(data)
        score = 5 if keep else 1
        return json.dumps({"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
                          "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                          "reason": "本次来源及回答质量通过模型核对。"})


def canonical_text(value):
    return json.dumps(value, ensure_ascii=False)


class DerivedWriter(Writer):
    def chat(self, messages, **kwargs):
        data, prompt = json.loads(messages[1]["content"]), messages[0]["content"]
        if data.get("request_phase") == "next_turn":
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            done = data["completed_turns"] >= 2
            return json.dumps({"continue": not done, "user_message": "检查结束以后呢？" if not done else "",
                               "reason": "确认完成后的记录步骤。" if not done else "当前目标已完成。",
                               "dialogue_state": {"user_intent": "完成检查并记录", "progress": "已确认断电前提。"}})
        if "messages" in data and "qa_contract" in data:
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            later = len(data["messages"]) > 1
            return json.dumps({"answer": "检查结束后记录结果。" if later else "先断电，再开始检查。",
                               "quotes": ["检查结束后记录结果。" if later else "检查设备前必须先断电。"]})
        if "另一个独立" in prompt:
            self.calls.append(deepcopy(data))
            self.usage["calls"] += 1
            return json.dumps({"answer": "BAD_NEGATIVE 不需要先断电。", "reasoning": "忽略了安全条件。"})
        return super().chat(messages, **kwargs)


def run(tmp_path, *, writer=None, reviewer=None, sources=(), options=None, **kwargs):
    output = tmp_path / "out"
    run_id = engine.create_run(output, targets=kwargs.pop("targets", ["sft"]), sources=sources,
                               qa_director=options or director(), **kwargs)
    writer, reviewer = writer or Writer(), reviewer or Reviewer()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer)
    state = workflow.execute()
    return workflow, state, engine.run_path(output, run_id), writer, reviewer


def test_human_configuration_is_opt_in_bounded_and_copies_the_design():
    supplied = manual()
    normalized = validate_human_augmentation(supplied)
    assert normalized["seeds"][0]["id"]
    normalized["seeds"][0]["answer"] = "changed"
    assert supplied["seeds"][0]["answer"] == ANSWER
    assert validate_human_augmentation(None) == {"enabled": False}
    assert validate_qa_director(director(planning_mode="balanced"))["planning_mode"] == "adaptive"
    assert validate_qa_director(director(planning_mode="balanced",
        type_weights=dict.fromkeys(("closed_book", "grounded", "partial", "multi_source", "distractor"), 0)))["planning_mode"] == "adaptive"
    styles = validate_human_augmentation(manual(seeds=[
        {"question": QUESTION, "answer": ANSWER, "answer_requirements": "用正式的措辞。"},
        {"question": QUESTION, "answer": ANSWER, "answer_requirements": "使用友善的口语。"}]))
    assert len(styles["seeds"]) == 2


@pytest.mark.parametrize("value", [False, {"enabled": 1}, manual(seeds=[]), manual(seeds=[{}]),
    manual(seeds=[{"question": QUESTION, "answer": ""}]),
    manual(seeds=[{"question": QUESTION, "answer": "sk-1234567890abcdefghij"}]),
    manual(seeds=[{"question": QUESTION, "answer": ANSWER}] * 2),
    manual(question_requirements="x" * 6001)])
def test_invalid_human_design_never_enters_a_run(value):
    with pytest.raises(ValueError):
        validate_human_augmentation(value)


def test_json_and_jsonl_import_share_validation_and_cannot_mark_a_seed_approved():
    seed = {"question": QUESTION, "answer": ANSWER}
    expected = parse_human_seeds(json.dumps([seed]))
    assert parse_human_seeds(json.dumps(seed)) == expected
    assert parse_human_seeds(json.dumps({"seeds": [seed]})) == expected
    assert parse_human_seeds(json.dumps(seed) + "\n") == expected
    with pytest.raises(ValueError):
        parse_human_seeds(json.dumps({**seed, "status": "eligible"}))


def test_historical_catalog_is_unchanged_and_human_check_is_node_editable():
    old = snapshot_node_prompts(None, recipe_version=16)
    assert all(set(old[stage]) == set(ids) for stage, ids in VERSION_16_NODE_PROMPT_IDS.items())
    assert not any("workflow.human_augmentation_check" in templates for templates in old.values())
    assert validate_node_prompt_snapshot(old, "SYSTEM", recipe_version=16) == old
    human = validate_qa_director(director())
    for stage in ("sft", "multiturn", "preference", "cot", "trim"):
        assert "workflow.human_augmentation_check" in NODE_PROMPT_IDS[stage]
        assert "workflow.human_augmentation_check" in active_node_prompt_ids(stage, "人工问答增强", qa_director=human)
        assert "workflow.human_augmentation_check" not in active_node_prompt_ids(stage, "文档资料", qa_director={"enabled": True})


def test_human_only_run_freezes_seeds_generates_phrasings_and_assesses_both_designs(tmp_path):
    supplied = director()
    _, state, path, writer, reviewer = run(tmp_path, options=supplied, sample_count=3)
    assert state["status"] == "completed", state.get("error")
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 17
    assert recipe["sources"][0]["kind"] == "human_design"
    assert engine.file_hash(path / "inputs" / "human-seeds.json") == recipe["sources"][0]["sha256"]
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert len(rows) == 3 and all(row["status"] == "eligible" for row in rows)
    assert len({row["messages"][0]["content"] for row in rows}) == 3
    assert all(row["evidence_level"] == "human_provided_and_model_assessed" for row in rows)
    assert all(row["qa_contract_check"]["human_augmentation"]["keep"] for row in rows)
    checks = [call for call in reviewer.calls if "human_design" in call]
    assert len(checks) == 3
    assert all(call["source_kind"] == "human_provided" for call in checks)
    assert all(call["human_design"]["seed"]["answer"] == ANSWER for call in checks)
    assert all(call["human_design"]["question_requirements"] == supplied["human_augmentation"]["question_requirements"] for call in checks)
    assert len([call for call in writer.calls if "qa_contract" in call]) == 3
    exported = (path / "artifacts/sft.jsonl").read_text(encoding="utf-8")
    assert "question_requirements" not in exported and "human_design" not in exported
    assert recipe["qa_director"]["human_augmentation"]["seeds"][0]["id"] not in exported


def test_manual_answers_are_not_directly_published_and_failed_constraints_remain_quarantined(tmp_path):
    _, state, path, _, _ = run(tmp_path, writer=Writer(change_answer=True), reviewer=Reviewer(inconsistent=True))
    assert state["status"] == "needs_attention", state.get("error")
    assert not (path / "artifacts/sft.jsonl").read_text(encoding="utf-8").strip()
    row = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert row["status"] == "quarantined"
    assert not row["qa_contract_check"]["human_augmentation"]["keep"]


def test_human_question_variants_still_use_durable_exact_deduplication(tmp_path):
    _, state, path, _, _ = run(tmp_path, writer=Writer(duplicate=True), sample_count=3)
    assert state["status"] == "completed", state.get("error")
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert sum(row["status"] == "eligible" for row in rows) == 1
    assert sum(row.get("reason") == "duplicate_qa_contract" for row in rows) == 2


def test_document_backed_design_assessment_gets_real_source_not_the_manual_answer(tmp_path):
    source = tmp_path / "policy.txt"
    source.write_text(ANSWER + "设备型号以铭牌为准。", encoding="utf-8")
    _, state, path, _, reviewer = run(tmp_path, sources=[source])
    assert state["status"] == "completed", state.get("error")
    check = next(call for call in reviewer.calls if "human_design" in call)
    assert check["source_kind"] == "document"
    assert check["teacher_evidence"] == source.read_text(encoding="utf-8")
    assert "human-seeds.json" not in [row["file"] for row in engine.read_json(path / "recipe.json")["sources"]]


def test_resume_reuses_fixed_human_design_and_does_not_repay_successful_model_calls(tmp_path):
    workflow, state, path, writer, _ = run(tmp_path, writer=Writer(fail_offset=1), sample_count=3)
    assert state["status"] == "failed"
    writer.fail_offset = None
    state = workflow.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    first_question = "准备检查设备，应先采取什么安全措施？"
    assert len([call for call in writer.calls if call.get("qa_contract", {}).get("question") == first_question]) == 1
    assert len(engine.read_json(path / "artifacts/sft.records.json")) == 3


def test_packaging_rechecks_human_design_review_and_cannot_publish_changed_answers(tmp_path):
    workflow, state, path, _, _ = run(tmp_path)
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/sft.records.json")[0]
    assert workflow.human_package_issue(row, "sft") is None
    changed = deepcopy(row)
    changed["messages"][-1]["content"] = "先通电。"
    assert workflow.human_package_issue(changed, "sft") == "human_augmentation_review_mismatch"
    del changed["qa_contract_check"]
    assert workflow.human_package_issue(changed, "sft") == "human_augmentation_review_missing"
    changed = deepcopy(row)
    changed["qa_contract"]["human_design"]["seed"]["answer"] = "先通电。"
    assert workflow.human_package_issue(changed, "sft") == "human_augmentation_design_mismatch"
    changed = deepcopy(row)
    del changed["qa_contract"]
    assert workflow.human_package_issue(changed, "sft") == "human_augmentation_design_missing"


def test_human_only_sources_cannot_leak_into_cpt_or_agent_targets(tmp_path):
    with pytest.raises(ValueError, match="human_augmentation_requires_qa_sources"):
        engine.create_run(tmp_path / "out", targets=["sft", "cpt"], qa_director=director())
    assert not (tmp_path / "out").exists()


def test_human_snapshot_cannot_be_mutated_without_a_new_recipe(tmp_path):
    supplied = director()
    run_id = engine.create_run(tmp_path / "out", targets=["sft"], qa_director=supplied)
    supplied["human_augmentation"]["seeds"][0]["answer"] = "改变原输入。"
    recipe = engine.read_json(engine.run_path(tmp_path / "out", run_id) / "recipe.json")
    assert recipe["qa_director"]["human_augmentation"]["seeds"][0]["answer"] == ANSWER
    assert human_design(recipe["qa_director"]["human_augmentation"])["seed"]["answer"] == ANSWER


@pytest.mark.parametrize("quantity_policy", ["quality_first", "bounded_replenishment"])
def test_human_production_uses_bounded_source_rounds_and_exact_design_reviews(tmp_path, quantity_policy):
    _, state, path, writer, reviewer = run(tmp_path,
        production={"version": 2, "quantity_policy": quantity_policy, "goals": {"sft": 3},
                    "round_size": 1, "max_rounds": 5, "max_attempts": 5})
    assert state["status"] == "completed", state.get("error")
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert len(rows) == 3 and all(row["status"] == "eligible" for row in rows)
    assert state["production"]["attempted"] == 3 and state["production"]["round"] == 3
    assert all(call["planning_mode"] == "adaptive" for call in writer.calls if "candidates" in call)
    assert len([call for call in reviewer.calls if "human_design" in call]) == 3


def test_quality_first_manual_variants_do_not_repay_rejected_samples(tmp_path):
    _, state, path, writer, _ = run(tmp_path, writer=Writer(duplicate=True),
        production={"version": 2, "quantity_policy": "quality_first", "goals": {"sft": 3},
                    "round_size": 1, "max_rounds": 10, "max_attempts": 10})
    assert state["status"] == "completed", state.get("error")
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert sum(row["status"] == "eligible" for row in rows) == 1
    assert state["production"]["attempted"] == 3
    assert len([call for call in writer.calls if "qa_contract" in call]) == 1


@pytest.mark.parametrize("targets", [["sft", "cot"], ["dpo", "orpo", "rlaif"], ["multiturn"]])
def test_human_design_checks_survive_reasoning_preferences_and_multiturn(tmp_path, targets):
    workflow, state, path, _, reviewer = run(tmp_path, writer=DerivedWriter(), targets=targets)
    assert state["status"] == "completed", state.get("error")
    for target in targets:
        row = engine.read_json(path / "artifacts" / f"{target}.records.json")[0]
        assert row["status"] == "eligible", row.get("reason")
        assert workflow.human_package_issue(row, target) is None
        assert row["qa_contract"]["human_design"]["seed"]["answer"] == ANSWER
    if targets == ["multiturn"]:
        assert state["quality"]["targets"]["multiturn"]["eligible"] == 1
        row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
        assert row["turn_count"] == 2
        assert row["dialogue_end_reason"] == "natural_completion"
        assert len([call for call in reviewer.calls if "human_design" in call]) == 2


def test_real_sources_can_deliver_cpt_and_human_enhanced_sft_together_without_checking_cpt_as_qa(tmp_path):
    source = tmp_path / "policy.txt"
    text = ANSWER + "设备型号以铭牌为准。发现异常后应停止操作并联系维护人员。"
    source.write_text(text, encoding="utf-8")
    _, state, path, _, reviewer = run(tmp_path, targets=["cpt", "sft"], sources=[source])
    assert state["status"] == "completed", state.get("error")
    cpt = engine.read_json(path / "artifacts/cpt.records.json")[0]
    assert cpt["status"] == "eligible" and cpt["text"] == text
    assert "qa_contract" not in cpt
    assert len([call for call in reviewer.calls if "human_design" in call]) == 1


def test_document_conflict_is_quarantined_even_if_generic_quality_and_contract_checks_pass(tmp_path):
    class ConflictReviewer(Reviewer):
        def chat(self, messages, **kwargs):
            result = json.loads(super().chat(messages, **kwargs))
            if "source_consistent" in result:
                result["source_consistent"] = False
                result["keep"] = True
            return json.dumps(result)
    source = tmp_path / "policy.txt"
    source.write_text(ANSWER, encoding="utf-8")
    supplied = director(human_augmentation=manual(seeds=[{"question": QUESTION, "answer": "检查前先通电。"}]))
    _, state, path, _, _ = run(tmp_path, sources=[source], options=supplied, reviewer=ConflictReviewer())
    assert state["status"] == "needs_attention", state.get("error")
    assert engine.read_json(path / "artifacts/sft.records.json")[0]["status"] == "quarantined"


def test_human_production_resume_keeps_completed_round_receipts_and_design_offsets(tmp_path):
    workflow, state, path, writer, _ = run(tmp_path, writer=Writer(fail_offset=1),
        production={"version": 2, "quantity_policy": "quality_first", "goals": {"sft": 3},
                    "round_size": 1, "max_rounds": 5, "max_attempts": 5})
    assert state["status"] == "failed"
    assert state["production"]["attempted"] == 1
    writer.fail_offset = None
    state = workflow.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["attempted"] == 3 and state["production"]["round"] == 3
    calls = [call for call in writer.calls if "qa_contract" in call]
    assert len(calls) == 3 and len({call["qa_contract"]["question"] for call in calls}) == 3
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert len(rows) == 3 and all(row["status"] == "eligible" for row in rows)
