"""The real final package guard checks native answers without editing native traces."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
from lib.io_utils import atomic_json


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


ARTICLE = "设备检查必须先确认可以停机，再切断电源。完成检查后记录结果。"
NATIVE = "PRIVATE_NATIVE_EXACT: 我读取 source_memory，根据所给资料判断。\n原始思考末尾保留空格。 "


class Reviewer:
    model = "offline-package-review"
    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0}
    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "reason": "模型评审通过，但不能覆盖最终确定性检查。", "scores": {
                "correctness": 5, "reasoning": 5, "grounding": 5, "instruction": 5, "safety": 5}})


def row(index, answer, *, article=False):
    question = ARTICLE + "\n设备检查如何准备？" if article else f"设备检查场景{index}如何准备？"
    return {"id": f"native-{index}", "source_id": "verified-source", "status": "eligible",
            "reasoning_origin": "provider_reasoning", "reasoning_api_format": "chat",
            "messages": [{"role": "user", "content": question},
                         {"role": "assistant", "content": answer, "reasoning_content": NATIVE}],
            "source_context": {"kind": "document", "text": ARTICLE},
            "qa_contract": {"qa_type": "grounded" if article else "closed_book", "answer_policy": "answer",
                            "visible_context": ARTICLE if article else ""}}


def package(tmp_path, values, *, review=False, marker=True):
    output = tmp_path / "output"
    rid = engine.create_run(output, brief="解释设备准备步骤。", targets=["sft"],
                            package_review={"enabled": review, "mode": "all"})
    path = engine.run_path(output, rid)
    if not marker:
        recipe = engine.read_json(path / "recipe.json")
        recipe.pop("training_surface_protocol_version", None)
        atomic_json(path / "recipe.json", recipe)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
    reviewer = Reviewer()
    worker = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    (path / "input_records.json").write_text("[]", encoding="utf-8")
    worker.package({"sft": values})
    records = engine.read_json(path / "artifacts/sft.records.json")
    exported = [json.loads(line) for line in (path / "artifacts/sft.jsonl").read_text(encoding="utf-8").splitlines()]
    return records, exported, reviewer


def test_auto_review_disabled_still_quarantines_native_answer_and_preserves_exact_chain(tmp_path):
    values = [row(0, "这块资料里没有可套用的通用结论。"), row(1, "先确认可停机，再断电。")]
    original = deepcopy(values)
    records, exported, reviewer = package(tmp_path, values, review=False)
    assert [record["status"] for record in records] == ["quarantined", "eligible"]
    assert records[0]["reason"] == "training_surface_hidden_source_reference"
    assert all(record["messages"][-1]["reasoning_content"] == NATIVE for record in records)
    assert len(exported) == 1 and exported[0]["messages"][-1]["reasoning_content"] == NATIVE
    assert exported[0]["messages"][-1]["content"] == values[1]["messages"][-1]["content"]
    assert values == original and reviewer.calls == []


def test_selected_ai_receipt_is_consumed_before_guard_rejection_and_next_row_exports(tmp_path):
    values = [row(0, "这些资料指出必须先断电。"), row(1, "先确认可停机，再断电。")]
    records, exported, reviewer = package(tmp_path, values, review=True)
    assert len(reviewer.calls) == 2
    assert [record["status"] for record in records] == ["quarantined", "eligible"]
    assert all(record["package_review"]["status"] == "accepted" for record in records)
    assert records[0]["reason"] == "training_surface_hidden_source_reference"
    assert len(exported) == 1 and exported[0]["messages"][-1]["content"] == values[1]["messages"][-1]["content"]
    assert all(record["messages"][-1]["reasoning_content"] == NATIVE for record in records)


def test_legacy_without_surface_marker_keeps_historical_native_delivery(tmp_path):
    records, exported, reviewer = package(tmp_path, [row(0, "这块资料里没有可套用的通用结论。")], marker=False)
    assert records[0]["status"] == "eligible" and len(exported) == 1 and reviewer.calls == []
    assert exported[0]["messages"][-1]["reasoning_content"] == NATIVE


def test_real_visible_article_allows_native_answer_source_reference(tmp_path):
    records, exported, reviewer = package(tmp_path, [row(0, "这份资料指出应先确认可停机，再断电。", article=True)])
    assert records[0]["status"] == "eligible" and len(exported) == 1 and reviewer.calls == []
    assert exported[0]["messages"][-1]["reasoning_content"] == NATIVE


@pytest.mark.parametrize("target", ["cpt", "dpo", "orpo", "rlaif", "gsm8k"])
def test_answer_guard_does_not_read_other_target_payloads(target):
    assert _row_training_surface_issue(target, None, {"training_surface_protocol_version": 1}) is None
