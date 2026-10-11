"""Multi-turn generation and factual review share the same accepted source pack."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine


ANCHOR = "BluePump inspection begins by switching off power and closing the inlet valve."
NEIGHBOR = "BluePump restart requires opening the outlet valve. Record outlet pressure after restart."


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All calls use in-memory clients; no model endpoint is contacted."""
    yield


class Generator:
    model = "offline-multiturn-source-pack"

    def __init__(self, *, expect_pack):
        self.expect_pack, self.calls, self.usage = expect_pack, [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if self.expect_pack:
            assert [item["text"] for item in data["teacher_context_pack"][0]["materials"]] == [ANCHOR, NEIGHBOR]
        else:
            assert "teacher_context_pack" not in data and "source_context_pack" not in data
        if data.get("request_phase") == "next_turn":
            assert data["dialogue_messages"][1]["content"].startswith("Switch off power")
            return json.dumps({"continue": True, "user_message": "What should I record after restart?",
                "reason": "The restart procedure has been explained; complete the requested inspection record.",
                "dialogue_state": {"progress": "Restart steps have been explained.",
                    "shared_understanding": [data["dialogue_messages"][1]["content"]],
                    "open_issues": ["Complete the inspection record."]}})
        assert data["task"] == ANCHOR  # precise primary anchor remains intact
        turn = sum(message["role"] == "user" for message in data["messages"])
        return json.dumps({"answer": "Switch off power, close the inlet, then open the outlet before restart."
            if turn == 1 else "Record the outlet pressure after restart.",
            "quotes": [NEIGHBOR] if self.expect_pack else [ANCHOR]})


class Reviewer:
    model = "offline-source-pack-reviewer"

    def __init__(self, *, expect_pack):
        self.expect_pack, self.calls, self.usage = expect_pack, [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if data.get("learner_context_only"):
            assert "teacher_context_pack" not in data
            assert "source_context" not in data and "teacher_evidence" not in data
            assert data["learner_messages"]
            return json.dumps({"keep": True, "adherence": 5, "reason": "Actual messages contain required context."})
        if self.expect_pack:
            assert NEIGHBOR in [item["text"] for item in data["teacher_context_pack"][0]["materials"]]
            assert data["context"]["task"] == ANCHOR
        else:
            assert "teacher_context_pack" not in data
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The complete source and real dialogue agree."})


def make_worker(tmp_path, *, packed=True):
    source = tmp_path / "chapters.jsonl"
    source.write_text("\n".join(json.dumps({"text": text}) for text in (ANCHOR, NEIGHBOR)), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"], conversation_turns=2,
        qa_director={"enabled": True, "planning_mode": "adaptive", "batch_size": 1})
    generator, reviewer = Generator(expect_pack=packed), Reviewer(expect_pack=packed)
    worker = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer)
    parsed = worker.stage_items("ingest", worker.recipe["sources"], worker.parse_source, stream_sources=True)
    cleaned = worker.prepare_clean_sources(parsed.ready(2, 2))
    unit = next(iter(cleaned))
    assert unit["text"] == ANCHOR and NEIGHBOR not in unit["text"]
    if not packed:
        # Old immutable recipes have neither the opt-in marker nor source-pack
        # references in their saved units. Keep their anchor-only requests.
        worker.recipe.pop("source_context_pack_version")
        unit.pop("source_context_pack")
    visible = ANCHOR + "\n\n" + NEIGHBOR if packed else ANCHOR
    unit["qa_contract"] = {"id": unit["id"], "qa_type": "grounded",
        "question": "Explain the BluePump inspection and restart sequence.", "visible_context": visible,
        "answer_policy": "answer", "guidance": "Finish the actual inspection record.",
        "evidence_quotes": [NEIGHBOR] if packed else [ANCHOR],
        "dialogue_design": {"interaction_goal": "Complete a safe restart checklist and inspection record."}}
    unit["family_id"] = "offline-family"
    worker.stage = "multiturn"
    return worker, unit, generator, reviewer


def test_multiturn_full_source_pack_reaches_generation_and_judges_with_neighbor_quotes(tmp_path):
    worker, unit, generator, reviewer = make_worker(tmp_path)
    result = worker.multiturn(unit)[0]
    assert result["status"] == "eligible", result.get("reason")
    assert result["turn_count"] == 2 and result["quotes_by_turn"] == [[NEIGHBOR], [NEIGHBOR]]
    assert len(generator.calls) == 3
    assert all(data.get("teacher_context_pack") for data in generator.calls)
    factual = [data for data in reviewer.calls if not data.get("learner_context_only")]
    blinded = [data for data in reviewer.calls if data.get("learner_context_only")]
    assert len(factual) == 3 and len(blinded) == 2
    assert all(data.get("teacher_context_pack") for data in factual)
    assert all("teacher_context_pack" not in data for data in blinded)
    assert factual[-1]["context"]["whole_dialogue"] is True


def test_old_multiturn_recipe_keeps_anchor_requests_and_quote_validation(tmp_path):
    worker, unit, generator, reviewer = make_worker(tmp_path, packed=False)
    result = worker.multiturn(unit)[0]
    assert result["status"] == "eligible", result.get("reason")
    assert result["quotes_by_turn"] == [[ANCHOR], [ANCHOR]]
    assert all("teacher_context_pack" not in data for data in [*generator.calls, *reviewer.calls])
