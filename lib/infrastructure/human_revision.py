"""Verify a reflow against the selected sealed result and persistent session map."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from pathlib import Path

from lib.domain.human_augmentation import validate_revision_context
from lib.domain.workflow_quality import canonical
from lib.infrastructure.json_stream import iter_json_records


_BATCH = ContextVar("authenticated_revision_batch", default=None)


def _identity(path):
    if path.is_symlink():
        raise ValueError("human_session_result_changed")
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


@contextmanager
def verified_revision_batch(output, run_id, contexts):
    """Share strong artifact verification only during this bounded repair load.

    Each parent artifact is hashed once on entry. Indexed record digests and
    file identities are still checked for every selection. Nothing is leased
    across jobs or retained after this context closes.
    """
    from lib.infrastructure.rework_candidate_index import rework_candidate_index
    from lib.infrastructure.training_workflow import run_path

    checked = [validate_revision_context(value, review=True) for value in contexts]
    batch = {"output": str(Path(output).resolve()), "run_id": run_id, "indexes": {}, "parents": {}, "children": {}}
    with ExitStack() as stack:
        for parent_id, target in sorted(set((value["parent_run_id"], value["target"]) for value in checked)):
            index = stack.enter_context(rework_candidate_index(run_path(output, parent_id), target))
            batch["indexes"][(parent_id, target)] = index
        token = _BATCH.set(batch)
        try:
            yield
        finally:
            _BATCH.reset(token)


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
        batch = _BATCH.get()
        if batch is not None and (batch["output"] != str(Path(output).resolve()) or batch["run_id"] != run_id):
            batch = None
        child_key = context["session_id"]
        cached_child = batch["children"].get(child_key) if batch is not None else None
        session_file = path / "session.json"
        recipe_file = run_path(output, run_id) / "recipe.json"
        if cached_child is None:
            session = read_json(session_file)
            child = next(row for row in session["rounds"] if row["run_id"] == run_id and row["kind"] in {"revision", "manual_review"})
            sidecar_file = None
            if child.get("details_ref"):
                from lib.infrastructure.human_round_store import verified_human_round
                child = verified_human_round(path, child)
                sidecar_file = path / child["details_ref"]["file"]
            child_recipe = read_json(recipe_file)
            if child.get("repair_inputs_sha256") is not None and child["repair_inputs_sha256"] != digest(child_recipe.get("repair_inputs")):
                raise ValueError("human_session_result_changed")
            cached_child = (child, _identity(session_file), _identity(recipe_file),
                            (sidecar_file, _identity(sidecar_file)) if sidecar_file is not None else None)
            if batch is not None:
                batch["children"][child_key] = cached_child
        child, session_identity, recipe_identity, sidecar_identity = cached_child
        if _identity(session_file) != session_identity:
            # Other reviews may save issues during execution. Recheck this
            # child's authorization instead of rejecting an unrelated update.
            fresh_session = read_json(session_file)
            fresh_child = next(row for row in fresh_session["rounds"] if row["run_id"] == run_id
                               and row["kind"] in {"revision", "manual_review"})
            if fresh_child.get("details_ref"):
                from lib.infrastructure.human_round_store import verified_human_round
                fresh_child = verified_human_round(path, fresh_child)
            immutable = ("id", "run_id", "kind", "parent_results", "details_ref", "repair_inputs_sha256")
            if (any(child.get(key) != fresh_child.get(key) for key in immutable)
                    or fresh_child["status"] in {"cancelled", "preparation_failed"}):
                raise ValueError("human_session_result_changed")
            child, session_identity = fresh_child, _identity(session_file)
            if batch is not None:
                batch["children"][child_key] = (child, session_identity, recipe_identity, sidecar_identity)
        if (_identity(recipe_file) != recipe_identity
                or sidecar_identity is not None and _identity(sidecar_identity[0]) != sidecar_identity[1]):
            raise ValueError("human_session_result_changed")
        expected = {"run_id": context["parent_run_id"], "round_id": context["round_id"],
                    "target": context["target"], "candidate_id": context["candidate_id"],
                    "content_sha256": context["content_sha256"]}
        if expected not in child["parent_results"] or child["status"] in {"cancelled", "preparation_failed"}:
            raise ValueError("human_session_revision_not_authorized")
        parent_path = run_path(output, context["parent_run_id"])
        index = batch["indexes"].get((context["parent_run_id"], context["target"])) if batch is not None else None
        cached_parent = batch["parents"].get(context["parent_run_id"]) if batch is not None else None
        if cached_parent is None:
            manifest = index.manifest if index is not None else verify_artifacts(parent_path)
            parent_recipe = read_json(parent_path / "recipe.json")
            parent_state = read_json(parent_path / "state.json")
            if (manifest.get("run_id") != context["parent_run_id"] or manifest.get("recipe_hash") != digest(parent_recipe)
                    or parent_state.get("recipe_hash") != manifest.get("recipe_hash")):
                raise ValueError("human_session_result_changed")
            files = [(parent_path / "recipe.json", _identity(parent_path / "recipe.json")),
                     (parent_path / "state.json", _identity(parent_path / "state.json"))]
            for source in parent_recipe["sources"]:
                source_path = parent_path / "inputs" / source["file"]
                if source_path.is_symlink() or file_hash(source_path) != source["sha256"]:
                    raise ValueError("human_session_source_changed")
                files.append((source_path, _identity(source_path)))
            cached_parent = (parent_recipe, files)
            if batch is not None:
                batch["parents"][context["parent_run_id"]] = cached_parent
        parent_recipe, checked_files = cached_parent
        for checked_file, identity in checked_files:
            if _identity(checked_file) != identity:
                raise ValueError("human_session_result_changed")
        if index is not None:
            record = index.candidates([context["candidate_id"]])[context["candidate_id"]]
        else:
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
