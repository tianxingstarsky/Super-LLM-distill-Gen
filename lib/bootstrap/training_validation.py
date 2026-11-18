"""Compose optional local training without importing ML frameworks at startup."""
from lib.contexts.training.api import RunTrainingValidation


def training_validation_application():
    from lib.bootstrap.datasets import qwen35_training_encoder
    from lib.contexts.training.infrastructure.filesystem import FilesystemTrainingValidation
    from lib.contexts.training.infrastructure.transformers_backend import TransformersTrainingBackend
    import json

    def render(tokenizer, messages):
        return qwen35_training_encoder(tokenizer).render_prompt(messages, "high")

    backend = TransformersTrainingBackend(prompt_renderer=render,
        progress=lambda event: print(json.dumps(event, ensure_ascii=False, default=str), flush=True))
    return RunTrainingValidation(FilesystemTrainingValidation(backend, qwen35_training_encoder))


def grpo_validation_application():
    """Compose TRL GRPO lazily; constructing this service never starts training."""
    from lib.contexts.training.api import RunGrpoValidation
    from lib.contexts.training.infrastructure.grpo_validation import FilesystemGrpoValidation
    from lib.contexts.training.infrastructure.trl_grpo_backend import TrlGrpoBackend
    import json

    backend = TrlGrpoBackend(progress=lambda event: print(json.dumps(event, ensure_ascii=False, default=str), flush=True))
    return RunGrpoValidation(FilesystemGrpoValidation(backend))


def full_sft_application():
    from functools import partial
    from lib.bootstrap.datasets import qwen35_training_encoder
    from lib.contexts.training.api import RunFullSft
    from lib.contexts.training.infrastructure.full_sft_inputs import prepare_full_sft

    def tokenizer(model):
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)

    def train(*args, **kwargs):
        import json
        from lib.contexts.training.infrastructure.full_language import train_full_language

        return train_full_language(*args, **kwargs, progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))

    return RunFullSft(partial(prepare_full_sft, encoder_factory=qwen35_training_encoder,
                              tokenizer_factory=tokenizer), train)
