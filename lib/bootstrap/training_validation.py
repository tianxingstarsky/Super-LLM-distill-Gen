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
