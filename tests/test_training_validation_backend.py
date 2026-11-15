"""Actual Qwen output delimiters must not turn unfinished thought into an answer."""
from types import SimpleNamespace
import hashlib
import json
import pytest

from lib.contexts.training.infrastructure.transformers_backend import _eos_ids, _split_output


def test_native_thinking_prefill_preserves_reasoning_and_requires_a_closed_answer_boundary():
    tokenizer = SimpleNamespace(eos_token="<|im_end|>", pad_token="<|endoftext|>")
    prompt = "<|im_start|>assistant\n<think>\n"
    reasoning, answer, closed = _split_output("完整思考\n</think>\n答案<|im_end|>", prompt, tokenizer)
    assert reasoning == "\n完整思考\n"
    assert answer == "答案" and closed
    assert _split_output("未结束的思考", prompt, tokenizer) == ("\n未结束的思考", "", False)


def test_stop_ids_include_the_actual_turn_end_without_modifying_model_configuration():
    configured = [248044]
    model = SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=configured))
    tokenizer = SimpleNamespace(eos_token_id=248046)
    assert _eos_ids(model, tokenizer) == [248044, 248046]
    assert configured == [248044]


def test_imported_assessment_cannot_certify_a_partial_answer_or_unfinished_thought(tmp_path):
    from lib.contexts.training.infrastructure.report import _assessments

    source = tmp_path / "baseline" / "case-0008.json"
    source.parent.mkdir()
    # Structural projection of the actual head-width case; the full raw record stays local.
    actual = {"case_id": "test_head_composition-1", "answer": "64", "reasoning": "Equation: $6 \\times w = 384$.", "thinking_closed": True,
              "ended_with_eos": True, "truncated": False}
    source.write_text(json.dumps(actual), encoding="utf-8")
    record = {"arm": "baseline", "case_id": actual["case_id"], "artifact": "baseline/case-0008.json",
        "output_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "semantic_score": 2,
        "reasoning_material_contradiction": False, "combined_usable": True}

    def publish():
        (tmp_path / "semantic-assessment.json").write_text(json.dumps({
            "experiment_identity": "frozen", "cases": [record]}), encoding="utf-8")

    publish()
    assert len(_assessments(tmp_path, {"identity": "frozen"})[0]) == 1
    record["semantic_score"] = 1
    publish()
    with pytest.raises(ValueError, match="decision_inconsistent"):
        _assessments(tmp_path, {"identity": "frozen"})
    record["semantic_score"] = 2
    actual["thinking_closed"], actual["reasoning"] = True, "\n"
    source.write_text(json.dumps(actual), encoding="utf-8")
    record["output_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    publish()
    with pytest.raises(ValueError, match="decision_inconsistent"):
        _assessments(tmp_path, {"identity": "frozen"})
    actual["reasoning"] = "Equation: $6 \\times w = 384$."
    actual["thinking_closed"] = False
    source.write_text(json.dumps(actual), encoding="utf-8")
    record["output_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    publish()
    with pytest.raises(ValueError, match="decision_inconsistent"):
        _assessments(tmp_path, {"identity": "frozen"})
