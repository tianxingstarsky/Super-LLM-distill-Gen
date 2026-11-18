"""Run the full-corpus teacher, both students, and the external evaluation in bounded windows."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from filelock import FileLock

from . import runner
from .ot_full_targets import load_manifest


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def arm_summary(root, routes):
    arm = root / "arms" / f"routes-{routes}"
    pointer = arm / "current.json"
    if not pointer.is_file():
        return dict(next_ordinal=0, rows_applied=0, status="pending")
    receipt = read(pointer)
    state_file = arm / receipt["snapshot"] / "state.json"
    if runner.file_sha(state_file) != receipt["state_sha256"]:
        raise ValueError("checkpoint pointer does not authenticate its training state")
    state = read(state_file)
    return {key: state[key] for key in ("next_ordinal", "rows_applied", "excluded_rows", "total_rows", "reasoning_tokens",
                                        "answer_labels", "target_segments", "training_seconds", "status")}


def snapshot(root):
    job_path = root / "job.json"
    return dict(job=read(job_path) if job_path.exists() else {}, pause_requested=(root / "STOP").exists(),
                total_rows=load_manifest(root)["total_rows"],
                arms={str(n): arm_summary(root, n) for n in (5, 1)})


def retire(root, cursor):
    """Only retire authenticated training queue entries below both durable cursors."""
    queue = root / "queue"
    if not queue.is_dir():
        return
    if queue.is_symlink() or queue.resolve().parent != root.resolve():
        raise ValueError("unexpected training queue location")
    for slot in queue.iterdir():
        if not re.fullmatch(r"[0-9]{9}", slot.name) or int(slot.name) >= cursor:
            continue
        if slot.is_symlink() or slot.resolve().parent != queue.resolve():
            raise ValueError("unexpected training queue directory")
        entries = list(slot.iterdir())
        if any(not item.is_file() or item.is_symlink() or item.name not in
               {"record.json", "target.safetensors", "receipt.json"} for item in entries):
            raise ValueError("unexpected files in a committed training queue entry")
        for item in entries:
            item.unlink()
        slot.rmdir()


def evaluation_file(root, requested):
    contract = root / "evaluation-contract.json"
    if contract.exists():
        saved = read(contract)
        if saved["path"] is None:
            if requested is not None:
                raise ValueError("this run was registered without an evaluation file")
            return None
        path = root / saved["path"]
        if runner.file_sha(path) != saved["sha256"] or (
            requested is not None and runner.file_sha(requested) != saved["sha256"]
        ):
            raise ValueError("external evaluation differs from the original run")
    else:
        path = requested
        if path is None:
            runner.write_json(contract, dict(path=None, sha256=None))
        else:
            # Keep a relocatable immutable copy even if the caller's path later changes.
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            stored = root / "evaluation" / (digest + ".jsonl")
            stored.parent.mkdir(exist_ok=True)
            if stored.exists() and stored.read_bytes() != payload:
                raise ValueError("registered evaluation copy has changed")
            temporary = stored.with_suffix(".tmp")
            temporary.write_bytes(payload)
            runner.replace_with_retry(temporary, stored)
            runner.write_json(contract, dict(path=stored.relative_to(root).as_posix(), sha256=digest))
            path = stored
    return path


def run(args):
    root = args.root
    manifest = load_manifest(root)
    total = manifest["total_rows"]

    def stage(name, **details):
        runner.write_json(root / "job.json", dict(phase=name, pid=os.getpid(), updated_unix=time.time(), **details))

    def invoke(module, *arguments):
        subprocess.run([sys.executable, "-B", "-X", "utf8", "-u", "-m",
                        f"experiments.nplus_shared_cache.{module}", *map(str, arguments)], check=True)

    with FileLock(root / "job.lock", timeout=0):
        evaluation = evaluation_file(root, args.evaluation)
        if args.action == "resume":
            (root / "STOP").unlink(missing_ok=True)
        try:
            while not (root / "STOP").exists():
                arms = {n: arm_summary(root, n) for n in (5, 1)}
                start = min(arm["next_ordinal"] for arm in arms.values())
                retire(root, start)
                if start == total:
                    if arms[1]["rows_applied"] != arms[5]["rows_applied"] or any(
                        arm["rows_applied"] + arm["excluded_rows"] != total for arm in arms.values()
                    ):
                        raise ValueError("the two students did not account for the same source corpus")
                    if evaluation:
                        stage("evaluation")
                        models = {n: (root / "arms" / f"routes-{n}" / read(
                            root / "arms" / f"routes-{n}" / "current.json")["snapshot"] / "model") for n in arms}
                        invoke("ot_long_eval", "--evaluation", evaluation, "--single", models[1],
                               "--five", models[5], "--output", root / "final-evaluation.json")
                    stage("completed_with_exclusions" if arms[5]["excluded_rows"] else "completed",
                          trained_per_arm=arms[5]["rows_applied"], held_per_arm=arms[5]["excluded_rows"])
                    return
                shard = next(s for s in manifest["shards"] if s["first_ordinal"] <= start < s["first_ordinal"] + s["rows"])
                end = min(start + args.batch_rows, shard["first_ordinal"] + shard["rows"])
                if not (root / "corpus" / shard["path"]).is_file():
                    from .ot_corpus_mirror import materialize
                    stage("downloading_shard", shard=shard["index"], start=start)
                    materialize(manifest, shard, root / "corpus")
                    if (root / "STOP").exists():
                        break
                stage("teacher", start=start, end=end)
                # A shorter window after a restart can leave a longer existing frontier.
                queue = root / "queue"
                prior = {p.name for p in queue.iterdir() if p.is_dir()} if queue.is_dir() else set()
                capacity = max(args.batch_rows, len(prior | {f"{i:09d}" for i in range(start, end)}))
                invoke("ot_full_targets", "--action", "prepare", "--output", root, "--base", args.base,
                       "--start", start, "--limit", end-start, "--queue-limit", capacity)
                for n in (5, 1):
                    if (root / "STOP").exists():
                        break
                    if arms[n]["next_ordinal"] >= end:
                        continue
                    stage(f"training_{n}", start=arms[n]["next_ordinal"], end=end)
                    invoke("ot_full_train", "--root", root, "--base", args.base, "--arm", n,
                           "--until", end, "--checkpoint-every", args.checkpoint_every)
                    if arm_summary(root, n)["next_ordinal"] < end and not (root / "STOP").exists():
                        raise RuntimeError("worker exited before the durable end of its prepared window")
            stage("paused")
        except Exception as exc:
            stage("interrupted", error=type(exc).__name__, reason=str(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status", "pause", "resume"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--batch-rows", type=int, default=128)
    parser.add_argument("--checkpoint-every", type=int, default=128)
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(snapshot(args.root), ensure_ascii=False, indent=2))
    elif args.action == "pause":
        (args.root / "STOP").touch()
    else:
        if not args.base or min(args.batch_rows, args.checkpoint_every) < 1:
            parser.error("training requires --base and positive window and checkpoint intervals")
        if args.evaluation and not args.evaluation.is_file():
            parser.error("external evaluation file does not exist")
        run(args)


if __name__ == "__main__":
    main()
