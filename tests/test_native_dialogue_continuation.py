"""A failed native first answer is repaired once, then uses the real dialogue loop."""
from copy import deepcopy
import pytest

from lib.contexts.workflows.application.continue_native_dialogue import ContinueNativeDialogue
from lib.contexts.workflows.domain.dialogue import DialogueSource, dialogue_digest
from lib.contexts.workflows.domain.native_dialogue_continuation import native_dialogue_continuation_input
from lib.contexts.review.application.review_candidate import ReviewCandidate


def parent_and_recipe():
    contract = {"question": "How can I make a small PDF demo?", "qa_type": "closed_book",
        "answer_policy": "answer", "guidance": "Give a conditional first draft.",
        "dialogue_design": {"user_intent": "Prepare a demo"}}
    source = {"id": "source-task", "source_id": "scene", "family_id": "family",
        "kind": "brief", "synthetic": True, "text": "A person prepares a small demo.",
        "qa_contract": contract}
    prompt = [{"role": "user", "content": contract["question"]}]
    old = {"role": "assistant", "content": "It will definitely work.",
        "reasoning_content": "The desired oververbosity is 5, so I can assert success."}
    record = {"id": "old", "source_id": "scene", "status": "quarantined", "failed_turn": 1,
        "reason": "native_generation_instruction_leakage", "native_generation": {"version": 1},
        "source_context": source, "question": prompt, "messages": [*prompt, old],
        "turn_reviews": [], "accepted_prefix": []}
    item = {"target": "multiturn", "record": record,
        "instruction": "Do not invent a requested length or claim extraction will succeed. Keep conditional checks.",
        "revision_context": {"parent_run_id": "parent", "content_sha256": dialogue_digest(record)}}
    recipe = {"version": 42, "conversation_turns": 2, "native_generation_version": 1,
        "native_dialogue_continuation_version": 1, "dialogue_context_scope_version": 1,
        "node_generation": {"multiturn": {"enabled": True, "answer_mode": "native_joint"}},
        "qa_director": {"planning_mode": "adaptive", "question_rules": "", "answer_rules": ""},
        "review_repair": {"mode": "auto", "max_rounds": 2, "score_threshold": .8}}
    return item, recipe


def assessment(keep=True):
    return {"keep": keep, "grounded": True, "reasoning_valid": True,
        "correctness": 5 if keep else 1, "scores": {"correctness": 5 if keep else 1},
        "reason": "Checks actual conditions."}


class RecordedPort:
    def __init__(self, *, native_failure=False, review_failure=False):
        self.requests, self.assessments, self.styles, self.contracts, self.next_steps = [], [], [], [], []
        self.native_failure, self.review_failure = native_failure, review_failure

    def prepare_source(self, unit, style):
        return DialogueSource({"kind": unit["kind"], "qa_contract": deepcopy(unit["qa_contract"])})

    def generation_style(self, *_):
        return {"instruction": "Keep warranted conditions."}

    def separated_questions(self):
        return True

    def learner_prompt(self, contract):
        return contract["question"]

    def source_evidence(self, source):
        return source.get("text", "")

    def request(self, key, role, prompt_id, task, **options):
        self.requests.append(deepcopy(task))
        assert prompt_id == "workflow.cot_native_joint"
        return {"reasoning": "" if self.native_failure else
            "I need to distinguish the proposed setup from an observed result. Extraction must be checked before the demo.",
            "answer": "Check a small extracted passage first; use a prepared excerpt if extraction fails."}

    def next_dialogue_step(self, unit, messages, state, **options):
        self.next_steps.append(deepcopy(messages))
        return {"continue": True, "user_message": "What if extraction fails?",
            "reason": "Explore the fallback", "dialogue_state": deepcopy(state)}

    def assess(self, key, context, message, **options):
        self.assessments.append((deepcopy(context), deepcopy(message)))
        return assessment(not self.review_failure)

    def check_style(self, *args, **options):
        self.styles.append(args)
        return {"keep": True, "adherence": 5, "reason": "Conditional reasoning retained."}

    def check_contract(self, *args, **options):
        self.contracts.append(args)
        return {"keep": True, "adherence": 5, "reason": "Answers the actual user."}

    def reject(self, unit, reason):
        return {"id": unit["id"], "source_id": unit["source_id"], "status": "quarantined", "reason": reason}


def test_first_answer_repair_preserves_question_source_and_native_history_then_continues():
    item, recipe = parent_and_recipe()
    frozen = deepcopy(item)
    port = RecordedPort()
    row, = ContinueNativeDialogue(port, recipe, "child").execute(item)
    assert row["status"] == "eligible" and row["turn_count"] == 2
    assert len(port.requests) == 2 and len(port.next_steps) == 1
    assert len(port.assessments) == 3 and len(port.styles) == len(port.contracts) == 2
    first = port.requests[0]
    assert first["prompt"] == item["record"]["question"]
    assert first["previous_candidate"] == item["record"]["messages"][-1]
    assert first["revision_feedback"] == item["instruction"]
    assert "previous_candidate" not in port.requests[1]
    assert len(row["messages"]) == 4 and all(message.get("reasoning_content") for message in row["messages"] if message["role"] == "assistant")
    assert row["source_context"] == item["record"]["source_context"]
    assert row["native_dialogue_continuation"]["original"] == item["record"] and item == frozen


@pytest.mark.parametrize("failure", ["native", "review"])
def test_a_failed_correction_keeps_full_draft_and_does_not_purchase_second_attempt(failure):
    item, recipe = parent_and_recipe()
    port = RecordedPort(native_failure=failure == "native", review_failure=failure == "review")
    row, = ContinueNativeDialogue(port, recipe, "child").execute(item)
    assert row["status"] == "quarantined" and len(port.requests) == 1 and not port.next_steps
    draft = row.get("messages") or row["failed_generation"]["messages"]
    assert draft[-1]["content"] and row["native_dialogue_continuation"]["original"] == item["record"]


def test_incomplete_or_accepted_history_cannot_be_promoted_to_supported_checkpoint():
    item, _ = parent_and_recipe()
    record = deepcopy(item["record"])
    record["source_context"]["qa_contract"]["question"] = "A different user question"
    with pytest.raises(ValueError, match="incomplete_parent"):
        native_dialogue_continuation_input(record, item["instruction"])
    record = deepcopy(item["record"])
    record["accepted_prefix"] = [{"role": "assistant", "content": "Already accepted"}]
    with pytest.raises(ValueError, match="unsupported_checkpoint"):
        native_dialogue_continuation_input(record, item["instruction"])


def test_review_dispatch_requires_authentication_and_reuses_dialogue_assessment():
    item, recipe = parent_and_recipe()
    class ReviewPort:
        authenticated_revision = False
        def continue_native_dialogue(self, selected):
            return ContinueNativeDialogue(RecordedPort(), recipe, "child").execute(selected)
        def payload(self, target, row):
            return {"messages": row.get("messages", [])}
        def score(self, *_):
            raise AssertionError("Duplicate paid assessment")
    port = ReviewPort()
    with pytest.raises(ValueError, match="parent_not_authenticated"):
        ReviewCandidate(port, recipe).execute(item)
    port.authenticated_revision = True
    row, = ReviewCandidate(port, recipe).execute(item)
    assert row["review_repair"]["status"] == "accepted" and row["review_repair"]["attempts"] == 1


class NativeToolRepairPort(RecordedPort):
    def __init__(self):
        super().__init__()
        self.tool_requests = []

    def check_reasoning_cancel(self):
        pass

    def turn(self, key, author_session, messages, tools, *, final):
        self.tool_requests.append(deepcopy({"session": author_session, "messages": messages, "tools": tools}))
        assistant = {"role": "assistant", "content": "Check a short extracted passage first; use a prepared excerpt if needed.",
            "reasoning_content": "A fallback is possible, but that does not establish extraction success."}
        if not final:
            assistant.update(content="", reasoning_content="I should consider whether this claim needs a condition.",
                tool_calls=[{"id": "recorded-feedback-" + str(len(self.tool_requests)), "type": "function",
                    "function": {"name": "read_review_feedback", "arguments": "{}"}}])
        return {"assistant": assistant, "response_details": {"api_format": "chat",
            "finish_reason": "stop" if final else "tool_calls", "checkpoint": "response-" + str(len(self.tool_requests))}}

    def continue_native_feedback(self, key, author_session, assistant, *, findings):
        from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
        return ContinueNativeFeedback(self, guidance_version=getattr(self, "guidance_version", 1)).execute(
            key, author_session, assistant, findings=findings)


def tool_repair_parent_and_recipe():
    item, recipe = parent_and_recipe()
    recipe["native_revision_context_version"] = 2
    item["record"]["native_generation"]["author_session"] = {
        "version": 1, "author": "authenticated-author", "stage": "multiturn", "role": "generation",
        "request_options": {"max_tokens": 32768, "temperature": .2}, "tools": [],
        "messages": deepcopy(item["record"]["question"])}
    return item, recipe


@pytest.mark.parametrize("guidance_version", [1, 2, 3, 4, 5, 6])
def test_tool_repair_can_repeat_from_exact_final_author_session_without_new_review(guidance_version):
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = guidance_version
    author = NativeToolRepairPort()
    author.guidance_version = guidance_version

    class ReviewPort:
        authenticated_revision = True
        def continue_native_dialogue(self, selected):
            return ContinueNativeDialogue(author, recipe, "child").execute(selected)
        def payload(self, target, row):
            return {"messages": row.get("messages", [])}

    first, = ReviewCandidate(ReviewPort(), recipe).execute(item)
    frozen = deepcopy(first)
    continuation = native_dialogue_continuation_input(first, "Keep the original uncertainty.")
    native = first["native_generation"]
    assert continuation["previous_candidate"] == native["revised_assistant"]
    assert continuation["previous_candidate"]["reasoning_content"] != first["messages"][-1]["reasoning_content"]
    assert continuation["author_session"]["messages"] == author.tool_requests[-1]["messages"]
    assert native["response_details"] == native["tool_feedback"]["response_details"]
    assert first["reasoning_call"] == native["response_details"]["checkpoint"]
    second, = ReviewCandidate(ReviewPort(), recipe).execute({**item, "record": first,
        "instruction": "Keep the original uncertainty.",
        "revision_context": {"parent_run_id": "child", "content_sha256": dialogue_digest(first)}})
    assert first == frozen
    assert author.tool_requests[2]["messages"][:len(native["continuation_session"]["messages"])] == native["continuation_session"]["messages"]
    assert author.tool_requests[2]["messages"][-2] == native["revised_assistant"]
    assert len(author.tool_requests) == 4
    assert not author.requests and not author.assessments and not author.styles and not author.contracts and not author.next_steps
    for row in (first, second):
        assert row["status"] == "quarantined" and row["failed_turn"] == 1
        assert row["native_dialogue_continuation"]["assessments"] == "not_assessed"
        assert row["review_repair"]["status"] == row["review_repair"]["assessment"] == "not_assessed"
        assert row["review_repair"]["history"][0]["action"] == "repair_first_native_answer_with_tool_feedback"


@pytest.mark.parametrize("guidance_version", [5, 6])
def test_reused_feedback_record_can_repeat_and_continue_through_the_normal_repair_route(guidance_version):
    from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = 4
    parent_port = NativeToolRepairPort()
    parent_port.guidance_version = 4
    parent, = ContinueNativeDialogue(parent_port, recipe, "v4-parent").execute(item)
    parent_receipt = parent["native_generation"]["tool_feedback"]

    class ReusePort(NativeToolRepairPort):
        def __init__(self, previous):
            super().__init__()
            self.previous = previous

        def continue_native_feedback(self, key, author_session, assistant, *, findings):
            return ContinueNativeFeedback(self, guidance_version=guidance_version).execute_from_feedback(
                key, author_session, assistant, findings=findings, previous_receipt=self.previous)

    recipe["native_feedback_guidance_version"] = guidance_version
    reuse = ReusePort(parent_receipt)
    row, = ContinueNativeDialogue(reuse, recipe, "v5-child").execute(item)
    native = row["native_generation"]
    assert len(reuse.tool_requests) == 1 and not reuse.requests and not reuse.assessments
    assert native["tool_feedback"]["previous_receipt"] == parent_receipt
    continued = native_dialogue_continuation_input(row, "Keep the conditional fallback.", guidance_version=guidance_version)
    assert continued["previous_candidate"] == native["revised_assistant"]
    assert continued["author_session"] == native["continuation_session"]

    # Continue the same provider sequence so its next real call ID is unique.
    ordinary = parent_port
    ordinary.guidance_version = guidance_version
    previous_calls = len(ordinary.tool_requests)
    next_row, = ContinueNativeDialogue(ordinary, recipe, "later-repair").execute({**item, "record": row,
        "instruction": "Keep the conditional fallback."})
    assert len(ordinary.tool_requests) == previous_calls + 2 and not ordinary.assessments
    assert ordinary.tool_requests[previous_calls]["messages"][:-2] == native["continuation_session"]["messages"]
    assert ordinary.tool_requests[previous_calls]["messages"][-2] == native["revised_assistant"]
    assert native_dialogue_continuation_input(next_row, "Keep conditions.", guidance_version=guidance_version)["previous_candidate"] == (
        next_row["native_generation"]["revised_assistant"])

    again = ReusePort(native["tool_feedback"])
    again_row, = ContinueNativeDialogue(again, recipe, "v5-again").execute(item)
    assert len(again.tool_requests) == 1 and not again.assessments
    assert native_dialogue_continuation_input(again_row, "Keep conditions.", guidance_version=guidance_version)
    again_row["native_generation"]["tool_feedback"]["previous_receipt"]["previous_receipt"]["tool_feedback"]["tool_call_id"] = "forged"
    with pytest.raises(ValueError, match="feedback_provenance_mismatch"):
        ContinueNativeDialogue(again, recipe, "refused").execute({**item, "record": again_row})
    assert len(again.tool_requests) == 1


@pytest.mark.parametrize("source", ["judge", "review_repair"])
def test_saved_first_person_review_reaches_real_tool_feedback_without_another_reviewer(source):
    from lib.contexts.workflows.domain.targets import training_record
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = 2
    record = item["record"]
    old = record["messages"][-1]
    reason = "Wait, I treated an untested extraction as a success. I need to keep that condition open."
    check = assessment(False) | {"reason": reason}
    if source == "judge":
        record.update(reason="cot_native_review_rejected", judge=check,
            cot_review_policy={"semantic_status": "not_accepted"})
        record["native_generation"]["content_sha256"] = dialogue_digest([old["reasoning_content"], old["content"]])
    else:
        record["review_repair"] = {"verdict": check,
            "candidate_sha256": dialogue_digest(training_record("multiturn", record))}
    frozen = deepcopy(record)
    port = NativeToolRepairPort()
    port.guidance_version = 2
    row, = ContinueNativeDialogue(port, recipe, "first-person-child").execute(item)
    findings = row["native_generation"]["tool_feedback"]["observation"]["findings"]
    assert findings[0]["issue"] == reason
    assert findings[0]["source_ref"]["kind"] == "saved_model_review"
    assert findings[1]["issue"] == item["instruction"]
    assert findings[1]["source_ref"]["kind"] == "human_revision_instruction"
    assert len(port.tool_requests) == 2 and not port.assessments and not port.requests
    assert row["status"] == "quarantined" and record == frozen
    legacy = native_dialogue_continuation_input(record, item["instruction"])
    assert "feedback_findings" not in legacy
    # Existing feedback is sufficient; the user need not copy it into a note.
    auto_port = NativeToolRepairPort()
    auto_port.guidance_version = 2
    auto_row, = ContinueNativeDialogue(auto_port, recipe, "review-only-child").execute({**item, "instruction": ""})
    auto_findings = auto_row["native_generation"]["tool_feedback"]["observation"]["findings"]
    assert len(auto_findings) == 1 and auto_findings[0]["issue"] == reason
    assert not auto_port.assessments and len(auto_port.tool_requests) == 2
    # Stale/unbound observations cannot become the new author's suggestions.
    if source == "judge":
        record["native_generation"]["content_sha256"] = "outdated"
    else:
        record["review_repair"]["candidate_sha256"] = "outdated"
    scoped = native_dialogue_continuation_input(record, item["instruction"], guidance_version=2)
    assert [f["source_ref"]["kind"] for f in scoped["feedback_findings"]] == ["human_revision_instruction"]
    with pytest.raises(ValueError, match="native_dialogue_continuation_feedback_required"):
        native_dialogue_continuation_input(record, "", guidance_version=2)


@pytest.mark.parametrize("change", [
    lambda row: row["reasoning"].__setitem__(0, "A substituted merged trace."),
    lambda row: row["native_generation"]["revised_assistant"].update(reasoning_content="An invented raw trace."),
    lambda row: row["native_generation"]["continuation_session"]["request_options"].update(temperature=.8),
    lambda row: row["native_generation"]["tool_feedback"]["tool_feedback"].update(tool_call_id="not-a-real-call"),
    lambda row: row["native_generation"]["tool_feedback"]["reasoning_segments"][0].update(text="An invented segment."),
    lambda row: row["native_generation"]["response_details"].update(finish_reason="length"),
])
def test_repair_provenance_mismatch_stops_before_any_next_request(change):
    item, recipe = tool_repair_parent_and_recipe()
    author = NativeToolRepairPort()
    first, = ContinueNativeDialogue(author, recipe, "child").execute(item)
    change(first)
    with pytest.raises(ValueError, match="feedback_provenance_mismatch"):
        ContinueNativeDialogue(author, recipe, "next").execute({**item, "record": first})
    assert len(author.tool_requests) == 2 and not author.requests and not author.assessments


def test_revision_v2_recipe_does_not_turn_an_ordinary_later_answer_into_repair():
    from lib.contexts.workflows.application.native_generation import generate_native_turn
    item, recipe = parent_and_recipe()
    recipe["native_revision_context_version"] = 2
    port = RecordedPort()
    messages = [*item["record"]["question"], {"role": "assistant", "content": "Check a short excerpt first.",
        "reasoning_content": "The user needs a small test."}, {"role": "user", "content": "And if extraction fails?"}]
    row = generate_native_turn(port, recipe, stage="multiturn", unit=item["record"]["source_context"],
        messages=messages, style={}, source_text="", source_scope="prepared_source")
    assert len(port.requests) == 1 and not port.assessments
    assert "previous_candidate" not in port.requests[0]
    assert "tool_feedback" not in row["native_generation"]
    assert row["messages"][:-1] == messages


@pytest.mark.parametrize("reuse", [False, True])
def test_real_session_feedback_child_keeps_author_director_and_endpoint_pins(tmp_path, reuse):
    """Prepare the real persisted branch; no chat, planning or scoring is requested."""
    import json
    from pathlib import Path
    from shutil import copyfile
    from lib.infrastructure import training_workflow as engine
    from lib.infrastructure.human_sessions import FilesystemHumanSessionDriver
    from lib.contexts.review.infrastructure.automatic_adapter import AuthenticatedRepairAdapter

    settings = tmp_path / "configs"
    settings.mkdir()
    copyfile(Path(__file__).resolve().parents[1] / "configs/preferences.yaml", settings / "preferences.yaml")
    (settings / "backends.local.yaml").write_text(json.dumps({"backends": {
        "offline-author": {"base_url": "http://127.0.0.1:9/v1", "models": ["author"]},
        "offline-reviewer": {"base_url": "http://127.0.0.1:9/v1", "models": ["reviewer"]}}}), encoding="utf-8")
    author = {"backend": "offline-author", "model": "author"}
    reviewer = {"backend": "offline-reviewer", "model": "reviewer"}
    models = {"director": {"generation": author, "question": author},
        "multiturn": {"generation": author, "jev": reviewer}, "review": {"generation": author}}
    item, config = parent_and_recipe()
    item["record"]["source_id"] = dialogue_digest("scene")
    item["record"]["source_context"]["source_id"] = item["record"]["source_id"]
    output = tmp_path / "output"
    parent_id = engine.create_run(output, settings_root=tmp_path, targets=["multiturn"],
        sources=[], brief="Prepare a small local PDF demo.", node_models=models,
        node_generation=config["node_generation"], conversation_turns=2,
        qa_director={"enabled": True, "planning_mode": "adaptive"}, package_review={"enabled": False})

    class NoModelCalls:
        usage = {}
        def chat(self, *args, **kwargs):
            raise AssertionError("Preparing and authenticating a revision cannot call a model")

    parent = engine.Workflow(output, parent_id, tmp_path, generator=NoModelCalls(), jev=NoModelCalls())
    engine.atomic_json(parent.path / "input_records.json", [{**item["record"]["source_context"], "status": "ready"}])
    parent.stage = "package"
    parent.package({"multiturn": [item["record"]]})
    parent.state["status"] = "needs_attention"
    parent.save()
    original_recipe = engine.read_json(parent.path / "recipe.json")
    original_files = {str(path.relative_to(parent.path)): path.read_bytes()
                      for path in parent.path.rglob("*") if path.is_file()}

    driver = FilesystemHumanSessionDriver(tmp_path, output)
    session_id = driver.create_session(from_run_id=parent_id, review_only=True,
        **({"reuse_initial_native_response": True, "open_dialogue_question_mode": "auto"} if reuse else {}))
    view = driver.session(session_id)
    driver.configure_review_models(session_id, {"generation": reviewer, "jev": reviewer},
        expected_version=view["version"])
    view = driver.session(session_id)
    feedback = driver.save_feedback(session_id, view["rounds"][0]["id"], "multiturn", item["record"]["id"],
        instruction=item["instruction"], decision="revise", expected_version=view["version"])
    child = driver.revise_round(session_id, request_id="native-first-answer",
        selected_feedback_ids=[feedback["new_feedback_id"]], expected_version=feedback["version"])
    frozen = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert frozen["node_models"]["multiturn"] == original_recipe["node_models"]["multiturn"]
    assert frozen["node_models"]["director"] == original_recipe["node_models"]["director"]
    assert frozen["node_models"]["review"]["jev"] == original_recipe["node_models"]["multiturn"]["jev"]
    assert frozen["node_models"]["review"]["generation"]["backend"] == "offline-reviewer"
    assert frozen["qa_director"]["enabled"]
    if reuse:
        assert frozen["native_dialogue_progress_version"] == 2
        assert frozen["reuse_initial_native_response"] is True
        assert frozen["open_dialogue_question_mode"] == "auto"
    for stage in ("director", "multiturn"):
        assert frozen["endpoint_pins"][stage] == original_recipe["endpoint_pins"][stage]
    worker = engine.Workflow(output, child["run_id"], tmp_path, generator=NoModelCalls(), jev=NoModelCalls())
    AuthenticatedRepairAdapter(worker).authenticate(frozen["repair_inputs"])
    assert not worker.state["usage"]
    # Parent authentication may add its disposable lookup index. Every file
    # that already existed, including recipe and artifacts, remains exact.
    assert original_files == {name: (parent.path / name).read_bytes() for name in original_files}


def diagnostic_parent_and_recipe():
    """Build a complete provider reply through the real native boundary offline."""
    from lib.contexts.workflows.application.native_generation import generate_native_turn
    item, recipe = parent_and_recipe()
    recipe.update(native_dialogue_progress_version=1, native_instruction_scope_version=1,
        reuse_initial_native_response=True)
    class InitialAuthor(RecordedPort):
        def request(self, *args, **kwargs):
            return {"answer": "First check a small extracted passage. If extraction fails, copy a short excerpt.",
                "reasoning": "I should distinguish setup from results. Desired oververbosity 5. A readable passage is not guaranteed."}
    source = item["record"]["source_context"]
    row = generate_native_turn(InitialAuthor(), recipe, stage="multiturn", unit=source,
        messages=item["record"]["question"], style={"instruction": "Keep warranted conditions."},
        source_text=source["text"], source_scope="prepared_source")
    row.update(id="old", failed_turn=1, turn_reviews=[], accepted_prefix=[])
    item["record"] = row
    item["revision_context"]["content_sha256"] = dialogue_digest(row)
    return item, recipe


def test_reuses_complete_native_response_without_rewriting_or_releasing_it():
    item, recipe = diagnostic_parent_and_recipe()
    original = deepcopy(item)
    port = RecordedPort()
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["status"] == "quarantined" and row["turn_count"] == 2
    assert len(port.requests) == 1 and len(port.next_steps) == 1
    assert len(port.assessments) == 3 and len(port.styles) == len(port.contracts) == 2
    assert row["messages"][:2] == item["record"]["messages"]
    assert row["native_dialogue_continuation"]["initial_response_reused"]
    assert row["native_dialogue_continuation"]["repair_attempt_limit"] == 0
    assert "initial_native_response" in row["native_dialogue_continuation"]["generation_reused"]
    assert row["dialogue_progress"]["generation"] == "complete"
    assert row["dialogue_progress"]["generated_turns"] == 2
    assert row["dialogue_progress"]["training_admission"] == "quarantined"
    assert row["dialogue_progress"]["release_blockers"][0]["reason"] == "unsupported_private_reasoning_setting"
    assert row["turn_reviews"][0]["accepted"] is False
    assert "reasoning_content" not in port.next_steps[0][-1]
    assert item == original


def test_diagnostic_continuation_preserves_negative_reviews_without_rebuying_answers():
    item, recipe = diagnostic_parent_and_recipe()
    port = RecordedPort(review_failure=True)
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["status"] == "quarantined" and row["reason"] == "multiturn_consistency_rejected"
    assert row["dialogue_progress"]["generation"] == "complete"
    assert row["dialogue_progress"]["passed_turn_reviews"] == 0
    assert len(row["messages"]) == 4
    assert len(port.requests) == 1 and len(port.assessments) == 3
    assert len(port.styles) == len(port.contracts) == 2
    assert all(review["accepted"] is False and review["judge"]["keep"] is False for review in row["turn_reviews"])
    rejected = [issue for issue in row["dialogue_progress"]["release_blockers"] if issue["reason"] == "multiturn_turn_review_rejected"]
    assert len(rejected) == 2 and all(issue["review"]["correctness"] == 1 for issue in rejected)


def test_diagnostic_style_and_contract_rejections_are_retained_not_overridden():
    item, recipe = diagnostic_parent_and_recipe()
    class RejectingStyleAndContract(RecordedPort):
        def check_style(self, *args, **options):
            self.styles.append(args)
            return {"keep": False, "adherence": 1, "reason": "Missing stated style."}
        def check_contract(self, *args, **options):
            self.contracts.append(args)
            return {"keep": False, "adherence": 2, "reason": "A condition is not yet confirmed."}
    port = RejectingStyleAndContract()
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["dialogue_progress"]["generation"] == "complete" and row["status"] == "quarantined"
    assert len(port.requests) == 1 and len(port.styles) == len(port.contracts) == 2
    blockers = row["dialogue_progress"]["release_blockers"]
    assert sum(issue["reason"] == "multiturn_style_review_rejected" for issue in blockers) == 2
    assert sum(issue["reason"] == "multiturn_contract_review_rejected" for issue in blockers) == 2
    assert all(review["training_eligible"] is False for review in row["turn_reviews"])


@pytest.mark.parametrize("problem", ["missing_reasoning", "delivery", "unknown_reference", "extra_issue", "tools", "content_mismatch"])
def test_diagnostic_reuse_stops_before_calls_for_unsupported_native_defects(problem):
    item, recipe = diagnostic_parent_and_recipe()
    record = item["record"]
    if problem == "missing_reasoning":
        record["messages"][-1]["reasoning_content"] = ""
    elif problem == "delivery":
        record["task_delivery"]["observable_passed"] = False
    elif problem == "unknown_reference":
        record["native_generation"]["instruction_scope_evidence"][0]["classification"] = "unresolved_reference"
    elif problem == "extra_issue":
        record["native_generation"]["issues"].append("native_response_truncated")
    elif problem == "tools":
        record["source_context"]["tools"] = [{"name": "unknown_tool"}]
    else:
        record["messages"][-1]["content"] = "A different answer."
    port = RecordedPort()
    with pytest.raises(ValueError, match="native_dialogue_(response_not_reusable|continuation_incomplete_parent)"):
        ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert not port.requests and not port.assessments


def test_diagnostic_later_missing_response_stops_and_does_not_call_unaccepted_history_accepted():
    item, recipe = diagnostic_parent_and_recipe()
    port = RecordedPort(native_failure=True)
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["status"] == "quarantined" and row["failed_turn"] == 2
    assert row["dialogue_progress"]["generation"] == "stopped"
    assert not row["accepted_prefix"]
    assert row["generated_prefix"] == item["record"]["messages"]
    assert row["messages"][:2] == item["record"]["messages"]
    assert len(port.requests) == 1


def test_diagnostic_invalid_next_step_keeps_generated_history_without_another_answer():
    from lib.contexts.workflows.domain.dialogue import DialoguePlanError
    item, recipe = diagnostic_parent_and_recipe()
    class BrokenNextStep(RecordedPort):
        def next_dialogue_step(self, *args, **kwargs):
            raise DialoguePlanError("invalid_dialogue_step")
    port = BrokenNextStep()
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["status"] == "quarantined" and row["failed_turn"] == 2
    assert row["dialogue_progress"]["generated_turns"] == 1
    assert row["generated_prefix"] == item["record"]["messages"]
    assert not row["accepted_prefix"] and not port.requests


def test_ordinary_new_generation_does_not_label_pending_native_history_as_accepted():
    from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
    item, recipe = diagnostic_parent_and_recipe()
    recipe.pop("reuse_initial_native_response")
    class FirstPendingThenIncomplete(RecordedPort):
        def request(self, key, role, prompt_id, task, **options):
            self.requests.append(deepcopy(task))
            return {"answer": "Check a small extracted passage first.",
                "reasoning": "I should check the passage. Desired oververbosity 5."
                    if len(self.requests) == 1 else ""}
    port = FirstPendingThenIncomplete()
    row, = GenerateMultiturnDialogue(port, recipe).execute(item["record"]["source_context"])
    assert row["status"] == "quarantined" and row["failed_turn"] == 2
    assert row["turn_reviews"][0]["accepted"] is False
    assert row["turn_reviews"][0]["continuation"] == "continue_with_release_blocker"
    assert row["generated_prefix"] == row["messages"][:2]
    assert not row["accepted_prefix"] and len(port.requests) == 2


@pytest.mark.parametrize("version", [1, 2])
def test_v2_diagnostic_progress_preserves_quality_rejections_and_completes_later_turns(version):
    item, recipe = diagnostic_parent_and_recipe()
    recipe.update(native_dialogue_progress_version=version, conversation_turns=3)
    class TechnicalDemoAuthor(RecordedPort):
        def request(self, key, role, prompt_id, task, **options):
            self.requests.append(deepcopy(task))
            if len(self.requests) == 1:
                return {"reasoning": "I need a small demo script. Maybe include exact system prompt.",
                    "answer": "Use this prompt: Answer using the handout and cite its page markers."}
            return {"reasoning": "I should distinguish a fallback from a tested result.",
                "answer": "Only demonstrate a saved response if a successful earlier run produced one."}
        def next_dialogue_step(self, unit, messages, state, **options):
            self.next_steps.append(deepcopy(messages))
            return {"continue": True, "user_message": "Can I see a short demo script?" if len(self.next_steps) == 1 else
                "What if the network fails before I have any saved response?",
                "reason": "Clarify a prerequisite", "dialogue_state": deepcopy(state)}
    port = TechnicalDemoAuthor()
    row, = ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert row["status"] == "quarantined"
    if version == 1:
        assert row["failed_turn"] == 2 and len(port.requests) == 1
        assert row["dialogue_progress"]["generation"] == "stopped"
    else:
        assert row["dialogue_progress"]["generation"] == "complete"
        assert len(port.requests) == 2 and len(port.assessments) == 4
        assert len(port.styles) == len(port.contracts) == 3
        assert row["turn_count"] == 3 and row["messages"][:2] == item["record"]["messages"]
        pending = row["dialogue_progress"]["release_blockers"][1]
        assert pending["issues"] == ["training_surface_internal_process_reference", "native_instruction_reference_unresolved"]
        assert pending["training_eligible"] is False
        assert row["messages"][3]["reasoning_content"] == "I need a small demo script. Maybe include exact system prompt."
        assert row["turn_reviews"][1]["accepted"] is False


@pytest.mark.parametrize("diagnostic", [False, True])
def test_v2_semantic_continuation_is_explicit_and_does_not_reclassify_findings(diagnostic):
    from lib.contexts.workflows.domain.native_dialogue_continuation import native_dialogue_progress_blocker
    item, _ = diagnostic_parent_and_recipe()
    row = item["record"]
    row["native_generation"]["issues"] = ["training_surface_internal_process_reference"]
    row["reason"] = "training_surface_internal_process_reference"
    original = deepcopy(row)
    blocker = native_dialogue_progress_blocker(row, version=2, diagnostic=diagnostic)
    assert (blocker is not None) == diagnostic
    if diagnostic:
        assert blocker["issues"] == ["training_surface_internal_process_reference"]
        assert blocker["training_eligible"] is False
    assert row == original


@pytest.mark.parametrize("problem", ["unknown_issue", "source_receipt", "missing_channel", "history_mismatch", "bad_roles", "delivery"])
def test_v2_diagnostic_integrity_failures_still_stop_before_api(problem):
    item, recipe = diagnostic_parent_and_recipe()
    recipe["native_dialogue_progress_version"] = 2
    row = item["record"]
    if problem == "unknown_issue":
        row["native_generation"]["issues"].append("native_unknown_integrity_failure")
    elif problem == "source_receipt":
        row["provided_source_context"]["text_sha256"] = "different"
    elif problem == "missing_channel":
        row["native_generation"]["channels"] = ["content"]
    elif problem == "history_mismatch":
        row["question"][0]["content"] = "A different question."
    elif problem == "bad_roles":
        row["messages"][-1]["role"] = "tool"
    else:
        row["task_delivery"]["observable_passed"] = False
    port = RecordedPort()
    with pytest.raises(ValueError):
        ContinueNativeDialogue(port, recipe, "diagnostic-child").execute(item)
    assert not port.requests and not port.assessments
