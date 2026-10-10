"""Imported reasoning aliases obey the SFT node's chosen export policy."""
from copy import deepcopy
import json

import pytest

from lib.domain.reasoning_fields import sft_reasoning_issue, sft_training_messages
from lib.infrastructure.training_workflow import (
    Workflow, create_run, read_json, run_path, verify_artifacts,
)


CHAIN = "先检查前提，再确认结果。"
MESSAGES = [{"role": "user", "content": "检查步骤是什么？"},
            {"role": "assistant", "content": "先检查前提。"}]


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model requests here use injected offline clients."""
    yield


class NoGeneration:
    model = "offline-unused-generation"
    usage = {"calls": 0}

    def chat(self, *args, **kwargs):
        raise AssertionError("Recorded reasoning must not trigger generation.")


class AcceptingJudge:
    model = "offline-reasoning-alias-judge"

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
            "correctness": 5, "reason": "Recorded content is consistent.",
            "scores": {key: 5 for key in (
                "correctness", "reasoning", "grounding", "instruction", "safety")}})


def run_import(tmp_path, fields, *, style="separated"):
    messages = deepcopy(MESSAGES)
    messages[-1].update(fields)
    source = tmp_path / "conversation.jsonl"
    original = json.dumps({"messages": messages}, ensure_ascii=False)
    source.write_text(original, encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["sft"], sft_output_style=style)
    judge = AcceptingJudge()
    state = Workflow(output, run_id, tmp_path, generator=NoGeneration(), jev=judge).execute()
    path = run_path(output, run_id)
    assert source.read_text(encoding="utf-8") == original
    assert read_json(path / "input_records.json")[0]["messages"] == messages
    return state, path, judge


@pytest.mark.parametrize("fields", [
    {"reasoning": CHAIN}, {"thinking": CHAIN},
    {"reasoning_content": CHAIN, "thinking": CHAIN, "reasoning": CHAIN},
])
@pytest.mark.parametrize("style", ["separated", "drop"])
def test_imported_aliases_use_one_canonical_field_or_no_reasoning(tmp_path, fields, style):
    state, path, judge = run_import(tmp_path, fields, style=style)
    assert state["status"] == "completed", state.get("error")
    native = json.loads((path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    trained = json.loads((path / "artifacts/trl_sft.jsonl").read_text(encoding="utf-8"))
    final = native["messages"][-1]
    assert final["content"] == MESSAGES[-1]["content"]
    assert "reasoning" not in final and "thinking" not in final
    assert (final.get("reasoning_content") == CHAIN) is (style == "separated")
    assert (trained["messages"][-1].get("thinking") == CHAIN) is (style == "separated")
    assert judge.usage["calls"] == 1
    assert verify_artifacts(path)["counts"] == {"sft": 1}


@pytest.mark.parametrize("fields,reason", [
    ({"reasoning_content": CHAIN, "thinking": "另一个互相矛盾的说明。"},
     "sft_conflicting_reasoning_fields"),
    ({"reasoning": ["unsupported structured reasoning"]}, "sft_invalid_reasoning_fields"),
])
def test_conflicting_or_structured_source_reasoning_is_quarantined_before_models(tmp_path, fields, reason):
    state, path, judge = run_import(tmp_path, fields)
    assert state["status"] == "needs_attention", state.get("error")
    assert state["quality"]["targets"]["sft"]["reasons"] == {reason: 1}
    assert judge.usage["calls"] == 0
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""
    assert verify_artifacts(path)["counts"] == {"sft": 0}


def test_export_projection_does_not_rewrite_review_evidence_and_drops_every_alias():
    messages = deepcopy(MESSAGES)
    messages[-1].update(reasoning_content=CHAIN, reasoning=CHAIN, thinking=CHAIN)
    before = deepcopy(messages)
    assert sft_reasoning_issue(messages) is None
    assert sft_training_messages(messages)[-1] == {**MESSAGES[-1], "reasoning_content": CHAIN}
    assert sft_training_messages(messages, drop=True) == MESSAGES
    assert messages == before


def test_final_package_rechecks_cached_sft_reasoning_instead_of_exporting_a_conflict(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("检查操作前应确认前提。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["sft"])

    class CachedConflict(Workflow):
        def sft(self, unit):
            messages = deepcopy(MESSAGES)
            messages[-1].update(reasoning_content=CHAIN, thinking="另一个不同的说明。")
            return [{"id": unit["id"], "source_id": unit["source_id"],
                     "status": "eligible", "messages": messages}]

    state = CachedConflict(output, run_id, tmp_path, generator=NoGeneration()).execute()
    path = run_path(output, run_id)
    assert state["status"] == "needs_attention", state.get("error")
    assert state["quality"]["targets"]["sft"]["reasons"] == {"sft_conflicting_reasoning_fields": 1}
    assert verify_artifacts(path)["counts"] == {"sft": 0}
