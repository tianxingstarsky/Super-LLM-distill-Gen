"""Actual Qwen output delimiters must not turn unfinished thought into an answer."""
from types import SimpleNamespace

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
