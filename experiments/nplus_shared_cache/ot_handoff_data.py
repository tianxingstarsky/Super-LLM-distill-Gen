"""Reconstruct the audited OpenThoughts3 train/held-out files from original pinned rows."""

import argparse
from collections import defaultdict
from hashlib import sha256
from itertools import islice
import json
from pathlib import Path
import re
import unicodedata


MANIFEST = Path(__file__).with_name("ot3_verified_math.json")


def upstream_rows(dataset, revision, filename, count):
    import pyarrow.dataset as arrow
    from datasets import load_dataset

    url = f"https://huggingface.co/datasets/{dataset}/resolve/{revision}/{filename}"
    stream = load_dataset(
        "parquet", data_files={"train": url}, split="train", streaming=True, token=False,
        columns=["domain", "difficulty", "source", "conversations"], batch_size=32,
        fragment_scan_options=arrow.ParquetFragmentScanOptions(pre_buffer=False),
    )
    yield from islice(stream, count)


def cached_rows(cache_path, count):
    with cache_path.open(encoding="utf-8") as stream:
        for line in islice(stream, count):
            yield json.loads(line)


def prepare_original(row, receipt, split, manifest):
    question, response = (message["value"] for message in row["conversations"])
    for text, field in ((question, "question_sha256"), (response, "response_sha256")):
        if sha256(text.encode()).hexdigest() != receipt[field]:
            raise ValueError(f"Original text digest mismatch in {receipt['file']} row {receipt['row']}")
    raw_reasoning, separator, answer = response.partition("</think>")
    if not separator or not raw_reasoning.startswith("<think>"):
        raise ValueError("Pinned original no longer has a complete teacher response")
    boxed = re.findall(r"\\boxed\{\s*(-?\d{1,9})\s*\}", answer)
    if len(boxed) != 1:
        raise ValueError("Expected one original boxed integer")
    normalized = " ".join(unicodedata.normalize("NFKC", question).lower().split())
    source_reference = str(int(boxed[0]))
    record = {
        "id": sha256(normalized.encode()).hexdigest(),
        "question": question,
        "reference": receipt["reference"],
        "reasoning": raw_reasoning.removeprefix("<think>").strip(),
        "answer": answer.strip(),
        "source": {
            "repo": manifest["dataset"], "revision": manifest["revision"], "file": receipt["file"],
            "row_in_file": receipt["row"], "source_split": "train",
            "question_sha256": receipt["question_sha256"], "response_sha256": receipt["response_sha256"],
            "upstream": row["source"], "difficulty": row["difficulty"],
        },
    }
    if split == "eval":
        record["source_reference"] = source_reference
    elif source_reference != record["reference"]:
        raise ValueError("Training teacher answer disagrees with the checked reference")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-cache", type=Path, help="Optional pinned original <shard>-source.jsonl cache")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    positions = defaultdict(dict)
    for split, entries in manifest["splits"].items():
        for entry in entries:
            positions[entry["file"]][entry["row"]] = (split, entry)
    records = {}
    for filename, requested in positions.items():
        count = max(requested) + 1
        if args.raw_cache:
            shard = int(Path(filename).name.split("-")[1])
            rows = cached_rows(args.raw_cache / f"{shard}-source.jsonl", count)
        else:
            rows = upstream_rows(manifest["dataset"], manifest["revision"], filename, count)
        for i, row in enumerate(rows):
            if i in requested:
                split, entry = requested[i]
                records[(filename, i)] = prepare_original(row, entry, split, manifest)
    files = {}
    for split, entries in manifest["splits"].items():
        encoded = [
            json.dumps(records[(e["file"], e["row"])], ensure_ascii=False, separators=(",", ":"))
            for e in entries
        ]
        files[split] = ("\n".join(encoded) + "\n").encode()
        if sha256(files[split]).hexdigest() != manifest[f"{split}_sha256"]:
            raise ValueError(f"Frozen {split} split digest mismatch")
    args.output.mkdir(parents=True, exist_ok=True)
    for split, binary in files.items():
        target = args.output / f"{split}-verified.jsonl"
        temp = target.with_suffix(".jsonl.tmp")
        temp.write_bytes(binary)
        temp.replace(target)
        print(f"{target.name} {sha256(binary).hexdigest()}")


if __name__ == "__main__":
    main()
