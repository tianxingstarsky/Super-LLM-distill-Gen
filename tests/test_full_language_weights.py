"""Native full-SFT readout, checkpoint and package behavior in optional training environments."""

import pytest

from lib.contexts.training.infrastructure import full_language
from lib.contexts.training.infrastructure.filesystem import file_sha, write_json
from lib.contexts.training.infrastructure.qwen35_weights import load


def test_blockwise_supervision_matches_native_loss_and_all_tied_language_gradients():
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    if not hasattr(transformers, "Qwen3_5ForCausalLM"):
        pytest.skip("native Qwen3.5 training runtime required")
    config = transformers.Qwen3_5TextConfig(
        vocab_size=48,
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=16,
        linear_key_head_dim=16,
        linear_value_head_dim=16,
        linear_num_key_heads=2,
        linear_num_value_heads=2,
        layer_types=["linear_attention", "full_attention"],
        tie_word_embeddings=True,
        use_cache=False,
    )
    torch.manual_seed(23)
    model = transformers.Qwen3_5ForCausalLM(config).train()
    inputs = torch.randint(2, 48, (2, 13))
    labels = inputs.clone()
    labels[:, :5] = -100
    labels[1, -3:] = -100
    native = model(input_ids=inputs, labels=labels, use_cache=False).loss
    native.backward()
    reference = {name: p.grad.clone() for name, p in model.named_parameters()}
    model.zero_grad(set_to_none=True)
    hidden = model.model(input_ids=inputs, use_cache=False).last_hidden_state
    loss = full_language.assistant_causal_loss(
        hidden, model.lm_head.weight, labels, chunk=3
    )
    loss.backward()
    torch.testing.assert_close(loss, native)
    for name, p in model.named_parameters():
        torch.testing.assert_close(p.grad, reference[name], rtol=1e-4, atol=1e-6)


def test_full_package_preserves_original_visual_weights_and_reloads_trained_language(
    tmp_path,
):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
        pytest.skip("native Qwen3.5 training runtime required")
    from tokenizers import Tokenizer, models

    base, state, destination = (
        tmp_path / "base",
        tmp_path / "state",
        tmp_path / "export",
    )
    state.mkdir()
    model = transformers.Qwen3_5ForConditionalGeneration(
        transformers.Qwen3_5Config(
            text_config=dict(
                vocab_size=32,
                hidden_size=16,
                intermediate_size=32,
                num_hidden_layers=1,
                num_attention_heads=2,
                num_key_value_heads=1,
                head_dim=8,
                linear_key_head_dim=8,
                linear_value_head_dim=8,
                linear_num_key_heads=2,
                linear_num_value_heads=2,
                layer_types=["linear_attention"],
                tie_word_embeddings=True,
                eos_token_id=2,
            ),
            vision_config=dict(
                depth=1,
                hidden_size=16,
                intermediate_size=32,
                num_heads=2,
                out_hidden_size=16,
                patch_size=2,
            ),
            tie_word_embeddings=True,
        )
    )
    model.bfloat16()
    for name, param in model.named_parameters():
        if name.endswith((".linear_attn.A_log", ".linear_attn.norm.weight")):
            param.data = param.data.float() + 0.00071
    model.save_pretrained(base)
    tokens = Tokenizer(
        models.WordLevel({"<pad>": 0, "x": 1, "<eos>": 2, "<end>": 3}, unk_token="x")
    )
    transformers.PreTrainedTokenizerFast(
        tokenizer_object=tokens, pad_token="<pad>", eos_token="<end>"
    ).save_pretrained(base)
    original, _ = load(base, device="cpu")
    named = [
        (name, p)
        for name, p in original.named_parameters()
        if name.startswith("model.language_model.")
    ]
    identity = dict(
        source_model={p.name: file_sha(p) for p in base.glob("*.safetensors")},
        assets={
            p.name: file_sha(p) for p in base.iterdir() if p.name.endswith(".json")
        },
    )
    progress = dict(rows=1, steps=1)
    snapshot = state / "state-1.pt"
    torch.save(
        dict(
            identity=identity,
            progress=progress,
            masters=[p.float().detach() + 0.03125 for _, p in named],
        ),
        snapshot,
    )
    write_json(
        state / "current.json",
        dict(
            file=snapshot.name,
            sha256=file_sha(snapshot),
            bytes=snapshot.stat().st_size,
            progress=progress,
            identity=identity,
        ),
    )
    completed = dict(
        rows=1,
        targets=3,
        identity=identity,
        actual_epochs=1,
        parameter_names=[n for n, _ in named],
        trainable_parameters=sum(p.numel() for _, p in named),
    )
    write_json(state / "training-receipt.json", completed)
    package = full_language._export(base, state, destination, completed)
    assert package["tied_output_embedding"] and package["fp32_gdn"] == 2
    assert (
        package["changed_language_tensors"] == len(named)
        and package["passthrough_tensors_verified"] > 0
    )
    assert package == full_language._export(base, state, destination, completed)
    new, tok = load(destination, device="cpu")
    assert new.lm_head.weight is new.get_input_embeddings().weight
    assert torch.equal(
        new.lm_head.weight, (original.lm_head.weight.float() + 0.03125).bfloat16()
    )
    assert {2, tok.eos_token_id}.issubset(new.generation_config.eos_token_id)
