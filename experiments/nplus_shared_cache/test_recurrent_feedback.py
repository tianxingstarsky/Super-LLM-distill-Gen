"""CPU protocol checks on a real tiny HF Qwen backbone; no quality/training claim."""
import sys
import unittest

import torch
from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5TextConfig
from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5TextModel

from experiments.nplus_shared_cache.differentiable_cache import DifferentiableQwen35Cache
from experiments.nplus_shared_cache.parallel_observer import route_transition
from experiments.nplus_shared_cache.recurrent_heads import RecurrentDirectionHeads


class RecurrentFeedbackProtocolTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows sharing-lock protocol")
    def test_replace_waits_for_native_windows_read_handle_without_delete_sharing(self):
        import ctypes
        from ctypes import wintypes
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from threading import Timer
        from experiments.nplus_shared_cache.runner import replace_with_retry

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
        with TemporaryDirectory(prefix="nplus-native-sharing-") as directory:
            target = Path(directory) / "progress.json"
            source = target.with_suffix(".json.tmp")
            target.write_text('{"stage":"old"}', encoding="utf-8")
            expected = {"stage": "new", "round": 8, "label": "共享缓存"}
            encoded = json.dumps(expected, ensure_ascii=False)
            source.write_text(encoded, encoding="utf-8")
            # GENERIC_READ + FILE_SHARE_READ, intentionally no FILE_SHARE_DELETE.
            handle = create(str(target), 0x80000000, 1, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            closed, errors = [], []

            def release():
                if not close(handle):
                    errors.append(ctypes.get_last_error())
                closed.append(True)

            timer = Timer(.05, release)
            try:
                # Establish that the real OS handle actually prevents replace.
                with self.assertRaises(PermissionError) as locked:
                    source.replace(target)
                self.assertIn(locked.exception.winerror, (5, 32, 33))
                timer.start()
                replace_with_retry(source, target)
                self.assertEqual(json.loads(target.read_text("utf-8")), expected)
                self.assertEqual(target.read_text("utf-8"), encoded)
                self.assertFalse(source.exists())
            finally:
                if timer.ident is not None:
                    timer.join()
                else:
                    release()
            self.assertTrue(closed, "The native handle must have been released")
            self.assertFalse(errors, errors)

    def test_real_qwen_four_steps_match_block_values_and_input_gradients(self):
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(17)
            config = Qwen3_5TextConfig(vocab_size=32, hidden_size=16,
                intermediate_size=32, num_hidden_layers=2, num_attention_heads=2,
                num_key_value_heads=1, head_dim=8,
                layer_types=["linear_attention", "full_attention"],
                linear_num_key_heads=2, linear_num_value_heads=2,
                linear_key_head_dim=8, linear_value_head_dim=8,
                linear_conv_kernel_dim=4, attention_dropout=0.,
                rope_parameters={"rope_type": "default", "rope_theta": 10000.,
                    "partial_rotary_factor": .5, "mrope_section": [1, 1, 0]})
            model = Qwen3_5TextModel(config).to(device="cpu", dtype=torch.float32)
            model.requires_grad_(False).eval()
            prefix = torch.randn(1, 3, 16)
            actual_inputs = torch.randn(1, 4, 16)
            probe = torch.randn(1, 16)

            def run(block):
                cache = DifferentiableQwen35Cache(config)
                with torch.no_grad():
                    initial = model(inputs_embeds=prefix,
                        attention_mask=torch.ones(1, 3, dtype=torch.long),
                        position_ids=torch.arange(3).unsqueeze(0),
                        past_key_values=cache, use_cache=True)
                self.assertIs(initial.past_key_values, cache)
                prefix_kv = {name: getattr(cache.layers[1], name).clone()
                    for name in ("keys", "values")}
                inputs = actual_inputs.clone().requires_grad_()
                outputs = []
                old_conv = []
                for start in range(0, 4, block):
                    previous = cache.layers[0].conv_states[0]
                    old_conv.append((previous, previous._version))
                    result = model(inputs_embeds=inputs[:, start:start+block],
                        attention_mask=torch.ones(1, 3+start+block, dtype=torch.long),
                        position_ids=torch.arange(3+start, 3+start+block).unsqueeze(0),
                        past_key_values=cache, use_cache=True)
                    self.assertIs(result.past_key_values, cache)
                    outputs.append(result.last_hidden_state)
                output = torch.cat(outputs, dim=1)
                # Only the last output/state is supervised: early input gradients
                # therefore require actual temporal paths, not separate losses.
                loss = (output[:, -1] * probe).sum() \
                    + .01 * cache.layers[0].recurrent_states[0].square().sum()
                gradient = torch.autograd.grad(loss, inputs)[0]
                self.assertTrue(torch.isfinite(gradient).all())
                self.assertTrue((gradient.norm(dim=-1) > 0).all())
                self.assertTrue(all(state._version == version for state, version in old_conv))
                self.assertEqual(cache.get_seq_length(), 7)
                for name, before in prefix_kv.items():
                    self.assertTrue(torch.equal(getattr(cache.layers[1], name)[..., :3, :], before))
                saved = {"keys": cache.layers[1].keys.clone(), "values": cache.layers[1].values.clone(),
                    "conv": cache.layers[0].conv_states[0].clone(),
                    "recurrent": cache.layers[0].recurrent_states[0].clone()}
                receipt = cache.detach_history()
                self.assertEqual(receipt["sequence_length_before"], 7)
                self.assertEqual(receipt["sequence_length_after"], 7)
                current = {"keys": cache.layers[1].keys, "values": cache.layers[1].values,
                    "conv": cache.layers[0].conv_states[0], "recurrent": cache.layers[0].recurrent_states[0]}
                for name, value in current.items():
                    self.assertTrue(torch.equal(value, saved[name]))
                    self.assertIsNone(value.grad_fn)
                    self.assertFalse(value.requires_grad)
                return output.detach(), gradient.detach()

            stepped, stepped_gradient = run(1)
            blocked, blocked_gradient = run(4)
            # FP32 official chunk/recurrent paths may differ by rounding;
            # these tight limits check agreement rather than text/top-1 alone.
            torch.testing.assert_close(stepped, blocked, rtol=1e-5, atol=1e-6)
            torch.testing.assert_close(stepped_gradient, blocked_gradient, rtol=1e-5, atol=1e-6)

    def test_answer_gradient_reaches_image_prompt_and_native_rotary_positions_continue(self):
        from transformers import Qwen3_5Config, Qwen3_5ForConditionalGeneration
        from experiments.nplus_shared_cache import ot_latent_model as routes
        from experiments.nplus_shared_cache.recurrent_runtime import _forward

        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(19)
            config = Qwen3_5Config(image_token_id=7, video_token_id=10, vision_start_token_id=6,
                vision_end_token_id=8, text_config=dict(vocab_size=32, hidden_size=16,
                intermediate_size=32, num_hidden_layers=2, num_attention_heads=2, head_dim=8,
                num_key_value_heads=1, layer_types=["linear_attention", "full_attention"],
                linear_num_key_heads=2, linear_num_value_heads=2, linear_key_head_dim=8,
                linear_value_head_dim=8, linear_conv_kernel_dim=4,
                rope_parameters={"rope_type":"default", "rope_theta":10000.,
                    "partial_rotary_factor":.5, "mrope_section":[1,1,0]}),
                vision_config=dict(depth=1, hidden_size=16, intermediate_size=32, num_heads=2,
                out_hidden_size=16, num_position_embeddings=64, patch_size=4))
            base = Qwen3_5ForConditionalGeneration(config).float().eval()
            heads = routes.LatentRoutes(routes.RouteConfig(5, 16, 8, max_rounds=2))
            inputs = dict(input_ids=torch.tensor([[5,6,7,7,7,7,8,9]]),
                attention_mask=torch.ones(1,8,dtype=torch.long),
                mm_token_type_ids=torch.tensor([[0,0,1,1,1,1,0,0]]),
                pixel_values=torch.randn(16,3*2*4*4), image_grid_thw=torch.tensor([[1,4,4]]))
            teacher_mask = torch.ones((2,5), dtype=torch.bool)
            input_embeddings = []
            def capture_prompt(module, args, embeddings):
                if torch.equal(args[0], inputs["input_ids"]):
                    embeddings.retain_grad()
                    input_embeddings.append(embeddings)
            handle = base.get_input_embeddings().register_forward_hook(capture_prompt)
            try:
                roll = routes.latent_rollout(base, heads, None, teacher_alive=teacher_mask, multimodal_inputs=inputs)
            finally:
                handle.remove()
            answer_input = base.get_input_embeddings()(torch.tensor([[11]]))
            expected = base.model.compute_3d_position_ids(None, answer_input, past_key_values=roll.cache)
            self.assertLess(roll.cache.image_rope_delta.item(), 0)
            captured = []
            def inspect(module, args, kwargs):
                captured.append(kwargs["position_ids"])
            handle = base.model.language_model.register_forward_pre_hook(inspect, with_kwargs=True)
            try:
                answer = _forward(base, roll.cache, answer_input)
            finally:
                handle.remove()
            torch.testing.assert_close(captured[0][1:], expected)
            torch.nn.functional.cross_entropy(base.lm_head(answer[0]), torch.tensor([17])).backward()
            for name, parameter in base.model.visual.named_parameters():
                self.assertIsNotNone(parameter.grad, name)
                self.assertGreater(parameter.grad.norm().item(), 0, name)
            self.assertGreater(input_embeddings[0].grad[0,0].norm().item(), 0)
            self.assertGreater(heads.merge.weight.grad.norm().item(), 0)

    def test_ending_participates_then_next_cat_slot_and_gradient_are_zero(self):
        heads = torch.arange(1., 1+5*4).reshape(1, 5, 4).requires_grad_()
        alive = torch.ones(5, dtype=torch.bool)
        current, next_alive = route_transition(heads, torch.tensor([2, 7, 8, 9, 10]), alive, {2})
        self.assertTrue(torch.equal(current, heads))
        self.assertEqual(next_alive.tolist(), [False, True, True, True, True])
        following, _ = route_transition(heads, torch.tensor([11, 7, 8, 9, 10]), next_alive, {2})
        cat = following.flatten(1)
        self.assertTrue(torch.equal(cat[:, :4], torch.zeros(1, 4)))
        self.assertTrue(torch.equal(cat[:, 4:], heads[:, 1:].flatten(1)))
        gradient = torch.autograd.grad(cat.sum(), heads)[0]
        self.assertTrue(torch.equal(gradient[:, 0], torch.zeros(1, 4)))
        self.assertTrue(torch.equal(gradient[:, 1:], torch.ones(1, 4, 4)))

    def test_private_route_history_changes_only_its_own_next_state(self):
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(29)
            heads = RecurrentDirectionHeads(width=16, memory_width=8, rank=4)
            heads.set_goal_states(torch.randn(5, 16))
            shared = torch.randn(2, 16)
            q0, memory0 = heads.initialize(shared)
            q1, memory1 = heads.forward_step(shared, q0, memory0)
            expected_q, expected_memory = heads.forward_step(shared, q1, memory1)
            # Change an actual private input, holding the shared backbone input
            # and all four other route histories exactly fixed.
            for source in ("previous_vector", "memory"):
                previous, memory = q1.clone(), memory1.clone()
                if source == "previous_vector":
                    previous[:, 0, 2] += 1
                else:
                    memory[:, 0, 1] += 1
                actual_q, actual_memory = heads.forward_step(shared, previous, memory)
                self.assertFalse(torch.equal(actual_q[:, 0], expected_q[:, 0]), source)
                self.assertFalse(torch.equal(actual_memory[:, 0], expected_memory[:, 0]), source)
                self.assertTrue(torch.equal(actual_q[:, 1:], expected_q[:, 1:]), source)
                self.assertTrue(torch.equal(actual_memory[:, 1:], expected_memory[:, 1:]), source)


if __name__ == "__main__":
    unittest.main()
