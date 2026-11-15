"""Freeze local inputs and bridge the validation use case to a training backend."""
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import time

from ..domain.validation import assert_disjoint_questions, fingerprint, validate_supervision


def read_rows(path):
    with Path(path).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_record(row, system_message):
    from lib.contexts.datasets.contracts import adapt_reasoning_record
    candidate = adapt_reasoning_record(row) or deepcopy(row)
    if not isinstance(candidate.get("messages"), list):
        if isinstance(candidate.get("conversations"), list):
            candidate["messages"] = deepcopy(candidate["conversations"])
        elif all(isinstance(candidate.get(key), str) for key in ("question", "reasoning", "answer")):
            candidate["messages"] = [{"role": "user", "content": candidate["question"]},
                {"role": "assistant", "content": candidate["answer"], "reasoning_content": candidate["reasoning"]}]
        else:
            raise ValueError("training_validation_canonical_record_required")
    if system_message and not any(message.get("role") == "system" for message in candidate["messages"]):
        candidate["messages"].insert(0, {"role": "system", "content": system_message})
        candidate["training_validation_system_origin"] = "experiment_added"
    return candidate


class FilesystemTrainingValidation:
    def __init__(self, backend, encoder_factory):
        self.backend, self.encoder_factory = backend, encoder_factory

    def prepare(self, plan):
        from transformers import AutoTokenizer
        from lib.contexts.datasets.api import iter_training_examples, default_control_text_config

        self.plan = plan
        self.output = Path(plan.output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.tokenizer = AutoTokenizer.from_pretrained(plan.model, local_files_only=True, trust_remote_code=False)
        controls = default_control_text_config()
        controls["enabled"] = False
        self.encoder = self.encoder_factory(self.tokenizer, max_length=plan.max_length,
            control_text_config=controls)
        raw_train, raw_validation = read_rows(plan.sft), read_rows(plan.validation)
        self.cases = read_rows(plan.evaluation)
        train = [canonical_record(row, plan.system_message) for row in raw_train]
        validation = [canonical_record(row, plan.system_message) for row in raw_validation]
        questions = lambda rows: [m["content"] for row in rows for m in row["messages"] if m["role"] == "user"]
        assert_disjoint_questions(questions(train), questions(validation), questions(self.cases))
        model_facts = self.backend.inspect(plan.model)
        self.sft_examples, self.validation_examples, audits = [], [], []
        for split, records, destination in (("train", train, self.sft_examples),
                                             ("validation", validation, self.validation_examples)):
            for record_index, record in enumerate(records):
                encoded = list(iter_training_examples([record], self.encoder, default_effort="high"))
                for turn in encoded:
                    text = self.tokenizer.decode(turn["input_ids"], skip_special_tokens=False)
                    target = self.tokenizer.decode([x for x in turn["labels"] if x != -100], skip_special_tokens=False)
                    eos = self.tokenizer.eos_token_id
                    ends = [i for i, x in enumerate(turn["input_ids"]) if x == eos]
                    tail_ids = turn["input_ids"][ends[-1] + 1:] if ends else []
                    if self.tokenizer.decode(tail_ids, skip_special_tokens=False).strip():
                        raise ValueError("training_validation_nonwhite_after_eos")
                    evidence = validate_supervision(turn, eos_token_id=eos, trailing_whitespace_ids=tuple(tail_ids))
                    original_assistant = record["messages"][turn["metadata"]["source_message_index"]]
                    original_reasoning = next((original_assistant[key] for key in
                        ("reasoning_content", "thinking", "reasoning") if original_assistant.get(key)), None)
                    if not isinstance(original_reasoning, str) or original_reasoning not in target or original_assistant["content"] not in target:
                        raise ValueError("training_validation_reasoning_or_answer_lost")
                    if any(m["content"] not in text for m in record["messages"] if m["role"] == "system"):
                        raise ValueError("training_validation_system_lost")
                    audits.append({"split": split, "record_index": record_index, "id": record.get("id"),
                        "system_origin": record.get("training_validation_system_origin", "source"),
                        "reasoning_sha256": fingerprint(original_reasoning),
                        "answer_sha256": fingerprint(original_assistant["content"]),
                        "rendered_text": text, "supervised_text": target, **evidence})
                    destination.append({key: turn[key] for key in ("input_ids", "attention_mask", "labels")})
        # One frozen order for SFT in both arms; never shuffle CPT into SFT.
        random.Random(plan.seed).shuffle(self.sft_examples)
        corpus = Path(plan.cpt).read_text(encoding="utf-8")
        corpus_ids = self.tokenizer.encode(corpus, add_special_tokens=False)
        document_eos = self.tokenizer.convert_tokens_to_ids("<|endoftext|>")
        corpus_ids.append(document_eos)
        self.cpt_examples = [{"input_ids": corpus_ids[i:i + plan.cpt_block_size],
            "attention_mask": [1] * len(corpus_ids[i:i + plan.cpt_block_size]),
            "labels": corpus_ids[i:i + plan.cpt_block_size]}
            for i in range(0, len(corpus_ids), plan.cpt_block_size)]
        if [token for row in self.cpt_examples for token in row["input_ids"]] != corpus_ids:
            raise ValueError("training_validation_cpt_tokens_lost")
        sources = {name: {"file": Path(path).name, "bytes": Path(path).stat().st_size, "sha256": file_sha(path)}
            for name, path in (("sft", plan.sft), ("validation", plan.validation), ("cpt", plan.cpt), ("evaluation", plan.evaluation))}
        self.configuration = {"epochs": 1.0, "seed": plan.seed, "max_length": plan.max_length,
            "batch_size": plan.batch_size, "gradient_accumulation_steps": plan.gradient_accumulation,
            "learning_rate": plan.learning_rate, "lora_rank": plan.lora_rank, "lora_alpha": plan.lora_rank * 2,
            "qlora": True, "gradient_checkpointing": True, "save_steps": 1, "max_new_tokens": plan.max_new_tokens,
            "temperature": 1.0, "top_p": .95, "top_k": 20, "min_p": 0.0,
            "repetition_penalty": 1.0, "requested_presence_penalty": 1.5}
        self.identity = fingerprint([sources, model_facts["files"], model_facts["dependencies"],
            self.encoder.provenance, self.configuration, plan.system_message, plan.cpt_block_size])
        frozen = self.output / "experiment.json"
        if frozen.exists() and json.loads(frozen.read_text(encoding="utf-8"))["identity"] != self.identity:
            raise ValueError("training_validation_frozen_experiment_changed")
        write_json(frozen, {"schema_version": 1, "identity": self.identity, "configuration": asdict(plan),
            "sources": sources, "model": model_facts, "epoch_policy": "one_ordered_pass_over_each_arm_union",
            "sft_source_kind": "recorded_authored_reasoning_not_assumed_native",
            "evaluation_scope": "within_paper_heldout_tasks_not_unseen_knowledge"})
        self.preflight = {"passed": True, "identity": self.identity, "train_records": len(train),
            "validation_records": len(validation), "test_cases": len(self.cases),
            "sft_training_examples": len(self.sft_examples), "cpt_training_blocks": len(self.cpt_examples),
            "cpt_tokens": len(corpus_ids), "cpt_document_eos": document_eos, "sft_turn_eos": self.tokenizer.eos_token_id,
            "maximum_sft_tokens": max(len(x["input_ids"]) for x in self.sft_examples),
            "supervised_sft_tokens": sum(sum(t != -100 for t in x["labels"]) for x in self.sft_examples),
            "system_preserved": True, "full_reasoning_preserved": True, "silent_truncation": False,
            "encoder": self.encoder.provenance, "cuda_available": model_facts["cuda_available"]}
        if not self.preflight["cuda_available"]:
            self.preflight["passed"] = False
        write_json(self.output / "preflight.json", self.preflight)
        write_json(self.output / "supervision-audit.json", audits)
        return self.preflight

    def completed_stage(self, stage):
        path = self.output / (stage + ".json")
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["identity"] != self.identity:
            raise ValueError("training_validation_stage_identity_changed")
        if record["result"]["status"] != "completed":
            return None
        for artifact in record.get("artifacts", []):
            file = self.output / artifact["path"]
            if not file.is_file() or file_sha(file) != artifact["sha256"]:
                raise ValueError("training_validation_artifact_changed")
        return record["result"]

    def execute_stage(self, stage):
        print(json.dumps({"stage": stage, "event": "started"}), flush=True)
        if stage.endswith("_train"):
            examples = self.sft_examples if stage == "sft_train" else self.cpt_examples + self.sft_examples
            return self.backend.train(self.plan.model, examples, self.output / stage, self.configuration)
        adapter = None if stage == "baseline" else self.output / stage.replace("_eval", "_train")
        return self.backend.evaluate(self.plan.model, adapter, self.cases, self.output / stage, self.configuration,
            validation_examples=self.validation_examples)

    def commit_stage(self, stage, result):
        directory = self.output / stage
        names = list(directory.glob("adapter*")) if stage.endswith("_train") else list(directory.glob("case-*.json"))
        artifacts = [{"path": file.relative_to(self.output).as_posix(), "sha256": file_sha(file)}
                     for file in names if file.is_file()]
        write_json(self.output / (stage + ".json"), {"identity": self.identity, "result": result, "artifacts": artifacts})

    def report(self, *, status, issue=None):
        from .report import write_training_report
        return write_training_report(self.output, status=status, issue=issue)
