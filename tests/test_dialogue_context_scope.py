"""Real failed conversations replayed at request boundaries, never as quality passes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.bootstrap.workflow_execution import workflow_execution
from lib.contexts.workflows.application.native_generation import generate_native_turn
from lib.contexts.workflows.contracts import training_record
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.infrastructure.generation_adapter import CheckpointedGenerationAdapter
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_separated_dialogue_generation import task_payload, tool_payload


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every provider response is a recorded failure or an injected response."""
    yield


def recorded_context():
    return json.loads((Path(__file__).parent / "fixtures/reasoning_agent/adaptive_dialogue_context.json")
                     .read_text(encoding="utf8"))


def make_worker(tmp_path, author, *, version=1, stage="sft", native=True, brief=None, separated=False,
                experience=None, instruction_scope=1, constraint_version=1):
    output = tmp_path / "out"
    options = {"node_generation": {stage: {"enabled": True, "answer_mode": "native_joint"}}} if native else {}
    if experience is not None:
        options["agent_experience"] = {"reasoning": {"enabled": True, "text": experience}}
    rid = engine.create_run(output, brief=brief or recorded_context()["english"]["unit"]["text"],
        targets=[stage], qa_director={"enabled": True, "planning_mode": "adaptive", "question_separation": separated}, **options)
    path = engine.run_path(output, rid)
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["dialogue_context_scope_version"] == 1
    if version is None:
        # Represent an already-frozen old task, rather than changing defaults on resume.
        recipe.pop("dialogue_context_scope_version")
    if instruction_scope is None:
        recipe.pop("native_instruction_scope_version")
    if constraint_version is None:
        from lib.prompts import get, render
        recipe.pop("dialogue_constraint_provenance_version")
        prompt = get("workflow.qa_director", "1.14.0")
        recipe["prompts"][prompt.id] = {"version": prompt.version, "sha256": engine.digest(prompt.template)}
        recipe["node_prompt_templates"]["director"][prompt.id] = render(prompt)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "recipe.json", recipe)
    atomic_json(path / "state.json", state)
    worker = engine.Workflow(output, rid, tmp_path, generator=author)
    worker.stage = stage
    session = workflow_execution(worker)._session
    with session.exclusive():
        session.verify_pinned_inputs()
    return worker


class RecordedNativeAuthor:
    model = "offline-recorded-v4.1-flash-response"
    reasoning = {"mode": "enabled"}

    def __init__(self, response):
        self.response, self.calls = response, []

    def chat(self, messages, **options):
        self.calls.append(deepcopy(messages))
        options["response_details"].update(api_format="chat", reasoning_content=self.response["reasoning"])
        return self.response["answer"]


@pytest.mark.parametrize("source_kind,version,disclosed", [
    ("synthetic_brief", 1, False), ("synthetic_brief", None, False),
    ("document", 1, False), ("human_brief", 1, False), ("synthetic_brief", 1, True),
])
@pytest.mark.parametrize("instruction_scope", [None, 1])
def test_native_actual_request_excludes_only_private_scenario_and_preserves_failed_trace(
        tmp_path, source_kind, version, disclosed, instruction_scope):
    fixture = recorded_context()["english"]
    unit, messages = deepcopy(fixture["unit"]), deepcopy(fixture["messages"])
    if source_kind == "document":
        unit.update(kind="document", synthetic=False)
    elif source_kind == "human_brief":
        unit["human_provided"] = True
    if disclosed:
        messages[-1]["content"] += " I have a laptop and an API account."
    author = RecordedNativeAuthor(fixture["response"])
    worker = make_worker(tmp_path, author, version=version, instruction_scope=instruction_scope)
    before = deepcopy((unit, messages, worker.recipe))

    def generate(runtime):
        return generate_native_turn(CheckpointedGenerationAdapter(runtime), runtime.recipe,
            stage="sft", unit=unit, messages=messages, style={}, source_text=unit["text"],
            source_scope="available_context")

    row = generate(worker)
    sent = author.calls[0]
    assert sent[-len(messages):] == messages
    private = source_kind == "synthetic_brief" and version == 1
    if private:
        # These assertions concern data visibility, not whether the recorded model understood it.
        assert unit["text"] not in json.dumps(sent, ensure_ascii=False)
        assert unit["qa_contract"]["guidance"] not in json.dumps(sent, ensure_ascii=False)
        assert "<knowledge>" not in sent[0]["content"]
    else:
        knowledge = json.loads(sent[0]["content"].split("<knowledge>\n", 1)[1].split("\n</knowledge>", 1)[0])
        assert knowledge == {"text": unit["text"]}
    source_text = "" if private else unit["text"]
    assert row["provided_source_context"]["scope"] == ("learner_dialogue_only" if private else "available_context")
    assert row["provided_source_context"]["text_chars"] == len(source_text)
    assert row["provided_source_context"]["text_sha256"] == dialogue_digest(source_text)
    assert row["source_context"] == unit
    assert row["qa_contract"] == unit["qa_contract"]
    assert row["reasoning"] == [fixture["response"]["reasoning"]]
    assert row["answer"] == fixture["response"]["answer"]
    assert row["cot_review_policy"]["semantic_status"] == "not_assessed"
    exported = training_record("cot", row)
    assert exported == {"question": row["question"], "reasoning": row["reasoning"], "answer": row["answer"]}
    assert (unit, messages, worker.recipe) == before
    resumed = engine.Workflow(tmp_path / "out", worker.path.name, tmp_path, generator=author)
    resumed.stage = "sft"
    assert generate(resumed) == row and len(author.calls) == 1
    if private and not disclosed:
        # The reviewer needs the author's actual visibility while retaining
        # the private source for audit. A failed replay is never labelled good.
        from lib.contexts.workflows.infrastructure.dialogue_adapter import CheckpointedDialogueAdapter
        contexts = []

        def recorded_review(key, context, candidate, **options):
            contexts.append(deepcopy(context))
            assert candidate[-1]["reasoning_content"] == fixture["response"]["reasoning"]
            return {"keep": False, "grounded": False, "reasoning_valid": False, "correctness": 1,
                    "scores": {key: 1 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                    "reason": "Recorded failure retained for transport verification only."}

        worker.judge_answer = recorded_review
        worker.qa_contract_check = lambda *args, **options: {"keep": False, "adherence": 1, "reason": "Not assessed."}
        worker.style_check = worker.qa_contract_check
        reviewed, = worker.sft(unit)
        assert reviewed["status"] == "quarantined"
        assert reviewed["reasoning"] == row["reasoning"] and reviewed["answer"] == row["answer"]
        if instruction_scope == 1:
            # The historical trace includes an unsupported verbosity setting.
            # The new local check stops it before paying for another assessment.
            assert not contexts
            findings = reviewed["native_generation"]["instruction_scope_evidence"]
            assert any(finding["classification"] == "unsupported_runtime_setting"
                       and finding["disposition"] == "block" for finding in findings)
        else:
            assert len(contexts) == 1
            assert contexts[0]["answer_context_scope"]["source_scope"] == "learner_dialogue_only"
            assert contexts[0]["provided_source_context"] == row["provided_source_context"]
            assert contexts[0]["qa_contract"] == unit["qa_contract"]
        dialogue_context = CheckpointedDialogueAdapter(worker).prepare_source(unit, None).context
        assert dialogue_context["answer_context_scope"]["source_scope"] == "learner_dialogue_only"
        if contexts:
            assert dialogue_context["answer_context_scope"] == contexts[0]["answer_context_scope"]
        assert dialogue_context["qa_contract"] == unit["qa_contract"]
        assert len(author.calls) == 1


@pytest.mark.parametrize("prompt_id", ["workflow.sft_answer", "workflow.sft_directed", "workflow.multiturn_assistant"])
@pytest.mark.parametrize("version", [None, 1])
def test_non_native_actual_adapter_cannot_restore_private_scene_as_fallback_memory(tmp_path, prompt_id, version):
    from tests.test_source_memory_requests import CapturingGenerator
    fixture = recorded_context()["english"]
    unit, messages = deepcopy(fixture["unit"]), deepcopy(fixture["messages"])
    author = CapturingGenerator(values=[{"answer": "Offline response only.", "quotes": []}])
    stage = "multiturn" if prompt_id == "workflow.multiturn_assistant" else "sft"
    worker = make_worker(tmp_path, author, version=version, stage=stage, native=False,
                         separated=prompt_id != "workflow.sft_directed")
    data = {"source_context": unit, "qa_contract": unit["qa_contract"], "learner_messages": messages,
            "requirement": unit["text"], "answer_rules": "Keep actual user conditions explicit."}
    before = deepcopy(data)
    worker.ask(["recorded-context", prompt_id], "generation", prompt_id, data)
    sent = author.calls[0]
    dispatch = tool_payload(sent, "dialogue_dispatch")
    assert sent[-len(messages):] == messages and data == before
    if version == 1:
        assert tool_payload(sent, "source_memory") is None
        assert dispatch["answer_context_scope"]["source_scope"] == "learner_dialogue_only"
        assert set(dispatch) <= {"qa_type", "answer_policy", "user_language", "fixed_question", "answer_context_scope"}
        assert unit["text"] not in json.dumps(sent, ensure_ascii=False)
    else:
        assert tool_payload(sent, "source_memory")["teacher_context_pack"][0]["materials"][0]["text"] == unit["text"]
        assert dispatch["requirement"] == unit["text"]
        assert dispatch["guidance"] == unit["qa_contract"]["guidance"]
    assert len(author.calls) == 1


@pytest.mark.parametrize("version", [None, 1])
@pytest.mark.parametrize("constraint_version", [None, 1])
def test_director_receives_original_examples_and_fallible_state_without_program_certification(
        tmp_path, version, constraint_version):
    fixture = recorded_context()["chinese"]
    payload = fixture["payload"]
    response = deepcopy(fixture["response"])
    if constraint_version == 1:
        # Adapt only the external protocol shape. The original failed summary
        # remains in the fixture and is still replayed as fallible input state.
        response["dialogue_state"].pop("active_constraints")
        response["user_constraint_refs"] = [1]

    class RecordedDirector:
        model = "offline-director-response"

        def __init__(self):
            self.calls = []

        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            return json.dumps(response, ensure_ascii=False)

    author = RecordedDirector()
    worker = make_worker(tmp_path, author, version=version, stage="multiturn", native=False,
                         brief=payload["teacher_evidence"], constraint_version=constraint_version)
    unit = {"id": "recorded-chinese-state", "source_id": "brief", "kind": "brief", "synthetic": True,
            "text": payload["teacher_evidence"], "qa_contract": deepcopy(payload["qa_contract"])}
    # Feed a known misleading summary from the previous recorded response back
    # as advisory state. Do not pretend this replay fixes the model's semantics.
    previous = deepcopy(fixture["response"]["dialogue_state"])
    messages = deepcopy(payload["dialogue_messages"])
    before = deepcopy((unit, previous, messages))
    step = worker.next_dialogue_step(unit, messages, previous)
    actual = task_payload(author.calls[0])
    assert actual["dialogue_messages"] == messages
    assert actual["previous_dialogue_state"] == previous
    assert actual["teacher_evidence"] == unit["text"]
    if version == 1:
        authority = actual["context_authority"]
        assert authority["previous_dialogue_state"] == "fallible_model_summary_not_verified_facts"
        assert authority["assistant_examples"] == "hypothetical_until_the_user_adopts_the_specific_condition"
        assert authority["completion"] == "draft_delivery_is_not_user_acceptance_or_real_world_execution"
    else:
        assert "context_authority" not in actual
    if constraint_version == 1:
        assert actual["user_message_indices"] == [1]
        assert step["dialogue_state"]["active_constraints"] == [messages[1]["content"]]
        assert step["constraint_provenance"]["sources"][0]["message_index"] == 1
        assert step["constraint_provenance"]["semantic_status"] == "not_certified"
    else:
        assert "user_message_indices" not in actual
        assert step["dialogue_state"]["active_constraints"] == previous["active_constraints"]
        assert "constraint_provenance" not in step
    assert (unit, previous, messages) == before and len(author.calls) == 1


@pytest.mark.parametrize("system_case,version", [
    ("exact", 1), ("exact", None), ("changed", 1), ("extra_field", 1), ("wrong_position", 1),
])
def test_whole_review_certifies_only_frozen_public_system_and_preserves_old_verdict(tmp_path, system_case, version):
    from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
    from lib.contexts.workflows.domain.dialogue import DialogueProgress
    from lib.contexts.workflows.domain.public_experience import with_public_experience
    from lib.contexts.workflows.infrastructure.dialogue_adapter import CheckpointedDialogueAdapter
    from tests.test_source_memory_requests import CapturingGenerator

    fixture = recorded_context()["english"]
    experience = "Public working principles: preserve uncertainty and answer the actual request."
    author = RecordedNativeAuthor(fixture["response"])
    worker = make_worker(tmp_path, author, version=version, stage="multiturn", experience=experience)
    messages = with_public_experience([
        *deepcopy(fixture["messages"]),
        {"role": "assistant", "content": fixture["response"]["answer"],
         "reasoning_content": fixture["response"]["reasoning"]},
        {"role": "user", "content": "Can we avoid the live API part?"},
        {"role": "assistant", "content": "Use a prepared example instead.",
         "reasoning_content": "The user changed the implementation constraint; a prepared example avoids a live call."},
    ], experience)
    if system_case == "changed":
        messages[0]["content"] += " modified"
    elif system_case == "extra_field":
        messages[0]["name"] = "unregistered"
    elif system_case == "wrong_position":
        messages.insert(0, {"role": "system", "content": "Unknown system message."})
    original = deepcopy(messages)
    old_verdict = {"keep": False, "grounded": True, "reasoning_valid": True, "correctness": 5,
        "scores": {"correctness": 5, "reasoning": 5, "grounding": 5, "instruction": 2, "safety": 2},
        "reason": "Recorded original rejection; this fixture does not certify the answer."}
    new_verdict = {**deepcopy(old_verdict), "reason": "Provenance is not a content-quality approval."}
    reviewer = CapturingGenerator(values=[old_verdict, new_verdict])
    worker.jev = reviewer
    unit = deepcopy(fixture["unit"])
    old_key = [unit["id"], "multiturn_consistency"]
    old = worker.judge_answer(old_key, {"original_review_context": True}, messages,
                              prompt_id="workflow.multiturn_consistency")
    dialogue = DialogueProgress(source_context={"task": unit["text"]}, messages=messages)
    use_case = GenerateMultiturnDialogue(CheckpointedDialogueAdapter(worker), worker.recipe)
    row, = use_case._assess_complete(unit, dialogue)
    certified = system_case == "exact" and version == 1
    assert len(reviewer.calls) == (2 if certified else 1)
    if certified:
        payload = task_payload(reviewer.calls[-1])
        assert payload["answer"] == original
        provenance = payload["context"]["learner_system_provenance"]
        assert provenance == {"version": 1, "message_index": 0,
            "message_sha256": dialogue_digest(original[0]), "origin": "program_frozen_public_experience",
            "visibility": "learner_system_message_exported_verbatim", "content_quality": "not_assessed",
            "authority": "message_provenance_only_not_quality_approval"}
        expected_key = [*old_key, "public_experience_context_v1",
                        dialogue_digest({"context": payload["context"], "messages": original})]
        assert worker.checkpoint_has(["call", expected_key])
        assert row["consistency"] == new_verdict
    else:
        assert row["consistency"] == old
    assert row["status"] == "quarantined" and row["messages"] == original and messages == original
    assert use_case._assess_complete(unit, dialogue) == [row]
    assert worker.judge_answer(old_key, {}, messages, prompt_id="workflow.multiturn_consistency") == old
    assert len(reviewer.calls) == (2 if certified else 1) and not author.calls
