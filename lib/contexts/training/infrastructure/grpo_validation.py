"""Freeze synthetic verifiable tasks and authenticate local GRPO stage receipts."""
from pathlib import Path

from ..domain.validation import fingerprint
from .filesystem import file_sha, write_json
from .trl_reward import REWARD_PROTOCOL, verify_numeric_answers


SYSTEM_MESSAGE = "You are a helpful assistant."


class FilesystemGrpoValidation:
    def __init__(self, backend):
        self.backend = backend

    def prepare(self, plan):
        import json
        from transformers import AutoTokenizer

        self.plan = plan
        self.output = Path(plan.output)
        self.output.mkdir(parents=True, exist_ok=True)
        from .grpo_tasks import load_grpo_tasks
        train, evaluation, source = load_grpo_tasks(plan)
        self.tasks = {"train": train, "evaluation": evaluation}
        targets = [task["expected_answer"] for task in (*train, *evaluation)]
        reward_probe = verify_numeric_answers([r"\boxed{" + target + "}" for target in targets], targets)
        if any(reward != 1.0 for reward in reward_probe):
            raise ValueError("grpo_gold_answer_verification_failed")
        model = self.backend.inspect(plan.model)
        self.parent_resume = None
        if plan.parent_run:
            from .grpo_checkpoint import authorize_capacity_resume
            self.parent_resume = authorize_capacity_resume(plan, model, self.tasks)
        tokenizer = AutoTokenizer.from_pretrained(plan.model, local_files_only=True, trust_remote_code=False)
        lengths = []
        for task in (*self.tasks["train"], *self.tasks["evaluation"]):
            prompt = tokenizer.apply_chat_template(self._messages(task), tokenize=False,
                add_generation_prompt=True, enable_thinking=True)
            lengths.append(len(tokenizer.encode(prompt, add_special_tokens=False)))
            if not prompt.endswith("<think>\n"):
                raise ValueError("grpo_native_thinking_template_required")
        self.manifest = {"schema_version": 1, "configuration": plan.to_dict(), "tasks": self.tasks,
            "model": model, "task_source": source, "system_message": SYSTEM_MESSAGE,
            "reward_protocol": REWARD_PROTOCOL,
            "prompt_tokens_max": max(lengths), "passed": max(lengths) <= plan.max_prompt_length,
            "epoch_policy": "one_dataset_pass_one_update_iteration_per_rollout",
            "scope": "small_fixed_numeric_task_sample_not_full_benchmark_or_general_reasoning_claim"}
        self.manifest["identity"] = fingerprint(self.manifest)
        if self.parent_resume:
            self.manifest["capacity_parent"] = {key: value for key, value in self.parent_resume.items()
                if key not in {"committed_rollouts", "unused_rollouts"}}
            self.manifest["identity"] = fingerprint({key: value for key, value in self.manifest.items() if key != "identity"})
        path = self.output / "experiment.json"
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != self.manifest:
                raise ValueError("grpo_frozen_experiment_changed")
        else:
            write_json(path, self.manifest)
        if self.parent_resume:
            from .grpo_checkpoint import reuse_capacity_baseline
            reuse_capacity_baseline(plan, self.manifest, self.parent_resume)
        return {"passed": self.manifest["passed"]}

    @staticmethod
    def _messages(task):
        return [{"role": "system", "content": SYSTEM_MESSAGE}, {"role": "user", "content": task["prompt"]}]

    def completed_stage(self, stage):
        from .grpo_checkpoint import read_committed_stage
        return read_committed_stage(self.output, self.manifest["identity"], stage)

    def execute_stage(self, stage):
        config = self.plan.to_dict()
        if stage == "grpo_train":
            return self.backend.train(self.plan.model, self.tasks["train"], self.output / stage, config,
                parent_resume=self.parent_resume)
        adapter = self.output / "grpo_train" if stage == "grpo_eval" else None
        from .grpo_checkpoint import evaluation_input
        cases, evaluation_config = evaluation_input(self.plan, self.tasks["evaluation"])
        receipt = self.backend.evaluate(self.plan.model, adapter, cases, self.output / stage, evaluation_config)
        numeric_rewards = verify_numeric_answers([row["answer"] for row in receipt["cases"]],
            [row["expected_answer"] for row in receipt["cases"]])
        for row, numeric_reward in zip(receipt["cases"], numeric_rewards, strict=True):
            complete = row["ended_with_eos"] and not row["truncated"]
            numeric = numeric_reward == 1.0
            reward = float(numeric and complete)
            row["reward"] = reward
            row["verification"] = {"method": self.manifest["reward_protocol"], "complete_output": complete,
                "numeric_answer_correct": numeric, "answer_correct": reward == 1.0,
                "reasoning_semantics": "not_independently_verified"}
        receipt["summary"]["correct"] = sum(row["reward"] == 1.0 for row in receipt["cases"])
        receipt["summary"]["accuracy"] = receipt["summary"]["correct"] / len(cases)
        return receipt

    def commit_stage(self, stage, receipt):
        files = [path for path in (self.output / stage).rglob("*") if path.is_file()
            and (path.name.startswith("adapter") or path.name.startswith("case-")
                 or path.name in {"evaluation-receipt.json", "training-receipt.json", "rollouts.jsonl"})]
        write_json(self.output / (stage + ".json"), {"identity": self.manifest["identity"],
            "receipt_sha256": fingerprint(receipt), "result": receipt,
            "artifacts": [{"path": path.relative_to(self.output).as_posix(), "sha256": file_sha(path)} for path in files]})

    def report(self, *, status, issue=None):
        from .grpo_report import render_grpo_report
        from ..application.grpo import GRPO_STAGES
        stages = {stage: receipt for stage in GRPO_STAGES if (receipt := self.completed_stage(stage)) is not None}
        report = render_grpo_report(self.manifest, stages, self.output)
        report.update(status=status, issue=issue, identity=self.manifest["identity"])
        write_json(self.output / "report.json", report)
        return report
