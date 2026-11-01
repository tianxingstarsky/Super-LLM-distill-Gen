"""Preview lazy Qwen3.5 training inputs without writing another dataset."""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from itertools import islice
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lib.contexts.datasets.api import iter_training_examples
from lib.contexts.datasets.contracts import TRAINING_EFFORTS


def iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    """Read one source record at a time; never rewrite or collect the file."""
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: each JSONL line must be an object")
            yield record


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Existing canonical or legacy JSONL")
    parser.add_argument("--tokenizer", type=Path, required=True, help="Local Qwen3.5 tokenizer directory")
    parser.add_argument("--take", type=_positive_integer, default=3, help="Assistant targets to preview (default: 3)")
    parser.add_argument("--max-length", type=_positive_integer,
                        help="Reject longer token sequences; never truncate them")
    parser.add_argument("--default-effort", choices=TRAINING_EFFORTS,
                        help="Explicit training condition for legacy rows without training_control")
    parser.add_argument("--effort-mapping", type=Path,
                        help="Small JSON map from all five canonical efforts to true/false or 1/0")
    parser.add_argument("--source-control-profile", choices=["deepseek-v4.1-flash-control/v1"],
                        help="Explicitly mark known DeepSeek native-reasoning control text")
    parser.add_argument("--verbosity-reference-text",
                        help="Target wording for the verbosity marker; omitted keeps the marker")
    parser.add_argument("--control-text-config", type=Path,
                        help="Editable JSON with enabled, literal rules, markers and target wording")
    parser.add_argument("--no-control-text", action="store_true",
                        help="Keep all reasoning control text unchanged; overrides text-adaptation options")
    parser.add_argument("--show-text", action="store_true",
                        help="Also print the complete decoded input and supervised target")
    args = parser.parse_args(argv)
    if not args.input.is_file():
        parser.error("--input must be an existing JSONL file")
    if not args.tokenizer.is_dir():
        parser.error("--tokenizer must be a local directory; this script does not download files")
    if args.effort_mapping is not None and not args.effort_mapping.is_file():
        parser.error("--effort-mapping must be an existing JSON file")
    if not args.no_control_text and args.control_text_config is not None:
        if not args.control_text_config.is_file():
            parser.error("--control-text-config must be an existing JSON file")
        if args.source_control_profile is not None or args.verbosity_reference_text is not None:
            parser.error("--control-text-config cannot be combined with legacy text-adaptation options")

    from transformers import AutoTokenizer
    from lib.bootstrap.datasets import qwen35_training_encoder

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(args.tokenizer), local_files_only=True, trust_remote_code=False,
        )
        effort_mapping = None
        if args.effort_mapping is not None:
            with args.effort_mapping.open("r", encoding="utf-8-sig") as mapping_file:
                effort_mapping = json.load(mapping_file)
        control_text_config = None
        if args.no_control_text:
            control_text_config = {"schema": "shujian.control-text-adaptation/v1", "enabled": False}
        elif args.control_text_config is not None:
            with args.control_text_config.open("r", encoding="utf-8-sig") as config_file:
                control_text_config = json.load(config_file)
            if not isinstance(control_text_config, dict):
                raise ValueError("control_text_configuration_must_be_object")
        encoder = qwen35_training_encoder(
            tokenizer, max_length=args.max_length, effort_mapping=effort_mapping,
            source_control_profile=None if args.no_control_text else args.source_control_profile,
            verbosity_reference_text=None if args.no_control_text else args.verbosity_reference_text,
            control_text_config=control_text_config,
        )
        examples = iter_training_examples(
            iter_jsonl_records(args.input), encoder, default_effort=args.default_effort,
        )
        print(json.dumps({"adapter": encoder.provenance}, ensure_ascii=False, indent=2))
        count = 0
        for example in islice(examples, args.take):
            supervised_ids = [token for token in example["labels"] if token != -100]
            preview = {
                "metadata": example["metadata"],
                "input_tokens": len(example["input_ids"]),
                "supervised_tokens": len(supervised_ids),
                "masked_tokens": len(example["labels"]) - len(supervised_ids),
            }
            if args.show_text:
                preview["input_text"] = tokenizer.decode(example["input_ids"], skip_special_tokens=False)
                preview["supervised_text"] = tokenizer.decode(supervised_ids, skip_special_tokens=False)
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            count += 1
        print(json.dumps({"previewed_targets": count, "dataset_written": False, "training_started": False}))
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
