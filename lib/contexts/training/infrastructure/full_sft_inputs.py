"""Freeze the exact JSONL byte stream and encode native, loss-masked targets."""
from array import array
from contextlib import contextmanager, nullcontext
import hashlib
import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryFile

from filelock import FileLock

from lib.contexts.datasets.api import default_control_text_config, iter_training_examples
from lib.contexts.datasets.contracts import TRAINING_EFFORTS, assistant_target_repetition
from lib.io_utils import atomic_json
from ..domain.validation import validate_supervision
from .qwen35_weights import weight_map


class NativeAssistantTargets:
    def __init__(self, encoder):
        self.encoder = encoder

    def encode_turn(self, messages, control, **options):
        # The published loader normalizes assistant reasoning to `thinking`.
        # These selectors drive the template switch, independent of source effort.
        selector = "high" if messages[-1].get("thinking") else "low"
        return self.encoder.encode_turn(messages, selector, **options)


class IndexedExamples:
    """Retain offsets in RAM while the trainer seeks individual prepared turns."""
    def __init__(self, stream):
        self.stream = stream
        self.offsets = array("Q")

    def append(self, example):
        self.offsets.append(self.stream.tell())
        values = {key: example[key] for key in ("input_ids", "attention_mask", "labels")}
        self.stream.write(json.dumps(values, separators=(",", ":")).encode("ascii") + b"\n")

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        self.stream.seek(self.offsets[index])
        return json.loads(self.stream.readline())


TARGET_POLICY = "native-assistant-prose-cycles/v1"


def accept_training_target(prefix, *, line_number, report):
    evidence = assistant_target_repetition(prefix)
    if not evidence:
        return True
    report["held_turns"] += 1
    if len(report["held_examples"]) < 16:
        report["held_examples"].append({"line": line_number, "message_index": len(prefix) - 1, **evidence})
    return False


@contextmanager
def prepare_full_sft(model, sft, output, *, max_length, for_training, encoder_factory, tokenizer_factory):
    model, source, output = Path(model).resolve(), Path(sft).resolve(), Path(output).resolve()
    if output.is_relative_to(model) or model.is_relative_to(output / "model"):
        raise ValueError("the output must keep the original model directory separate")
    config = json.loads((model / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3_5":
        raise ValueError("this native full-language trainer requires a local Qwen3.5 model")
    native_limit = config.get("text_config", {}).get("max_position_embeddings")
    if type(native_limit) is not int or native_limit <= 0:
        raise ValueError("Qwen3.5 config is missing a valid native text context length")
    for shard in set(weight_map(model).values()):
        if not (model / shard).is_file():
            raise FileNotFoundError(f"missing model weight shard: {shard}")
    if max_length is not None and max_length > native_limit:
        raise ValueError("max length exceeds the model's native context")
    token_limit = max_length or native_limit
    tokenizer = tokenizer_factory(model)
    switches = {effort: effort != "low" for effort in TRAINING_EFFORTS}
    controls = default_control_text_config()
    controls["enabled"] = False
    encoder = encoder_factory(tokenizer, max_length=token_limit, effort_mapping=switches, control_text_config=controls)
    targets = NativeAssistantTargets(encoder)
    trailing = tokenizer.encode("\n", add_special_tokens=False)
    source_hash = hashlib.sha256()
    report = dict(status="preflight_passed", epochs=1, source_rows=0, assistant_turns=0,
                  thinking_turns=0, answer_only_turns=0, supervised_tokens=0,
                  maximum_input_tokens=0, max_length=token_limit, eos_token_id=tokenizer.eos_token_id)
    policy_file = output / "full-sft-target-policy.json"
    saved_policy = json.loads(policy_file.read_bytes()) if policy_file.is_file() else None
    if saved_policy is not None and saved_policy.get("policy") != TARGET_POLICY:
        raise ValueError("unsupported frozen full-SFT target policy")
    # Legacy runs froze encoded targets before this policy existed. Never change
    # their eligibility when the same checkpoint is resumed.
    screen_targets = saved_policy is not None or not (output / "full-training" / "identity.json").is_file()
    if screen_targets:
        report.update(target_policy=TARGET_POLICY, held_turns=0, held_examples=[])
    if for_training:
        output.mkdir(parents=True, exist_ok=True)
    with TemporaryFile(dir=output) if for_training else nullcontext() as spool:
        examples = IndexedExamples(spool) if for_training else None
        with source.open("rb") as rows:
            for line_number, line in enumerate(rows, 1):
                source_hash.update(line)
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8-sig" if line_number == 1 else "utf-8"))
                    if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                        raise ValueError("a text SFT record with messages is required")
                    if row.get("status") == "accepted":
                        # The guided workflow marks approved exports as accepted.
                        row = {**row, "status": "eligible"}
                    selector = partial(accept_training_target, line_number=line_number, report=report) if screen_targets else None
                    for turn in iter_training_examples([row], targets, default_effort="medium", accept_target=selector):
                        audit = validate_supervision(turn, eos_token_id=tokenizer.eos_token_id,
                                                     trailing_whitespace_ids=trailing)
                        mode = "thinking_turns" if turn["metadata"]["enable_thinking"] else "answer_only_turns"
                        report[mode] += 1
                        report["assistant_turns"] += 1
                        if "guided_training_target" in row:
                            info = row["guided_training_target"]
                            if not isinstance(info, dict) or not isinstance(info.get("objective"), str):
                                raise ValueError("guided training target must identify its objective")
                            by_objective = report.setdefault("guided_targets_by_objective", {})
                            by_objective[info["objective"]] = by_objective.get(info["objective"], 0) + 1
                        report["supervised_tokens"] += audit["supervised_tokens"]
                        report["maximum_input_tokens"] = max(report["maximum_input_tokens"], audit["input_tokens"])
                        if examples is not None:
                            examples.append(turn)
                except ValueError as error:
                    raise ValueError(f"SFT JSONL line {line_number}: {error}") from error
                report["source_rows"] += 1
        if not report["assistant_turns"]:
            raise ValueError(f"SFT source contains no assistant targets ({report.get('held_turns', 0)} held for repetition)")
        report["source_sha256"] = source_hash.hexdigest()
        if screen_targets:
            receipt = {"policy": TARGET_POLICY, "source_sha256": report["source_sha256"],
                       "source_rows": report["source_rows"], "included_turns": report["assistant_turns"],
                       "held_turns": report["held_turns"], "held_examples": report["held_examples"]}
            if saved_policy is not None and receipt != saved_policy:
                raise ValueError("full-SFT targets differ from the frozen filter receipt")
            if for_training and saved_policy is None:
                with FileLock(str(policy_file) + ".lock"):
                    if policy_file.exists():
                        if json.loads(policy_file.read_bytes()) != receipt:
                            raise ValueError("full-SFT targets differ from the frozen filter receipt")
                    else:
                        atomic_json(policy_file, receipt)
        yield report, examples
