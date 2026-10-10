"""Verify a reflow against the selected sealed result and persistent session map."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lib.domain.human_augmentation import validate_revision_context
from lib.domain.workflow_quality import canonical
from lib.infrastructure.json_stream import iter_json_records


def record_messages(record):
    messages = record.get("messages")
    if not messages and record.get("chosen"):
        messages = [*record.get("prompt", []), *record["chosen"]]
    if not messages and record.get("question") and record.get("answer"):
        question = record["question"]
        messages = [*(question if isinstance(question, list) else [{"role": "user", "content": question}]),
                    {"role": "assistant", "content": record["answer"],
                     **({"reasoning_content": "\n".join(record["reasoning"]) if isinstance(record["reasoning"], list)
                         else record["reasoning"]} if record.get("reasoning") else {})}]
    if not messages and record.get("text"):
        messages = [{"role": "user", "content": "Review this source-preserving corpus."},
                    {"role": "assistant", "content": record["text"]}]
    if not messages:
        seed = record.get("qa_contract", {}).get("human_design", {}).get("seed", {})
        if seed.get("question") and seed.get("answer"):
            messages = [{"role": "user", "content": seed["question"]},
                        {"role": "assistant", "content": seed["answer"]}]
    return deepcopy(messages or [])


def revision_evidence(record, recipe):
    context = record.get("source_context") or {}
    text = context.get("text") or context.get("task")
    if not isinstance(text, str) or not text.strip():
        quotes = record.get("qa_contract", {}).get("evidence_quotes") or record.get("quotes") or []
        # Multi-turn records store per-turn lists. Only actual quoted source
        # fragments may be reused as evidence, never the generated old answer.
        quotes = [q for item in quotes for q in (item if isinstance(item, list) else [item]) if isinstance(q, str)]
        text = "\n".join(dict.fromkeys(quotes))
    real_source = any(source.get("kind") != "human_design" and source["sha256"] == record.get("source_id")
                      for source in recipe["sources"])
    human = context.get("human_provided") or record.get("evidence_level", "").startswith("human_provided")
    if not text and human:
        text = record.get("qa_contract", {}).get("human_design", {}).get("seed", {}).get("answer", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("human_session_source_evidence_unavailable")
    return {"teacher_evidence": text.strip(), "source_id": record["source_id"],
            "source_kind": "document" if real_source else "human_provided" if human else "synthetic"}


def verified_revision_parent(output, run_id, supplied):
    from lib.infrastructure.training_workflow import digest, file_hash, read_json, run_path, verify_artifacts

    context = validate_revision_context(supplied, review=True)
    path = Path(output) / "human-sessions" / context["session_id"]
    try:
        session = read_json(path / "session.json")
        child = next(row for row in session["rounds"] if row["run_id"] == run_id and row["kind"] in {"revision", "manual_review"})
        expected = {"run_id": context["parent_run_id"], "round_id": context["round_id"],
                    "target": context["target"], "candidate_id": context["candidate_id"],
                    "content_sha256": context["content_sha256"]}
        if expected not in child["parent_results"] or child["status"] in {"cancelled", "preparation_failed"}:
            raise ValueError("human_session_revision_not_authorized")
        child_recipe = read_json(run_path(output, run_id) / "recipe.json")
        if child.get("repair_inputs_sha256") is not None and child["repair_inputs_sha256"] != digest(child_recipe.get("repair_inputs")):
            raise ValueError("human_session_result_changed")
        parent_path = run_path(output, context["parent_run_id"])
        manifest = verify_artifacts(parent_path)
        parent_recipe = read_json(parent_path / "recipe.json")
        parent_state = read_json(parent_path / "state.json")
        if (manifest.get("run_id") != context["parent_run_id"] or manifest.get("recipe_hash") != digest(parent_recipe)
                or parent_state.get("recipe_hash") != manifest.get("recipe_hash")):
            raise ValueError("human_session_result_changed")
        for source in parent_recipe["sources"]:
            source_path = parent_path / "inputs" / source["file"]
            if source_path.is_symlink() or file_hash(source_path) != source["sha256"]:
                raise ValueError("human_session_source_changed")
        records = iter_json_records(parent_path / "artifacts" / f"{context['target']}.records.json", max_record_chars=2_000_000)
        try:
            record = next(row for row in records if row.get("id") == context["candidate_id"])
        finally:
            records.close()
        prior = record.get("revision_context") or record.get("qa_contract", {}).get("human_design", {}).get("seed", {}).get("revision_context", {})
        ancestry = [*prior.get("ancestors", []), {"run_id": context["parent_run_id"], "candidate_id": context["candidate_id"]}]
        evidence = revision_evidence(record, parent_recipe)
        if (digest(record) != context["content_sha256"] or canonical(record_messages(record)) != canonical(context["messages"])
                or ancestry != context["ancestors"] or context["depth"] != prior.get("depth", 0) + 1
                or any(context[key] != value for key, value in evidence.items())):
            raise ValueError("human_session_result_changed")
        return record
    except (OSError, KeyError, StopIteration, TypeError) as error:
        raise ValueError("human_session_revision_not_authorized") from error
