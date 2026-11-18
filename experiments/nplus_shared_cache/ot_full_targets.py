"""Register the complete pinned OT3 release and encode every reasoning token in order."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import urlopen

from . import runner


DATASET = "open-thoughts/OpenThoughts3-1.2M"
REVISION = "61bcf9d4eb38b30295efc2021227a63cc5bb34c8"
SYSTEM = "Solve the task and give a complete answer."
ROWS = 1_200_000
SHARDS = 120
ROWS_PER_SHARD = ROWS // SHARDS
DOWNLOAD_BYTES = 28_188_197_544
ARROW_BYTES = 59_763_369_750
SHARD_INDEX_SHA256 = "7816d2d3099e26e6e86f7e29cbc96602d408cf1c7f907b4f89b949173934c957"
TARGET_SCHEMA = "openthoughts-full-reasoning-target/v1"


def register(output):
    """Read official revision metadata. Keep shard names and LFS content hashes portable."""
    path = output / "manifest.json"
    if path.exists():
        return load_manifest(output)
    api = (
        f"https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}/data?limit=1000"
    )
    with urlopen(api, timeout=90) as stream:
        listing = json.load(stream)
    shards = []
    for entry in sorted(listing, key=lambda item: item["path"]):
        if not entry["path"].endswith(".parquet"):
            continue
        index = len(shards)
        if entry["path"] != f"data/train-{index:05d}-of-{SHARDS:05d}.parquet":
            raise ValueError("Unexpected shard sequence in the pinned release")
        if entry["size"] != entry["lfs"]["size"]:
            raise ValueError("Hub file size differs from LFS pointer")
        shards.append(
            dict(
                index=index,
                path=entry["path"],
                size=entry["size"],
                sha256=entry["lfs"]["oid"],
                first_ordinal=index * ROWS_PER_SHARD,
                rows=ROWS_PER_SHARD,
            )
        )
    card = f"https://huggingface.co/datasets/{DATASET}/raw/{REVISION}/README.md"
    with urlopen(card, timeout=90) as stream:
        card_text = stream.read().decode()
    for key, value in (
        ("num_examples", ROWS),
        ("download_size", DOWNLOAD_BYTES),
        ("dataset_size", ARROW_BYTES),
    ):
        if not re.search(rf"(?m)^\s*{key}: {value}\s*$", card_text):
            raise ValueError(f"Pinned card no longer declares {key}={value}")
    manifest = dict(
        schema="openthoughts-full-corpus/v1",
        dataset=DATASET,
        revision=REVISION,
        total_rows=ROWS,
        download_bytes=DOWNLOAD_BYTES,
        arrow_bytes=ARROW_BYTES,
        row_order="shard ascending, then physical parquet row ascending",
        row_count_check="Each shard's expected row count is verified against its Parquet footer on access.",
        source_card=card,
        shards=shards,
    )
    check_manifest(manifest)
    runner.write_json(path, manifest)
    return manifest


def check_manifest(manifest):
    shards = manifest["shards"]
    if (manifest["dataset"], manifest["revision"], manifest["total_rows"]) != (
        DATASET,
        REVISION,
        ROWS,
    ):
        raise ValueError("Wrong corpus identity")
    if len(shards) != SHARDS or sum(s["size"] for s in shards) != DOWNLOAD_BYTES:
        raise ValueError("Incomplete official shard listing")
    shard_bytes = json.dumps(shards, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(shard_bytes).hexdigest() != SHARD_INDEX_SHA256:
        raise ValueError("Shard hashes differ from the registered upstream revision")
    for index, shard in enumerate(shards):
        if (shard["index"], shard["path"], shard["first_ordinal"], shard["rows"]) != (
            index,
            f"data/train-{index:05d}-of-{SHARDS:05d}.parquet",
            index * ROWS_PER_SHARD,
            ROWS_PER_SHARD,
        ) or not re.fullmatch(r"[0-9a-f]{64}", shard["sha256"]):
            raise ValueError(f"Invalid pinned shard entry at {index}")


def load_manifest(output):
    manifest = json.loads((output / "manifest.json").read_text("utf-8"))
    check_manifest(manifest)
    return manifest


def text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_response(response):
    """The first closing tag ends the open thought. Preserve subsequent text verbatim."""
    if not response.startswith("<think>"):
        if not response.strip():
            raise ValueError("Empty assistant response")
        return "answer_only", "", response
    reasoning, close, answer = response[len("<think>") :].partition("</think>")
    if not close:
        return "open_reasoning", reasoning, ""
    if not answer.strip():
        return "closed_without_answer", reasoning, ""
    if not reasoning.strip():
        return "answer_only", "", answer
    return "complete", reasoning, answer


def parse_record(shard, row_index, item):
    conversation = item["conversations"]
    if [turn["from"] for turn in conversation] != ["human", "gpt"]:
        raise ValueError(
            f"Unsupported conversation shape at {shard['path']} row {row_index}"
        )
    question, response = [turn["value"] for turn in conversation]
    if not question.strip():
        raise ValueError(f"Empty source question at {shard['path']} row {row_index}")
    completion, reasoning, answer = split_response(response)
    return dict(
        id=f"{REVISION}:{shard['index']:05d}:{row_index:05d}",
        ordinal=shard["first_ordinal"] + row_index,
        question=question,
        source_completion=completion,
        reasoning=reasoning,
        answer=answer,
        source=dict(
            repo=DATASET,
            revision=REVISION,
            file=shard["path"],
            row_in_file=row_index,
            domain=item["domain"],
            upstream=item["source"],
            difficulty=item["difficulty"],
            question_sha256=text_sha(question),
            response_sha256=text_sha(response),
            closing_think_tags=response.count("</think>"),
            shard_sha256=shard["sha256"],
        ),
    )


def source_rows(output, manifest, start, limit):
    """Seek directly to the first Parquet row group, then stream bounded batches."""
    import pyarrow.parquet as pq

    if start < 0 or limit < 1 or start + limit > manifest["total_rows"]:
        raise ValueError("Requested rows outside the pinned release")
    stop = start + limit
    for shard_index in range(start // ROWS_PER_SHARD, (stop - 1) // ROWS_PER_SHARD + 1):
        shard = manifest["shards"][shard_index]
        path = output / "corpus" / shard["path"]
        if (
            path.stat().st_size != shard["size"]
            or runner.file_sha(path) != shard["sha256"]
        ):
            raise ValueError(f"Invalid local Parquet mirror for {shard['path']}")
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows != shard["rows"]:
                raise ValueError(f"Wrong physical row count for {shard['path']}")
            first = max(0, start - shard["first_ordinal"])
            end = min(shard["rows"], stop - shard["first_ordinal"])
            boundaries = [0]
            for group in range(parquet.num_row_groups):
                boundaries.append(
                    boundaries[-1] + parquet.metadata.row_group(group).num_rows
                )
            for group in range(
                bisect_right(boundaries, first) - 1, bisect_right(boundaries, end - 1)
            ):
                cursor = boundaries[group]
                for batch in parquet.iter_batches(
                    batch_size=16,
                    row_groups=[group],
                    columns=["conversations", "source", "difficulty", "domain"],
                ):
                    lo, hi = max(0, first - cursor), min(batch.num_rows, end - cursor)
                    if hi > lo:
                        for row_index, item in enumerate(
                            batch.slice(lo, hi - lo).to_pylist(), cursor + lo
                        ):
                            yield parse_record(shard, row_index, item)
                    cursor += batch.num_rows
                    if cursor >= end:
                        break


def encode_case(base, tokenizer, record, segment_tokens=128, forward_tokens=512):
    """Return float32 [segments,width] means and exclusive reasoning-token ends."""
    from . import ot_corpus_scope as scope

    if scope.review_record(record) is not None:
        raise ValueError(
            f"Source row {record['ordinal']} is outside the training scope"
        )
    return _encode_allowed(base, tokenizer, record, segment_tokens, forward_tokens)


def _encode_allowed(base, tokenizer, record, segment_tokens, forward_tokens):
    import torch
    from transformers.cache_utils import DynamicCache
    from . import ot_latent_model as model
    from . import recurrent_runtime as runtime

    if (
        segment_tokens < 1
        or forward_tokens < segment_tokens
        or forward_tokens % segment_tokens
    ):
        raise ValueError("forward_tokens must be a positive multiple of segment_tokens")
    if base.training or any(parameter.requires_grad for parameter in base.parameters()):
        raise ValueError("Teacher must be frozen and in evaluation mode")
    prompt = model.encode_prompt(
        tokenizer,
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": record["question"]},
        ],
    )
    ids = tokenizer.encode(
        record["reasoning"], add_special_tokens=False, truncation=False
    )
    config = base.config.text_config
    if len(prompt) + len(ids) > config.max_position_embeddings:
        raise ValueError(
            f"{record['id']} needs {len(prompt)} prompt and {len(ids)} reasoning tokens "
            f"within native context {config.max_position_embeddings}"
        )
    embedding = base.get_input_embeddings()
    device = embedding.weight.device
    cache = DynamicCache(config=config)
    with torch.inference_mode():
        # Qwen's native GDN and full-attention cache both survive every block.
        def prefill(block):
            return runtime._forward(
                base, cache, embedding(torch.tensor([block], device=device))
            )

        for start in range(0, len(prompt), forward_tokens):
            prefill(prompt[start : start + forward_tokens])
        means, ends = [], []
        for start in range(0, len(ids), forward_tokens):
            block = ids[start : start + forward_tokens]
            states = prefill(block)[0]
            if states.shape[0] != len(block) or cache.get_seq_length() != len(
                prompt
            ) + start + len(block):
                raise RuntimeError(
                    "The native cache did not consume the ordered input block"
                )
            for offset in range(0, len(block), segment_tokens):
                segment = states[offset : offset + segment_tokens]
                means.append(segment.float().mean(dim=0))
                ends.append(start + offset + len(segment))
        targets = (
            torch.stack(means).cpu()
            if means
            else torch.empty(0, config.hidden_size, dtype=torch.float32)
        )
    if (ends[-1] if ends else 0) != len(ids) or not torch.isfinite(targets).all():
        raise RuntimeError("Incomplete or nonfinite teacher targets")
    # Canonical little-endian IDs bind token boundaries independently of text hashes.
    import struct

    digest = hashlib.sha256(struct.pack(f"<{len(ids)}I", *ids)).hexdigest()
    return targets, dict(
        id=record["id"],
        ordinal=record["ordinal"],
        source_completion=record["source_completion"],
        n_prompt_tokens=len(prompt),
        n_reasoning_tokens=len(ids),
        n_answer_tokens=len(
            tokenizer.encode(
                record["answer"], add_special_tokens=False, truncation=False
            )
        ),
        reasoning_token_sha256=digest,
        reasoning_sha256=text_sha(record["reasoning"]),
        answer_sha256=text_sha(record["answer"]),
        segment_ends=ends,
        segment_tokens=segment_tokens,
        forward_tokens=forward_tokens,
        native_context_limit=config.max_position_embeddings,
        final_cache_tokens=cache.get_seq_length(),
        hidden_width=targets.shape[-1],
    )


def prepare(
    output,
    base_dir,
    start,
    limit,
    segment_tokens=128,
    forward_tokens=512,
    device="cuda:0",
    queue_limit=128,
):
    """Produce a bounded queue window. The consumer retires rows after durable checkpoints."""
    import torch
    from filelock import FileLock
    from safetensors.torch import save_file
    from transformers import AutoTokenizer
    from . import ot_corpus_scope as scope
    from .ot_latent_model import _base_identity

    manifest = load_manifest(output)
    if queue_limit < 1 or limit > queue_limit:
        raise ValueError("Preparation window exceeds queue capacity")
    queue = output / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / "teacher.lock"), timeout=0):
        binding = dict(
            schema=TARGET_SCHEMA,
            system=SYSTEM,
            base_sha256=_base_identity(base_dir),
            manifest_sha256=runner.file_sha(output / "manifest.json"),
            segment_tokens=segment_tokens,
            forward_tokens=forward_tokens,
            pooling="float32 mean over every last-layer token hidden within each segment",
        )
        binding_path = output / "teacher-binding.json"
        if (
            binding_path.exists()
            and json.loads(binding_path.read_text("utf-8")) != binding
        ):
            raise ValueError(
                "Queue already belongs to a different teacher or target protocol"
            )
        if not binding_path.exists():
            runner.write_json(binding_path, binding)
        binding_sha = runner.file_sha(binding_path)
        scope_sha = scope.scope_sha256()
        pending = {p.name for p in queue.iterdir() if p.is_dir()}
        requested = {f"{n:09d}" for n in range(start, start + limit)}
        if len(pending | requested) > queue_limit:
            raise ValueError(
                "Queue full. Retire durably trained rows before preparing more."
            )
        base = tokenizer = None
        for record in source_rows(output, manifest, start, limit):
            if (output / "STOP").exists():
                print("Preparation stopped at the requested row boundary.", flush=True)
                break
            dest = queue / f"{record['ordinal']:09d}"
            receipt_path = dest / "receipt.json"
            record_path = dest / "record.json"
            target_path = dest / "target.safetensors"
            reason = scope.review_record(record)
            if reason is not None:
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("Unrecognized training-scope decision")
                dest.mkdir(exist_ok=True)
                runner.write_json(
                    receipt_path,
                    dict(
                        schema=TARGET_SCHEMA,
                        ordinal=record["ordinal"],
                        id=record["id"],
                        status="excluded",
                        reason=reason,
                        binding_sha256=binding_sha,
                        scope_decision="excluded",
                        scope_sha256=scope_sha,
                        source_sha256={
                            key: record["source"][key]
                            for key in (
                                "question_sha256",
                                "response_sha256",
                                "shard_sha256",
                            )
                        },
                    ),
                )
                # Retire previously staged outputs if a stricter review now isolates this row.
                for old in (
                    record_path,
                    target_path,
                    dest / "record.json.tmp",
                    dest / "target.safetensors.tmp",
                ):
                    old.unlink(missing_ok=True)
                continue
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text("utf-8"))
                if (
                    receipt["id"] != record["id"]
                    or receipt["ordinal"] != record["ordinal"]
                    or receipt["status"] != "ready"
                    or receipt["binding_sha256"] != binding_sha
                    or receipt["record_sha256"] != runner.file_sha(record_path)
                    or receipt["target_sha256"] != runner.file_sha(target_path)
                    or json.loads(record_path.read_text("utf-8")) != record
                ):
                    raise ValueError(
                        f"Cannot resume unauthenticated row {record['ordinal']}"
                    )
                if (
                    receipt.get("scope_sha256") != scope_sha
                    or receipt.get("scope_decision") != "allowed"
                ):
                    receipt.update(scope_decision="allowed", scope_sha256=scope_sha)
                    runner.write_json(receipt_path, receipt)
                continue
            dest.mkdir(exist_ok=True)
            runner.write_json(record_path, record)
            if base is None:
                tokenizer = AutoTokenizer.from_pretrained(
                    base_dir, local_files_only=True, trust_remote_code=False
                )
                base, _ = runner.load_frozen_model(base_dir, device=device)
            t0 = time.monotonic()
            hidden, receipt = _encode_allowed(
                base, tokenizer, record, segment_tokens, forward_tokens
            )
            temporary = dest / "target.safetensors.tmp"
            save_file(
                {
                    "hidden": hidden,
                    "ends": torch.tensor(receipt["segment_ends"], dtype=torch.long),
                },
                str(temporary),
            )
            runner.replace_with_retry(temporary, target_path)
            receipt.update(
                schema=TARGET_SCHEMA,
                status="ready",
                scope_sha256=scope_sha,
                scope_decision="allowed",
                binding_sha256=binding_sha,
                record_sha256=runner.file_sha(record_path),
                target_sha256=runner.file_sha(target_path),
                elapsed_seconds=time.monotonic() - t0,
            )
            runner.write_json(receipt_path, receipt)
            print(
                json.dumps(
                    {
                        key: receipt[key]
                        for key in (
                            "ordinal",
                            "status",
                            "n_reasoning_tokens",
                            "n_answer_tokens",
                            "elapsed_seconds",
                        )
                    }
                ),
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("register", "prepare"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of sequential source rows to enqueue",
    )
    parser.add_argument("--segment-tokens", type=int, default=128)
    parser.add_argument("--forward-tokens", type=int, default=512)
    parser.add_argument("--queue-limit", type=int, default=128)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    if args.action == "register":
        manifest = register(args.output)
        print(
            f"{manifest['total_rows']} rows in {len(manifest['shards'])} pinned shards"
        )
    else:
        if args.base is None:
            parser.error("--base is required for preparation")
        prepare(
            args.output,
            args.base,
            args.start,
            args.limit,
            args.segment_tokens,
            args.forward_tokens,
            args.device,
            args.queue_limit,
        )


if __name__ == "__main__":
    main()
