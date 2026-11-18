"""Mirror a pinned OpenThoughts corpus and verify the actual Parquet row counts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time

from huggingface_hub import hf_hub_download
from pyarrow.parquet import ParquetFile

from .runner import file_sha, write_json
from .ot_full_targets import load_manifest


def materialize(manifest, entry, destination):
    name = entry["path"]
    if not name.startswith("data/train-") or Path(name).parts != ("data", Path(name).name):
        raise ValueError("unexpected shard path")
    path = destination / name
    bad_digest = False
    for attempt in range(3):
        if not path.exists() or bad_digest:
            try:
                hf_hub_download(manifest["dataset"], name, revision=manifest["revision"],
                                repo_type="dataset", local_dir=destination, token=False,
                                force_download=bad_digest, etag_timeout=30)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
                continue
        bad_digest = path.stat().st_size != entry["size"] or file_sha(path) != entry["sha256"]
        if not bad_digest:
            with ParquetFile(path) as shard:
                count = shard.metadata.num_rows
            if count != entry["rows"]:
                raise ValueError(f"row count differs from the pinned manifest: {name}")
            return dict(index=entry["index"], rows=count, bytes=path.stat().st_size,
                        sha256=entry["sha256"])
    raise ValueError(f"shard checksum failed: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--first", type=int, default=0)
    args = parser.parse_args()
    manifest = load_manifest(args.root)
    progress = dict(revision=manifest["revision"], total_rows=manifest["total_rows"],
                    expected_files=len(manifest["shards"]), verified={}, errors={})
    receipt = args.root / f"mirror-from-{args.first}.json"
    work = [s for s in manifest["shards"] if s["index"] >= args.first]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(materialize, manifest, s, args.root / "corpus"): s for s in work}
        for job in as_completed(jobs):
            entry = jobs[job]
            key = str(entry["index"])
            try:
                progress["verified"][key] = job.result()
            except Exception as error:
                progress["errors"][key] = type(error).__name__
                print(f"Shard {key}: {type(error).__name__}", flush=True)
            progress["updated_unix"] = time.time()
            write_json(receipt, progress)
            print(f"Verified {len(progress['verified'])}/{len(work)} requested shards", flush=True)
    if progress["errors"]:
        raise SystemExit("one or more shards require another download attempt")


if __name__ == "__main__":
    main()
