"""Prepare untrusted arithmetic tutoring plan candidates from annotated source rows."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.contexts.workflows.api import prepare_calculation_plan_candidate


def prepare_batch(input_path, output):
    if output.exists():
        raise ValueError("choose a new output directory to preserve the existing candidates")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {"candidates": 0, "unresolved": 0, "review_status": "requires_independent_attestation"}
    with TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary) / "batch"
        work.mkdir()
        seen = set()
        with (input_path.open(encoding="utf-8-sig") as rows,
              (work / "candidates.jsonl").open("w", encoding="utf-8") as candidates,
              (work / "unresolved.jsonl").open("w", encoding="utf-8") as unresolved):
            for line_number, line in enumerate(rows, 1):
                if not line.strip():
                    continue
                ident = None
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        raise ValueError("source needs a string id")
                    ident = row["id"]
                    if ident in seen:
                        raise ValueError("duplicate source id")
                    seen.add(ident)
                    annotations = row.pop("calculation_steps", None)
                    candidate = prepare_calculation_plan_candidate(row, annotations)
                except ValueError as exc:
                    record = {"input_line": line_number, "id": ident, "issue": str(exc)}
                    unresolved.write(json.dumps(record, ensure_ascii=False) + "\n")
                    summary["unresolved"] += 1
                else:
                    candidates.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                    summary["candidates"] += 1
        (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        work.rename(output)
    return summary


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True, help="Guided source JSONL with per-row calculation_steps.")
    cli.add_argument("--output", type=Path, required=True, help="New directory for candidates, unresolved rows and counts.")
    args = cli.parse_args()
    try:
        summary = prepare_batch(args.input, args.output)
    except (OSError, ValueError) as exc:
        cli.exit(2, str(exc) + "\n")
    print(json.dumps(summary))
    return int(bool(summary["unresolved"]))


if __name__ == "__main__":
    raise SystemExit(main())
