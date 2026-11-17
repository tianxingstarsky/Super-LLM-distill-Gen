"""CPU protocol checks using scripted fake predictions, not model-quality tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
from safetensors import safe_open

from experiments.nplus_shared_cache.parallel_observer import run_parallel_observer
from experiments.nplus_shared_cache.soft_embedding import SoftEmbeddingReadout
from experiments.nplus_shared_cache.text_supervision import encode_five_paths


WIDTH = 3
EOS = 1
THOUGHT_END = 2


class ScriptedTokenizer:
    def encode(self, text, add_special_tokens=False):
        if text != "</think>":
            raise AssertionError("Only the actual ending-marker lookup is expected")
        return [THOUGHT_END]

    def decode(self, tokens, skip_special_tokens=False):
        return "".join(f"<{token}>" for token in tokens)


class RecordingEmbedding(torch.nn.Embedding):
    def __init__(self):
        super().__init__(64, WIDTH)
        self.calls = 0
        with torch.no_grad():
            self.weight.copy_(torch.arange(64 * WIDTH).reshape(64, WIDTH).float() / 10 + 1)
        self.requires_grad_(False)

    def forward(self, ids):
        self.calls += 1
        return super().forward(ids)


class ScriptedVocabulary(torch.nn.Module):
    """The first two head coordinates identify a route and scripted round."""
    def __init__(self, endings):
        super().__init__()
        self.endings = endings
        self.weight = torch.nn.Parameter(torch.zeros(64, WIDTH), requires_grad=False)

    @staticmethod
    def selected_token(route, round_number, ending_round):
        if round_number == ending_round:
            return EOS
        if ending_round is not None and round_number > ending_round:
            # A poison token catches appending predictions after a route ended.
            return 50 + route
        return 10 + route * 5 + round_number

    def forward(self, heads):
        scores = torch.full((5, 64), -100.0)
        for route in range(5):
            if int(heads[route, 0]) != route + 1:
                raise AssertionError("Unexpected scripted route marker")
            round_number = int(heads[route, 1])
            token = self.selected_token(route, round_number, self.endings[route])
            scores[route, token] = 100.0
        return scores


class RecordingFusion(torch.nn.Linear):
    def __init__(self):
        super().__init__(5 * WIDTH, WIDTH, bias=False)
        self.inputs = []
        with torch.no_grad():
            self.weight.copy_(torch.arange(1, 5 * WIDTH * WIDTH + 1).reshape(WIDTH, 5 * WIDTH) / 100)
        self.requires_grad_(False)

    def forward(self, cat):
        self.inputs.append(cat.detach().clone())
        return super().forward(cat)


class ScriptedHeads(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.rounds = 0
        self.history = []
        self.fusion = RecordingFusion()
        self.gain = torch.nn.Parameter(torch.tensor(1.0), requires_grad=False)

    def forward(self, hidden):
        self.rounds += 1
        heads = torch.tensor([[[route + 1, self.rounds, route + self.rounds + 2]
                               for route in range(5)]], dtype=torch.float32)
        self.history.append(heads.clone())
        # These unused values do not represent a trained auxiliary model.
        return heads, heads.flatten(1), torch.zeros((1, WIDTH))


class RecordingCache:
    def __init__(self):
        self.layers = [SimpleNamespace(keys=torch.empty(1, 1, 0, WIDTH),
                                       values=torch.empty(1, 1, 0, WIDTH))]

    def append(self, inputs):
        for name in ("keys", "values"):
            setattr(self.layers[0], name, torch.cat((getattr(self.layers[0], name), inputs.unsqueeze(1)), dim=-2))

    def get_seq_length(self):
        return self.layers[0].keys.shape[-2]


class RecordingBackbone:
    def __init__(self):
        self.cache = None
        self.calls = []

    def __call__(self, *, inputs_embeds, attention_mask, position_ids,
                 use_cache, past_key_values=None):
        if not use_cache:
            raise AssertionError("This observer protocol should retain its cache")
        if self.cache is None:
            if past_key_values is not None:
                raise AssertionError("Prefill unexpectedly received a cache")
            self.cache = RecordingCache()
        elif past_key_values is not self.cache:
            raise AssertionError("Shared cache was replaced")
        before = self.cache.get_seq_length()
        count = inputs_embeds.shape[1]
        if attention_mask.tolist() != [[1] * (before + count)]:
            raise AssertionError("Attention mask lost the prior context")
        if position_ids.tolist() != [list(range(before, before + count))]:
            raise AssertionError("Positions did not advance with the cache")
        self.calls.append({"before": before, "input": inputs_embeds.clone(), "cache": self.cache})
        self.cache.append(inputs_embeds)
        return SimpleNamespace(last_hidden_state=inputs_embeds.clone(), past_key_values=self.cache)


class ScriptedBase:
    def __init__(self, endings):
        self.embedding = RecordingEmbedding()
        self.backbone = RecordingBackbone()
        self.model = SimpleNamespace(language_model=self.backbone, rope_deltas=None)
        self.lm_head = ScriptedVocabulary(endings)
        self.generation_config = SimpleNamespace(eos_token_id=[EOS])
        self.config = SimpleNamespace(text_config=SimpleNamespace(max_position_embeddings=256))

    def get_input_embeddings(self):
        return self.embedding


class ParallelObserverProtocolTests(unittest.TestCase):
    def check_scenario(self, endings, max_rounds):
        base, auxiliary = ScriptedBase(endings), ScriptedHeads()
        expected_rounds = max(endings) if all(value is not None for value in endings) else max_rounds
        expected_rounds = min(expected_rounds, max_rounds)
        with TemporaryDirectory(prefix="nplus-observer-protocol-") as temporary:
            prepared = {"output": Path(temporary), "prefix_ids": [3, 4, 5],
                        "manifest": {"identity": "scripted-protocol-only"}}
            # Production remains CUDA-specific. Only instrumentation is replaced;
            # route transition, real torch masking/CAT/fusion and loop are intact.
            with patch("torch.cuda.reset_peak_memory_stats"), patch("torch.cuda.synchronize"), \
                    patch("torch.cuda.max_memory_allocated", return_value=0), \
                    patch("torch.cuda.max_memory_reserved", return_value=0):
                receipt = run_parallel_observer(base, ScriptedTokenizer(), auxiliary,
                    prepared, max_rounds=max_rounds, initialization_probe=True)
            trace = [json.loads(line) for line in (Path(temporary) / "parallel-rounds.jsonl").read_text().splitlines()]
            with safe_open(str(Path(temporary) / "parallel-tensor-samples.safetensors"), framework="pt", device="cpu") as handle:
                saved = {key: handle.get_tensor(key).clone() for key in handle.keys()}

        self.assertEqual(receipt["rounds"], expected_rounds)
        self.assertEqual(len(trace), expected_rounds)
        self.assertEqual(auxiliary.rounds, expected_rounds)
        self.assertEqual(len(auxiliary.fusion.inputs), expected_rounds)
        self.assertEqual(len(base.backbone.calls), 1 + expected_rounds)
        self.assertEqual(base.backbone.cache.get_seq_length(), 3 + expected_rounds)
        self.assertEqual(base.embedding.calls, 1, "Displayed tokens must not be embedded for feedback")
        self.assertTrue(all(call["cache"] is base.backbone.cache for call in base.backbone.calls))

        for round_index, (record, actual_cat) in enumerate(zip(trace, auxiliary.fusion.inputs), 1):
            self.assertEqual(list(actual_cat.shape), [1, 5 * WIDTH])
            slots = actual_cat.reshape(1, 5, WIDTH)
            for route, ending in enumerate(endings):
                active = ending is None or round_index <= ending
                expected = auxiliary.history[round_index - 1][:, route] if active else torch.zeros(1, WIDTH)
                self.assertTrue(torch.equal(slots[:, route], expected), (round_index, route))
                self.assertEqual(record["alive_before"][route], active)
                self.assertEqual(record["emitted_ids"][route] is not None, active)
            self.assertEqual(record["cache_sequence_length"], 3 + round_index)

        for route, ending in enumerate(endings):
            actual = receipt["routes"][route]
            ended = ending is not None and ending <= max_rounds
            count = ending if ended else expected_rounds
            expected = [ScriptedVocabulary.selected_token(route, step, ending)
                        for step in range(1, count + 1)]
            self.assertEqual(actual["token_ids"], expected)
            self.assertEqual(actual["ended"], ended)
            self.assertEqual(actual["ending_round"], ending if ended else None)
            self.assertEqual(actual["ending_token_id"], EOS if ended else None)
            text_ids = expected[:-1] if ended else expected
            self.assertEqual(actual["text"], ScriptedTokenizer().decode(text_ids))
        self.assertTrue(torch.equal(saved["last_masked_CAT"], auxiliary.fusion.inputs[-1]))
        complete = all(value is not None and value <= max_rounds for value in endings)
        self.assertEqual(receipt["partial"], not complete)
        self.assertEqual(receipt["stop_cause"], "all_route_endings" if complete else "round_capacity")
        self.assertEqual(receipt["stage"], "completed" if complete else "partial")
        return receipt

    def test_staggered_endings_zero_only_subsequent_cat_slots(self):
        self.check_scenario([1, 3, 2, 3, 1], max_rounds=8)

    def test_simultaneous_endings_have_one_final_feedback_and_no_extra_round(self):
        self.check_scenario([1, 1, 1, 1, 1], max_rounds=8)

    def test_no_ending_reaches_cap_without_claiming_eos(self):
        self.check_scenario([None] * 5, max_rounds=4)


class SoftEmbeddingReadoutTests(unittest.TestCase):
    def setUp(self):
        self.embedding = torch.nn.Embedding.from_pretrained(torch.tensor([
            [2., 0., 4.], [0., 6., 0.], [-3., -3., -3.]]), freeze=True)

    def test_equal_candidates_mix_embeddings_instead_of_selecting_a_token(self):
        readout = SoftEmbeddingReadout(self.embedding, max_topk=2,
            temperature=1., top_k=2, top_p=1., min_p=0.)
        concept, visible, support = readout(torch.tensor([[0., 0., -float("inf")]]))

        torch.testing.assert_close(concept, torch.tensor([[1., 3., 2.]]), rtol=0, atol=0)
        torch.testing.assert_close(support["weights"], torch.tensor([[.5, .5]]), rtol=0, atol=0)
        self.assertEqual(set(support["ids"][0].tolist()), {0, 1})
        self.assertEqual(support["nonzero_support"].tolist(), [2])
        self.assertIn(visible.item(), (0, 1))
        self.assertFalse(torch.equal(concept, self.embedding(visible)),
                         "The visible token is a probe, not the continuous feedback")

    def test_one_surviving_candidate_is_exactly_its_original_embedding(self):
        readout = SoftEmbeddingReadout(self.embedding, max_topk=3,
            temperature=1., top_k=1, top_p=1., min_p=0.)
        concept, visible, support = readout(torch.tensor([[-2., 4., 1.]]))

        self.assertEqual(visible.tolist(), [1])
        self.assertEqual(support["nonzero_support"].tolist(), [1])
        torch.testing.assert_close(support["weights"], torch.tensor([[1., 0., 0.]]), rtol=0, atol=0)
        torch.testing.assert_close(concept, self.embedding(torch.tensor([1])), rtol=0, atol=0)


class FivePathTextSupervisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A real, entirely local tokenizer keeps ID 0 meaningful without loading
        # a model or depending on a user's installed model/cache location.
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast

        backend = Tokenizer(models.WordLevel(
            {"!": 0, "甲": 1, "乙": 2, "长": 3, "[UNK]": 4, "</think>": 5, "[PAD]": 6},
            unk_token="[UNK]"))
        backend.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
        cls.tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend,
            unk_token="[UNK]", eos_token="</think>", pad_token="[PAD]")

    def test_complete_unequal_paths_keep_zero_token_and_supervise_ending_once(self):
        paths = {"forward": "!", "backward": "甲 乙", "alternatives": "甲 ! 乙",
                 "constraints": " ".join(["长"] * 1024 + ["!"]), "counterexamples": "乙 !"}
        ending = self.tokenizer.eos_token_id
        result = encode_five_paths(self.tokenizer, paths, ending_token_id=ending)

        self.assertEqual(result["route_lengths_with_ending"], [2, 3, 4, 1026, 3])
        self.assertEqual(result["rounds"], 1026)
        self.assertEqual(self.tokenizer.encode("!", add_special_tokens=False), [0])
        self.assertEqual(result["labels"][0][0], 0)
        self.assertTrue(result["active"][0][0])
        for route, purpose in enumerate(result["purposes"]):
            body = self.tokenizer.encode(paths[purpose], add_special_tokens=False)
            length = len(body) + 1
            ids = [row[route] for row in result["token_ids"]]
            labels = [row[route] for row in result["labels"]]
            active = [row[route] for row in result["active"]]
            self.assertEqual(ids[:length], body + [ending])
            self.assertEqual(labels[:length], body + [ending])
            self.assertEqual(active[:length], [True] * length)
            self.assertEqual(ids[length:], [self.tokenizer.pad_token_id] * (1026 - length))
            self.assertEqual(labels[length:], [-100] * (1026 - length))
            self.assertEqual(active[length:], [False] * (1026 - length))
        # The long path's final original token is retained, not replaced by EOS.
        self.assertEqual(result["labels"][1024][3], 0)
        self.assertEqual(result["labels"][1025][3], ending)
        self.assertTrue(result["active"][1025][3])

    def test_existing_ending_in_source_is_rejected_instead_of_duplicated(self):
        paths = {purpose: "甲" for purpose in
                 ("forward", "backward", "alternatives", "constraints", "counterexamples")}
        paths["forward"] = "甲 </think>"
        with self.assertRaisesRegex(ValueError, "teacher_path_already_contains_ending_token:forward"):
            encode_five_paths(self.tokenizer, paths, ending_token_id=self.tokenizer.eos_token_id)


if __name__ == "__main__":
    unittest.main()
