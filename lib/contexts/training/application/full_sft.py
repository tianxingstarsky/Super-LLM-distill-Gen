"""Preflight and train one pass over the supplied native assistant targets."""
from ..domain.full_sft import validate_full_sft_optimizer


class RunFullSft:
    def __init__(self, prepare, train):
        self.prepare = prepare
        self.train = train

    def execute(self, model, sft, output, *, action="preflight", max_length=None,
                learning_rate=8e-6, gradient_accumulation=8, seed=20261119):
        if action not in {"run", "preflight"}:
            raise ValueError("unknown full SFT action")
        if max_length is not None and (type(max_length) is not int or max_length <= 0):
            raise ValueError("max length must be positive")
        validate_full_sft_optimizer(learning_rate, gradient_accumulation)
        with self.prepare(model, sft, output, max_length=max_length, for_training=action == "run") as (report, examples):
            if action == "preflight":
                return report
            return self.train(model, examples, output, source_sha256=report["source_sha256"],
                              learning_rate=learning_rate, gradient_accumulation=gradient_accumulation, seed=seed)
