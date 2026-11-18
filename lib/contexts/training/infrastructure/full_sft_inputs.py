"""Freeze the exact JSONL byte stream and encode native, loss-masked targets."""
from array import array
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryFile

from lib.contexts.datasets.api import default_control_text_config, iter_training_examples
from lib.contexts.datasets.contracts import TRAINING_EFFORTS
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
                    for turn in iter_training_examples([row], targets, default_effort="medium"):
                        audit = validate_supervision(turn, eos_token_id=tokenizer.eos_token_id,
                                                     trailing_whitespace_ids=trailing)
                        mode = "thinking_turns" if turn["metadata"]["enable_thinking"] else "answer_only_turns"
                        report[mode] += 1
                        report["assistant_turns"] += 1
                        report["supervised_tokens"] += audit["supervised_tokens"]
                        report["maximum_input_tokens"] = max(report["maximum_input_tokens"], audit["input_tokens"])
                        if examples is not None:
                            examples.append(turn)
                except ValueError as error:
                    raise ValueError(f"SFT JSONL line {line_number}: {error}") from error
                report["source_rows"] += 1
        if not report["assistant_turns"]:
            raise ValueError("SFT source contains no assistant targets")
        report["source_sha256"] = source_hash.hexdigest()
        yield report, examples
