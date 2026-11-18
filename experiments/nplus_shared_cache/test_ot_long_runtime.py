"""CPU generation protocol on the real Qwen GDN/attention cache."""
from types import SimpleNamespace
import unittest

import torch
from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5TextConfig
from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5TextModel

from . import ot_long_model as model


class TinyTokenizer:
    pad_token_id = 1
    eos_token_id = 0

    def apply_chat_template(self, *args, **kwargs):
        return "prompt"

    def encode(self, text, **kwargs):
        return [1, 2, 3] if text == "prompt" else [4, 5]

    def decode(self, ids, **kwargs):
        return " ".join(map(str, ids))


class LongRuntimeTests(unittest.TestCase):
    def test_staggered_halting_beyond_four_and_real_backbone_call_accounting(self):
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(349)
            config = Qwen3_5TextConfig(
                vocab_size=32, hidden_size=16, intermediate_size=32, num_hidden_layers=2,
                num_attention_heads=2, num_key_value_heads=1, head_dim=8,
                layer_types=["linear_attention", "full_attention"], linear_num_key_heads=2,
                linear_num_value_heads=2, linear_key_head_dim=8, linear_value_head_dim=8,
                linear_conv_kernel_dim=4, rope_parameters={"rope_type": "default", "rope_theta": 10000.,
                "partial_rotary_factor": .5, "mrope_section": [1, 1, 0]})
            native = Qwen3_5TextModel(config).eval()
            readout = torch.nn.Linear(16, 32, bias=False)
            heads = model.LatentRoutes(model.RouteConfig(5, 16, 8))
            base = SimpleNamespace(model=SimpleNamespace(language_model=native),
                config=SimpleNamespace(text_config=config), generation_config=SimpleNamespace(eos_token_id=0),
                lm_head=readout, get_input_embeddings=native.get_input_embeddings)
            # The real GRU halves each positive initial state. Each slot crosses
            # the same learned halting plane at a different predictable time.
            with torch.no_grad():
                for param in heads.parameters():
                    param.zero_()
                heads.route_codes[:, 0] = torch.atanh(torch.tensor([.012, .025, .05, .1, .8]))
                heads.halt.weight[0, 0] = -1.
                heads.halt.bias.fill_(.01)
                heads.gain.fill_(1.)
                readout.weight.zero_()
            backbone_calls, observations, fused_inputs = [], [], []
            handlers = [
                native.register_forward_hook(lambda *_: backbone_calls.append(1)),
                readout.register_forward_hook(lambda *_: observations.append(1)),
                heads.merge.register_forward_pre_hook(lambda _, args: fused_inputs.append(args[0].clone())),
            ]
            try:
                result = model.generate(base, TinyTokenizer(), heads, "question", latent_budget=6,
                                        max_new_tokens=3, matched_total_calls=12)
                self.assertEqual(result["per_route_stop"], [1, 2, 3, 4, None])
                self.assertEqual(result["stop"], "thinking_incomplete")
                self.assertEqual((result["token_ids"], observations), ([], []))
                self.assertEqual((result["natural_calls"], result["benchmark_padding_calls"]), (7, 5))
                self.assertEqual(len(backbone_calls), result["total_calls"])
                for i, fused in enumerate(fused_inputs):
                    slot = fused.reshape(5, 8)[:, 0]
                    self.assertEqual((slot != 0).tolist(), [i < stop for stop in (1, 2, 3, 4, 7)])
                backbone_calls.clear()
                result = model.generate(base, TinyTokenizer(), heads, "question", latent_budget=10,
                                        max_new_tokens=3, observe=True, matched_total_calls=12)
                self.assertEqual(result["per_route_stop"], [1, 2, 3, 4, 7])
                self.assertEqual(result["token_ids"], [0])
                self.assertEqual(result["stop"], "eos")
                self.assertEqual((len(backbone_calls), len(observations)), (12, 8))
                for i, row in enumerate(result["observation_token_ids"]):
                    self.assertEqual(row, [0 if i < stop else -1 for stop in (1, 2, 3, 4, 7)])
                diagnostic = model.generate(base, TinyTokenizer(), heads, "question", latent_budget=1,
                                            diagnostic_answer_on_partial=True)
                self.assertEqual(diagnostic["token_ids"], [0])
                self.assertEqual(diagnostic["thinking_status"], "incomplete")
                self.assertTrue(diagnostic["diagnostic_answer"])
            finally:
                for handler in handlers:
                    handler.remove()


if __name__ == "__main__":
    unittest.main()
