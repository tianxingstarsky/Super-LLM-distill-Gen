"""Offline contracts for bounded, cited source lookup before author completion."""
from copy import deepcopy
import hashlib
import json

import pytest

from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.generate_with_sources import GenerateWithSources
from lib.contexts.workflows.domain.generation_sources import GenerationSources
from lib.contexts.workflows.domain.review_source_inventory import text_sha256
from lib.contexts.workflows.infrastructure.generation_source_search import search_generation_sources
from lib.contexts.workflows.contracts import training_record
from lib.domain.workflow_generation import validate_node_generation, style_for_sample
from test_integrated_reflection import NoReviewPort, recipe, body
from test_generation_node_use_cases import conversation, REASONING, ANSWER


LOOKUP = {"version": 1, "max_rounds": 2, "max_hits": 3}


def config(mode="joint"):
    value = recipe(mode)
    value["node_generation"]["cot"]["source_lookup"] = deepcopy(LOOKUP)
    return value


def tool(identity="a", query="ROUGE-L"):
    return {"role": "assistant", "content": "", "reasoning_content": "Need to distinguish scores and rates.",
            "tool_calls": [{"id": identity, "type": "function", "function": {
                "name": "search_evidence", "arguments": json.dumps({"query": query})}}]}


def final(candidate=None):
    return {"role": "assistant", "content": json.dumps(body() if candidate is None else candidate),
            "reasoning_content": "Complete native thought retained.", "tool_calls": []}


class SourcePort(NoReviewPort):
    def __init__(self, turns):
        super().__init__()
        self.turns = iter(turns)
        self.sent, self.searches = [], []

    def prepare_generation_context(self, context):
        return deepcopy(context)

    def check_reasoning_cancel(self):
        pass

    def generation_source_messages(self, prompt_id, task, instruction, index):
        return [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(task)}]

    def generation_source_turn(self, key, messages, tools, *, final):
        def invoke():
            self.sent.append((deepcopy(messages), final))
            return next(self.turns)
        return self.checkpoint(key, invoke)

    def search_generation_sources(self, passages, query, limit):
        self.searches.append(query)
        return search_generation_sources(passages, query, limit)


def paper():
    text = "ROUGE-L is reported on a score scale.\nThe score changed from 12.4 to 19.8."
    return {"text": text, "coverage_manifest": {"version": 1, "text_sha256": text_sha256(text),
        "page_count": 1, "pages": [{"page": 1, "start": 0, "end": len(text), "text_sha256": text_sha256(text)}]}}


def test_exact_spans_pages_and_history_never_become_source_facts():
    context = {"source": paper(), "prompt": [{"role": "assistant", "content": "BANNEDHISTORY"}]}
    sources = GenerationSources(context)
    locations = search_generation_sources(sources.passages, "ROUGE-L score", 3)
    hit, = sources.search_receipt(locations)["hits"]
    assert hit["text"] == context["source"]["text"][hit["start"]:hit["end"]]
    assert hit["pages"] == [1] and hit["line_start"] == 1 and hit["line_end"] == 2
    assert hit["text_sha256"] == hashlib.sha256(hit["text"].encode()).hexdigest()
    assert search_generation_sources(sources.passages, "BANNEDHISTORY", 3) == []
    assert "passed" not in hit


def test_selected_range_cannot_reuse_full_source_index_or_metadata_as_text():
    full = GenerationSources({"source": {"text": "OUTSIDESCOPE secret value."}})
    assert search_generation_sources(full.passages, "OUTSIDESCOPE", 1)
    selected = GenerationSources({"source_line_scope": "selected", "source": {"materials": [
        {"material_id": "x", "start_line": 30, "text": "Allowed evidence.", "name": "OUTSIDESCOPE"}]}})
    assert search_generation_sources(selected.passages, "OUTSIDESCOPE", 1) == []
    hit = selected.read(selected.passages[0]["id"], 0, 100)
    assert hit["line_start"] == 30 and hit["scope"] == "selected_ranges"
    assert selected.read("source.text", 0, 20)["ok"] is False


def test_fts_preserves_decimals_and_supports_explicit_figure_phrase():
    sources = GenerationSources({"source": {"materials": [
        {"text": "Figure 4 describes 44 unrelated cases."},
        {"text": "Figure 7 shows 44.4 and 54.4."},
        {"text": "Figure 6 shows satisfaction counts."}]}})
    hit, = sources.search_receipt(search_generation_sources(sources.passages, '"Figure 6"', 3))["hits"]
    assert hit["text"] == "Figure 6 shows satisfaction counts."
    hit, = sources.search_receipt(search_generation_sources(sources.passages, '44.4 54.4', 3))["hits"]
    assert hit["text"] == "Figure 7 shows 44.4 and 54.4."


def test_real_source_pack_projection_enforces_selected_scope(tmp_path):
    from types import SimpleNamespace
    from test_source_memory_requests import request, SELECTED
    from lib.contexts.workflows.infrastructure.generation_adapter import CheckpointedGenerationAdapter
    port = CheckpointedGenerationAdapter(SimpleNamespace(path=tmp_path, recipe={"source_memory_protocol_version": 2}))
    context = port.prepare_generation_context(request())
    sources = GenerationSources(context)
    assert len(sources.passages) == 1 and sources.passages[0]["text"] == SELECTED
    assert sources.inventory["coverage"] == "selected_ranges"
    assert search_generation_sources(sources.passages, "OUTSIDE FORGED", 3) == []


def test_author_looks_up_before_final_with_exact_native_history_and_no_review():
    port = SourcePort([tool(), final()])
    sample = {**conversation(), "source_context": paper()}
    row, = GenerateCot(port, config()).generate_styled(sample, {"instruction": "CUSTOM INSTRUCTION"})
    assert row["status"] == "eligible" and not port.calls and len(port.sent) == 2
    messages = port.sent[1][0]
    assert messages[-2] == tool() and messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "a"
    assert json.loads(messages[-1]["content"])["hits"][0]["pages"] == [1]
    receipt = row["generation_sources"]
    assert receipt["semantic_status"] == "not_assessed" and receipt["independent_review"] is False
    assert receipt["transcript"][0]["assistant"]["reasoning_content"] == tool()["reasoning_content"]
    exported = training_record("cot", row)
    assert exported == {"question": conversation()["messages"][:-1], "reasoning": [REASONING], "answer": ANSWER}
    assert not {"generation_sources", "source_context", "embedding", "vectors"}.intersection(exported)
    # Recovery reuses the same model decisions and reconstructs exact tool receipts.
    resumed, = GenerateCot(port, config()).generate_styled(sample, {"instruction": "CUSTOM INSTRUCTION"})
    assert resumed == row and len(port.sent) == 2


def test_no_lookup_has_no_extra_model_turn():
    port = SourcePort([final()])
    candidate, receipt = GenerateWithSources(port, LOOKUP).execute(["q"], "workflow.cot_generate", {}, "rules")
    assert candidate == body() and receipt["model_turns"] == 1 and not port.searches


def test_repeat_lookup_stops_without_purchasing_another_answer_or_fabricating_pass():
    port = SourcePort([tool(), tool("b")])
    candidate, receipt = GenerateWithSources(port, LOOKUP).execute(["q"], "workflow.cot_generate", {"source": paper()}, "rules")
    assert candidate is None and len(port.sent) == 2 and port.searches == ["ROUGE-L"]
    assert receipt["issue"] == "repeated_source_lookup_without_progress"
    assert receipt["transcript"][1]["observations"][0]["result"]["cached"] is True
    assert "passed" not in receipt


def test_tool_error_recovery_preserves_native_thought_and_last_turn_disables_tools():
    broken = tool()
    broken["tool_calls"][0]["function"]["arguments"] = "{invalid"
    port = SourcePort([broken, tool("b"), final()])
    candidate, receipt = GenerateWithSources(port, LOOKUP).execute(["q"], "workflow.cot_generate", {"source": paper()}, "rules")
    assert candidate == body() and receipt["model_turns"] == 3
    assert port.sent[-1][1] is True
    assert port.sent[-1][0][2] == broken
    assert receipt["transcript"][0]["observations"][0]["result"]["ok"] is False


@pytest.mark.parametrize("content", [None, "{truncated", "not json"])
def test_invalid_completed_response_is_preserved_and_not_rewritten(content):
    response = {"role": "assistant", "content": content, "reasoning_content": "Full provider thought.", "tool_calls": []}
    port = SourcePort([response])
    row, = GenerateCot(port, config()).generate_styled(conversation(), {"instruction": "rules"})
    assert row["status"] == "quarantined" and len(port.sent) == 1
    assert row["feedback"]["generation_sources"]["transcript"][0]["assistant"] == response


def test_native_prefill_preserves_full_retrieval_grounded_rationale():
    rationale = body(); rationale.pop("answer")
    port = SourcePort([tool(), final(rationale)])
    port.validate_native_prefill = lambda: None
    requested = []
    class Answer(dict):
        response_details = {"reasoning_prefill": {"transport": "deepseek_chat_beta",
                            "prefix_sha256": hashlib.sha256(REASONING.encode()).hexdigest()}}
    def answer(key, payload, **options):
        requested.append(options)
        return Answer(answer=ANSWER)
    port.request_native_answer = answer
    usecase = GenerateCot(port, config("native_prefill"))
    port.generate_native_answer = usecase.generate_native_answer
    row, = usecase.generate_styled({**conversation(), "source_context": paper()}, {"instruction": "rules"})
    assert row["status"] == "eligible" and row["reasoning"] == [REASONING]
    assert len(requested) == 1 and requested[0]["reasoning_prefix"] == REASONING
    assert row["generation_sources"]["model_turns"] == 2 and not port.calls


def test_lookup_config_is_explicit_validated_and_does_not_change_old_recipes():
    old = recipe()["node_generation"]
    assert "source_lookup" not in validate_node_generation(old)["cot"]
    new = config()["node_generation"]
    assert validate_node_generation(new)["cot"]["source_lookup"] == LOOKUP
    assert style_for_sample(new["cot"], "sample") == style_for_sample(old["cot"], "sample")
    for broken in ({**LOOKUP, "max_rounds": 100}, {**LOOKUP, "version": True}, {**LOOKUP, "max_hits": 0}):
        new["cot"]["source_lookup"] = broken
        with pytest.raises(ValueError):
            validate_node_generation(new)


def test_real_workflow_freezes_lookup_preserves_exports_and_resumes(tmp_path):
    from test_workflow_styled_generation import run, Generator
    from lib.infrastructure import training_workflow as engine
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
                               node_generation=config()["node_generation"])
    class GeneratorWithTools(Generator):
        def __init__(self):
            super().__init__()
            self.turns = []
        def tool_turn(self, messages, tools, **options):
            self.turns.append(deepcopy(messages))
            return tool() if len(self.turns) == 1 else final()
        def chat(self, *args, **kwargs):
            pytest.fail("lookup author must not start a separate ordinary/review request")
    generator = GeneratorWithTools()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator).execute()
    assert state["status"] == "completed" and len(generator.turns) == 2
    record = engine.read_json(path / "artifacts/cot.records.json")[0]
    assert record["generation_sources"]["model_turns"] == 2
    learner_users = [m for m in record["question"] if m.get("role") == "user"]
    assert [m for m in generator.turns[0] if m.get("role") == "user"] == learner_users
    # Retrieval continuation adds real tool results, not synthetic user commands.
    assert [m for m in generator.turns[1] if m.get("role") == "user"] == learner_users
    exported = (path / "artifacts/cot.jsonl").read_bytes()
    assert json.loads(exported)["reasoning"] == [REASONING]
    assert "generation_sources" not in json.loads(exported)
    engine.Workflow(output, run_id, tmp_path, generator=generator).execute()
    assert len(generator.turns) == 2 and (path / "artifacts/cot.jsonl").read_bytes() == exported
