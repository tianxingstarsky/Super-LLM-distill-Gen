"""The public CLI preflights mixed native targets and runs only on an explicit request."""
from functools import partial
import hashlib
import json

import pytest

from lib.cli import build_parser
from lib.contexts.training.api import RunFullSft
from lib.contexts.training.infrastructure.full_sft_inputs import prepare_full_sft


def test_native_sft_cli_preflight_and_explicit_run(tmp_path, monkeypatch, capsys):
    from lib.bootstrap import training_validation

    base, output = tmp_path / "base", tmp_path / "out"
    base.mkdir()
    (base / "config.json").write_text(json.dumps({"model_type": "qwen3_5", "text_config": {"max_position_embeddings": 100}}))
    np = pytest.importorskip("numpy")
    pytest.importorskip("safetensors.numpy").save_file({"test": np.zeros(1)}, str(base / "test.safetensors"))
    (base / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"test": "test.safetensors"}}))
    plain = {"status": "accepted", "messages": [{"role": "user", "content": "2+2?"},
             {"role": "assistant", "content": "4"}]}
    reasoned = {"messages": [*plain["messages"], {"role": "user", "content": "And one more?"},
                {"role": "assistant", "reasoning_content": "4+1=5", "content": "5"}]}
    looping = {"messages": [plain["messages"][0], {"role": "assistant", "reasoning": "等等。再看看。" * 54,
                                                  "content": "4"}]}
    source = tmp_path / "sft.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in (plain, reasoned, looping)) + "\n", encoding="utf-8")

    class Tokenizer:
        eos_token_id = 99
        def encode(self, text, add_special_tokens=False):
            return [10]

    class Encoder:
        def encode_turn(self, messages, selector, **_):
            thinking = selector == "high"
            assert thinking == bool(messages[-1].get("thinking"))
            ids = [1, 8, 5, 99, 10] if thinking else [1, 4, 99, 10]
            return dict(input_ids=ids, attention_mask=[1] * len(ids), labels=[-100, *ids[1:]],
                        metadata={"enable_thinking": thinking})

    calls = []
    def train(model, examples, directory, **kwargs):
        calls.append((model, directory, kwargs))
        assert len(examples) == 3
        assert examples[2]["labels"] == [-100, 8, 5, 99, 10]
        assert examples[0]["input_ids"] == examples[1]["input_ids"] == [1, 4, 99, 10]
        assert set(examples[-1]) == {"input_ids", "labels", "attention_mask"}
        return {"status": "paused"}

    prepare = partial(prepare_full_sft, tokenizer_factory=lambda _: Tokenizer(), encoder_factory=lambda *a, **kw: Encoder())
    monkeypatch.setattr(training_validation, "full_sft_application", lambda: RunFullSft(prepare, train))
    parser = build_parser()
    command = ["train-sft", "--model", str(base), "--sft", str(source), "--output", str(output)]
    args = parser.parse_args(command)
    assert args.func(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["answer_only_turns"] == 2 and report["thinking_turns"] == 1
    assert report["supervised_tokens"] == 10 and report["source_rows"] == 3
    assert report["held_turns"] == 1 and report["held_examples"][0]["line"] == 3
    assert report["held_examples"][0]["field"] == "thinking"
    assert report["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert not calls and not output.exists()
    args = parser.parse_args([*command, "--action", "run"])
    assert args.func(args) == 2
    capsys.readouterr()
    assert calls == [(base, output, {"source_sha256": report["source_sha256"], "gradient_accumulation": 8,
                                      "seed": 20261119, "learning_rate": 8e-6})]
    receipt = json.loads((output / "full-sft-target-policy.json").read_bytes())
    assert receipt["held_turns"] == 1 and receipt["included_turns"] == 3
    previous = tmp_path / "previous"
    (previous / "full-training").mkdir(parents=True)
    (previous / "full-training" / "identity.json").write_text("{}")
    args = parser.parse_args([*command[:-1], str(previous)])
    assert args.func(args) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert legacy["assistant_turns"] == 4 and "target_policy" not in legacy
    args = parser.parse_args([*command, "--action", "run"])
    source.write_text(json.dumps({**plain, "status": "unverified"}), encoding="utf-8")
    with pytest.raises(ValueError, match="line 1.*training_record_ineligible"):
        args.func(args)
    assert len(calls) == 1


def test_repetition_requires_a_recurring_prose_sequence_and_respects_requested_output():
    from lib.contexts.datasets.contracts import assistant_target_repetition

    def evidence(question, content, thinking=""):
        return assistant_target_repetition([{"role": "user", "content": question},
                                            {"role": "assistant", "thinking": thinking, "content": content}])

    repeated = "Wait. Check again. " * 60
    assert evidence("Solve the puzzle.", repeated)["field"] == "content"
    assert evidence("Repeat this dialogue twenty times.", repeated) is None
    assert evidence("把这句话重复二十遍。", "我再看一下。" * 20) is None
    assert evidence("Explain the geometry.", "4", "CD = (2ab/(a + b)) * cos(C/2)\n" * 24) is None
    assert evidence("Show the program.", "\x60\x60\x60python\n" + "print('Checking again.')\n" * 20 + "\x60\x60\x60") is None
    varied = "\n".join(f"Check the result against the previous case. There are now {i} possible items."
                       for i in range(40))
    assert evidence("Evaluate every case.", "40", varied) is None
