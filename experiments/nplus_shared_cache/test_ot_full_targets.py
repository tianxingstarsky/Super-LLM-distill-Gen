"""Check a full Qwen trajectory and the producer/consumer isolation contract on CPU."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5TextConfig
from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5TextModel

from experiments.nplus_shared_cache import (
    ot_corpus_scope as scope,
    ot_full_targets as targets,
    ot_full_train,
    runner,
)


class FullTargetTests(unittest.TestCase):
    def test_every_pooled_segment_matches_one_native_forward_including_partial_tail(
        self,
    ):
        config = Qwen3_5TextConfig(
            vocab_size=32,
            hidden_size=16,
            intermediate_size=32,
            num_hidden_layers=2,
            num_attention_heads=2,
            num_key_value_heads=1,
            head_dim=8,
            layer_types=["linear_attention", "full_attention"],
            linear_num_key_heads=2,
            linear_num_value_heads=2,
            linear_key_head_dim=8,
            linear_value_head_dim=8,
            linear_conv_kernel_dim=4,
            rope_parameters={
                "rope_type": "default",
                "rope_theta": 10000.0,
                "partial_rotary_factor": 0.5,
                "mrope_section": [1, 1, 0],
            },
        )
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(172)
            native = Qwen3_5TextModel(config).requires_grad_(False).eval()
        base = SimpleNamespace(
            model=SimpleNamespace(language_model=native),
            config=SimpleNamespace(text_config=config),
            training=native.training,
            parameters=native.parameters,
            get_input_embeddings=native.get_input_embeddings,
        )
        record = dict(
            id="integer-count",
            ordinal=1,
            question="Count the integers in the sequence.",
            reasoning="Count the entries in order.",
            answer="11",
            source_completion="complete",
            source={"domain": "math"},
        )
        ids = {
            "prompt": list(range(2, 7)),
            record["reasoning"]: list(range(7, 18)),
            record["answer"]: [18],
        }
        tokenizer = SimpleNamespace(
            apply_chat_template=lambda *args, **kwargs: "prompt",
            encode=lambda text, **kwargs: ids[text],
        )
        pooled, receipt = targets.encode_case(
            base, tokenizer, record, segment_tokens=3, forward_tokens=6
        )
        with torch.inference_mode():
            states = native(
                input_ids=torch.arange(2, 18)[None], use_cache=False
            ).last_hidden_state[0, 5:]
        torch.testing.assert_close(
            pooled,
            torch.stack([s.mean(0) for s in states.split(3)]),
            rtol=1e-5,
            atol=1e-6,
        )
        self.assertEqual(receipt["segment_ends"], [3, 6, 9, 11])
        self.assertEqual(receipt["final_cache_tokens"], 16)

    def test_isolated_material_has_only_a_bound_receipt_and_no_model_load(self):
        record = dict(
            id="out-of-scope",
            ordinal=7,
            question="Define malware.",
            reasoning="",
            answer="A security definition.",
            source={
                "domain": "code",
                "question_sha256": "a" * 64,
                "response_sha256": "b" * 64,
                "shard_sha256": "c" * 64,
            },
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner.write_json(root / "manifest.json", {})
            with (
                patch.object(targets, "load_manifest", return_value={}),
                patch.object(targets, "source_rows", return_value=[record]),
                patch(
                    "experiments.nplus_shared_cache.ot_latent_model._base_identity",
                    return_value={},
                ),
                patch.object(
                    runner,
                    "load_frozen_model",
                    side_effect=AssertionError("unexpected load"),
                ),
            ):
                targets.prepare(root, root, 7, 1)
            receipt, row, hidden, ends = ot_full_train.read_case(root, 7)
            self.assertEqual(
                receipt["binding_sha256"],
                runner.file_sha(root / "teacher-binding.json"),
            )
            self.assertEqual(receipt["scope_sha256"], scope.scope_sha256())
            self.assertEqual(
                (receipt["status"], row, hidden, ends), ("excluded", None, None, None)
            )
            self.assertEqual(
                [p.name for p in (root / "queue" / "000000007").iterdir()],
                ["receipt.json"],
            )
            with self.assertRaises(ValueError):
                targets.encode_case(None, None, record)

    def test_original_open_thought_and_missing_answer_are_separate_states(self):
        self.assertEqual(
            targets.split_response("<think>unfinished "),
            ("open_reasoning", "unfinished ", ""),
        )
        self.assertEqual(
            targets.split_response("<think>reasoned</think>\n"),
            ("closed_without_answer", "reasoned", ""),
        )
        self.assertEqual(
            targets.split_response("published answer"),
            ("answer_only", "", "published answer"),
        )
        self.assertEqual(
            targets.split_response("<think>first</think>rest</think>"),
            ("complete", "first", "rest</think>"),
        )
