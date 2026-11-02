"""One-call wire ownership, unreviewed replacement lineage and local memory receipts."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.contexts.review.contracts import (parse_review_evolution, review_evolution_schema,
    parse_review_evolution_submission, review_evolution_submission_schema,
    render_review_memory_item, review_evolution_step_snapshot,
    finalize_native_sft_review_evolution, native_sft_review_evolution_release_valid,
    native_sft_review_evolution_summary)
from lib.contexts.review.domain.candidate_content import candidate_digest as digest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def wire(keep=False, proposed=True):
    return {"verdict": {"keep": keep, "grounded": True, "reasoning_valid": True,
        "correctness": 5 if keep else 3, "scores": {key: 5 if keep else 3 for key in
            ("correctness", "reasoning", "grounding", "instruction", "safety")},
        "reason": "依据实际输出空间判断，不以表面格式替代判据。"},
        "task_notes": [] if keep else ["依照用户已给出的标签集合重判，保留未知条件。"],
        "memory_proposal": {"status": "proposed" if proposed else "no_update", "memory_items": [{
            "title": "区分实际判据与输出形式", "description": "表面表达可能遮蔽真正判断依据时。",
            "content": "先核对任务的真实决策依据，按实际条件应用已有规则。", "context_types": ["common"]}] if proposed else []}}


def test_closed_tool_schema_owns_semantic_fields_and_program_serializes_memory():
    schema = review_evolution_schema()
    def check(part):
        if not isinstance(part, dict):
            return
        if part.get("type") == "object":
            assert part["additionalProperties"] is False
            assert set(part["required"]) == set(part["properties"])
        assert "maxLength" not in part
        for child in part.values():
            if isinstance(child, dict):
                check(child)
            elif isinstance(child, list):
                for item in child:
                    check(item)
    check(schema)
    value = wire()
    value["memory_proposal"]["memory_items"][0]["content"] += "\n## Contexts clinical"
    parsed = parse_review_evolution(value)
    item, = parsed["memory_proposal"]["memory_items"]
    assert item.startswith("# Memory Item 1\n") and "\n## Contexts common" in item
    assert "\n\\## Contexts clinical" in item
    assert value["memory_proposal"]["memory_items"][0]["content"].endswith("## Contexts clinical")
    assert parse_review_evolution(wire(proposed=False))["memory_proposal"] == {"status": "no_update", "memory_items": []}


def test_flat_submission_assembles_existing_business_contract_and_rejects_missing_or_unknown_fields():
    nested = wire()
    quality = nested["verdict"]
    flat = {name: quality[name] for name in ("keep", "grounded", "reasoning_valid", "reason")}
    flat.update(quality["scores"], task_notes=nested["task_notes"],
        memory_status=nested["memory_proposal"]["status"], memory_items=nested["memory_proposal"]["memory_items"])
    schema = review_evolution_submission_schema()
    assert set(schema["required"]) == set(flat) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert all(schema["properties"][name]["type"] != "object" for name in schema["properties"])
    assert schema["properties"]["memory_items"]["items"]["additionalProperties"] is False
    assert all(key not in repr(schema) for key in ("minLength", "maxLength", "minItems", "maxItems"))
    unchanged = deepcopy(flat)
    assert parse_review_evolution_submission(flat) == parse_review_evolution(nested)
    assert flat == unchanged
    with pytest.raises(ValueError, match="invalid_review_evolution_submission"):
        parse_review_evolution_submission({key: value for key, value in flat.items() if key != "reason"})
    with pytest.raises(ValueError, match="invalid_review_evolution_submission"):
        parse_review_evolution_submission({**flat, "verdict": quality})


@pytest.mark.parametrize("change", ["extra", "wrong_notes", "markdown_wire", "domain_label", "false_no_update"])
def test_invalid_wire_cannot_silently_become_quality_or_memory_success(change):
    value = wire()
    if change == "extra":
        value["needs_revision"] = True
    elif change == "wrong_notes":
        value["task_notes"] = "one note"
    elif change == "markdown_wire":
        value["memory_proposal"]["memory_items"] = ["# Memory Item model-formatted"]
    elif change == "domain_label":
        value["memory_proposal"]["memory_items"][0]["context_types"] = ["clinical"]
    else:
        value["memory_proposal"]["status"] = "no_update"
    with pytest.raises(ValueError):
        parse_review_evolution(value)


def original_case():
    original = {"id": "first", "source_id": "source", "status": "eligible",
        "qa_contract": {"question": "怎样判断分类任务？", "guidance": "核对给出的定义。"},
        "provided_source_context": {"scope": "selected_lines", "text_sha256": digest("small limited label space")},
        "messages": [{"role": "user", "content": "怎样判断分类任务？"},
            {"role": "assistant", "content": "首稿。", "reasoning_content": "首稿完整私有推理。"}]}
    return original, {"messages": deepcopy(original["messages"])}, {"mode": "auto", "score_threshold": .8, "max_rounds": 0}


def test_quality_summary_counts_first_pass_and_never_grades_an_optimized_current_draft():
    original, payload, config = original_case()
    good = {"status": "observed", **parse_review_evolution(wire(keep=True))}
    first = finalize_native_sft_review_evolution(original, good, {"status": "no_update"},
        config=config, original_payload=payload, action="accepted")
    assert native_sft_review_evolution_summary(iter([first])) == {
        "policy": "single_review_with_optional_native_optimization", "assessed_first_drafts": 1,
        "accepted_first_drafts": 1, "optimized_drafts": 0, "current_assessed": 1}
    rejected = {"status": "observed", **parse_review_evolution(wire())}
    revised = deepcopy(original)
    revised["messages"][-1]["content"] = "保留用户实际给出的判断条件。"
    optimized = finalize_native_sft_review_evolution(original, rejected, {"status": "proposed"},
        config=config, original_payload=payload, action="revised", revised=revised,
        revised_payload={"messages": revised["messages"]}, task_identity={"original": "same", "current": "same"},
        derived_snapshot=review_evolution_step_snapshot(rejected, {}))
    unavailable = finalize_native_sft_review_evolution(original, {"status": "invalid"}, {"status": "not_called"},
        config=config, original_payload=payload, action="not_optimized")
    legacy = {"status": "eligible", "review_repair": {"status": "released_advisory"}}
    assert native_sft_review_evolution_summary(iter([first, optimized, unavailable, legacy])) == {
        "policy": "single_review_with_optional_native_optimization", "assessed_first_drafts": 2,
        "accepted_first_drafts": 1, "optimized_drafts": 1, "current_assessed": 1}


def test_first_pass_is_terminal_but_replacement_has_no_inherited_grade_and_exact_archive():
    original, payload, config = original_case()
    good = {"status": "observed", **parse_review_evolution(wire(keep=True))}
    first = finalize_native_sft_review_evolution(original, good, {"status": "proposed"},
        config=config, original_payload=payload, action="accepted")
    assert first["semantic_status"] == "model_assessed"
    assert native_sft_review_evolution_release_valid(first, payload, config)
    observation = {"status": "observed", **parse_review_evolution(wire())}
    recipe = {"agent_experience": {"sha256": "frozen-base"}}
    before = deepcopy(recipe)
    snapshot = review_evolution_step_snapshot(observation, recipe)
    revised = deepcopy(original)
    revised.update(id="second", parent_id="first", judge={"keep": True}, score=1)
    revised["messages"][-1] = {"role": "assistant", "content": "按实际标签空间判断。", "reasoning_content": "新的完整原生推理。"}
    current = {"messages": deepcopy(revised["messages"])}
    final = finalize_native_sft_review_evolution(original, observation, {"status": "proposed"}, config=config,
        original_payload=payload, action="revised", revised=revised, revised_payload=current,
        task_identity={"original": "same-task", "current": "same-task"}, derived_snapshot=snapshot)
    assert final["review_repair"]["status"] == final["semantic_status"] == "not_assessed"
    assert final["review_repair"]["score"] is None
    assert all(key not in final for key in ("judge", "score", "scores", "review_observation"))
    assert final["reviewed_first_draft"]["record"] == original
    assert "首稿完整私有推理" in final["reviewed_first_draft"]["record"]["messages"][-1]["reasoning_content"]
    assert recipe == before and "<proposed_general_experience>" in snapshot["system_instruction"]
    assert "<review_task_notes>" in snapshot["system_instruction"]
    assert native_sft_review_evolution_release_valid(final, current, config)
    tampered = deepcopy(final)
    tampered["qa_contract"]["question"] = "改了任务"
    assert not native_sft_review_evolution_release_valid(tampered, current, config)
    from lib.contexts.workflows.domain.native_generation import native_sft_to_cot_record
    projected = native_sft_to_cot_record(final)
    cot_payload = {key: projected[key] for key in ("question", "reasoning", "answer")}
    projected["review_repair"].update(candidate_sha256=digest(cot_payload), derived_from={"target": "sft", "candidate_id": "second"})
    assert native_sft_review_evolution_release_valid(projected, cot_payload, config)


def test_same_review_proposal_storage_replays_without_model_or_activation_and_no_update_has_no_edition(tmp_path):
    from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
    from lib.contexts.workflows.infrastructure.review_memory_proposals import record_review_memory_proposal
    repository = ExperienceEvolutionRepository(tmp_path)
    calls = []
    bank = SimpleNamespace(index_edition=lambda edition: calls.append(edition) or {"status": "unavailable"})
    proposal = parse_review_evolution(wire())["memory_proposal"]
    options = {"source_receipt": {"review_observation_sha256": "recorded-review"},
        "base_text": "原有通用经验保留。", "episode": {"id": "first", "review": "recorded"}}
    first = record_review_memory_proposal(repository, bank, proposal, **options)
    assert record_review_memory_proposal(repository, bank, proposal, **options) == first
    assert first["new_model_calls"] == 0 and first["adopted"] is False and first["index"]["status"] == "unavailable"
    assert len(calls) == len(repository.list_versions("reasoning")) == 1
    edition = repository.get_version(first["edition_id"])
    assert edition["upstream_memory_items"] == proposal["memory_items"]
    assert edition["text"] == options["base_text"] + "\n\n" + "\n\n".join(proposal["memory_items"])
    assert edition["base_text"] == options["base_text"] and edition["quality"] == "model_proposed_not_verified"
    result = record_review_memory_proposal(repository, bank, {"status": "no_update", "memory_items": []}, **options)
    assert result["status"] == "no_update" and "edition_id" not in result
    assert len(repository.list_versions("reasoning")) == 1
    assert not (tmp_path / "reasoning-bank-active.json").exists()


def test_adopting_additive_review_edition_preserves_exact_custom_base_with_or_without_styles(tmp_path):
    from hashlib import sha256
    from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
    from lib.bootstrap.reasoning_bank import reasoning_bank_application
    from lib.bootstrap.contextual_reasoning_memory import contextual_reasoning_memory
    from lib.contexts.workflows.domain.dialogue_style_context import context_system_message
    from lib.contexts.workflows.domain.reasoning_bank import cumulative_review_base_text
    from lib.contexts.workflows.infrastructure.review_memory_proposals import record_review_memory_proposal
    from lib.domain.agent_experience import validate_agent_experience

    class Embedding:
        identity = "additive-review-protocol-fixture"
        def embed(self, texts):
            return [[1., 0.] for _ in texts]
        def embed_query(self, query, instruction):
            return [1., 0.]

    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    base = (" \n用户明确保留的通用规则：先确认当前任务的真实约束。\n\n"
        "<retrieved_procedural_experience>\n旧版已采用的经验仍然适用。\n</retrieved_procedural_experience>\n")
    proposal = parse_review_evolution(wire())["memory_proposal"]
    receipt = record_review_memory_proposal(app.repository, app.bank, proposal,
        source_receipt={"review_observation_sha256": "one-recorded-review"}, base_text=base, episode={"id": "first"})
    edition = app.repository.get_version(receipt["edition_id"])
    assert cumulative_review_base_text(edition) == base
    assert edition["base_text_sha256"] == sha256(base.encode("utf-8")).hexdigest()
    assert edition["upstream_memory_items"] == proposal["memory_items"]
    assert app.activate("reasoning", edition["id"])["status"] == "active"

    adopted = validate_agent_experience(None)
    adopted["reasoning"] = {"enabled": True, "text": edition["text"]}
    unchanged = deepcopy(adopted)
    without_styles = app.freeze(adopted, "Relevant procedural guidance")
    assert without_styles["config"]["reasoning"]["text"] == edition["text"]
    with_styles = contextual_reasoning_memory(root=tmp_path, user_key="fixture", embedder=Embedding(),
        reasoning_text=edition["text"])
    assert with_styles["base_projection"]["base_text"] == base
    message, _ = context_system_message({"dialogue_style_context_version": 1,
        "dialogue_style_context_catalog": with_styles["catalog"]}, [])
    assert proposal["memory_items"][0] in message["content"]
    assert adopted == unchanged
    assert app.repository.get_version(edition["id"]) == edition
    with pytest.raises(ValueError, match="review_memory_proposal_base_changed"):
        cumulative_review_base_text({**edition, "base_text": "changed base"})
