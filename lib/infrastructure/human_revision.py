"""Verify a reflow against the selected sealed result and persistent session map."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from pathlib import Path

from lib.domain.human_augmentation import validate_revision_context
from lib.domain.workflow_quality import canonical, conversation_issue
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


def _recorded_conversation_evidence(context):
    messages = context.get("messages")
    if (context.get("verification_status") == "synthetic_unverified"
            or conversation_issue(messages)):
        raise ValueError("human_session_source_evidence_unavailable")
    # Recorded input and tool observations can support assessment, but the
    # answer under review cannot prove itself. Earlier assistant answers and
    # their reasoning remain available in the candidate, not as source facts.
    prefix = []
    for original in messages[:-1]:
        message = {key: deepcopy(original[key]) for key in (
            "role", "content", "tool_calls", "toolCalls", "tool_call_id", "toolCallId", "name")
            if key in original}
        if message.get("role") == "assistant":
            message["content"] = "[Recorded assistant answer omitted; not independent evidence.]"
        prefix.append(message)
    return canonical({"scope": "recorded_conversation_prefix",
        "verification": "recorded_not_independently_verified",
        "limitations": "User claims and tool observations are recorded context, not independently verified facts. "
                       "Assistant answers and reasoning are excluded; judge the candidate against this context.",
        "messages": prefix, "tools": deepcopy(context.get("tools", []))})


def revision_evidence(record, recipe, run_path=None, *, with_blocks=False, include_source_ids=False):
    """Project authenticated source evidence from a selected sealed result.

    Callers derive run_path from their verified parent run identity. A packed
    source must be fully validated there; a caller cannot silently substitute
    its small anchor when the fixed teacher context is unavailable.
    """
    context = record.get("source_context") or {}
    text = context.get("text") or context.get("task")
    reference = context.get("source_context_pack") or record.get("source_context_pack")
    ranges = (record.get("qa_contract") or {}).get("source_line_ranges")
    pack_identity, source_kind, selection = None, None, {}
    prior = (record.get("revision_context") or record.get("qa_contract", {}).get("human_design", {})
             .get("seed", {}).get("revision_context") or {})
    if prior.get("evidence_pack_sha256") is not None:
        # A repair child has no local copy of its parent's pack. Keep the
        # authenticated selection pinned in its revision context, and only
        # recover block boundaries through the authorized parent lineage.
        prior = validate_revision_context(prior, review=True)
        if prior["source_id"] != record.get("source_id"):
            raise ValueError("human_session_result_changed")
        text, pack_identity, source_kind = (prior["teacher_evidence"],
            prior["evidence_pack_sha256"], prior["source_kind"])
        if with_blocks:
            if run_path is None:
                raise ValueError("human_session_source_evidence_unavailable")
            from lib.infrastructure.training_workflow import read_json, run_path as workflow_path
            current_path = Path(run_path)
            parent = verified_revision_parent(current_path.parent.parent, current_path.name, prior)
            parent_path = workflow_path(current_path.parent.parent, prior["parent_run_id"])
            original = revision_evidence(parent, read_json(parent_path / "recipe.json"), parent_path,
                                         with_blocks=True, include_source_ids=include_source_ids)
            if any(original[key] != prior[key] for key in (
                    "teacher_evidence", "source_id", "source_kind", "evidence_pack_sha256")):
                raise ValueError("human_session_result_changed")
            selection = {key: original[key] for key in (
                "source_line_scope", "source_line_ranges", "teacher_evidence_blocks") if key in original}
            if include_source_ids and "teacher_evidence_source_ids" in original:
                selection["teacher_evidence_source_ids"] = original["teacher_evidence_source_ids"]
    elif recipe.get("source_context_pack_version") == 1 and reference is not None:
        if run_path is None:
            raise ValueError("human_session_source_evidence_unavailable")
        from lib.infrastructure.workflow_source_context import load_context_pack
        pack = load_context_pack(run_path, reference)
        if recipe.get("source_line_protocol_version") == 1 and ranges is not None:
            from lib.domain.source_line_ranges import resolve_source_line_ranges
            resolved = resolve_source_line_ranges(pack["materials"], ranges)
            blocks = [block["text"] for block in resolved]
            text = "\n\n".join(blocks)
            if with_blocks:
                selection = {"source_line_scope": "selected", "teacher_evidence_blocks": blocks,
                    "source_line_ranges": [{key: block[key] for key in (
                        "material_id", "start_line", "end_line")} for block in resolved]}
                if include_source_ids:
                    by_material = {material["material_id"]: material for material in pack["materials"]}
                    selection["teacher_evidence_source_ids"] = [by_material[block["material_id"]].get("source_id")
                                                               for block in resolved]
        else:
            text = "\n\n".join(material["text"] for material in pack["materials"])
            if with_blocks and include_source_ids:
                selection = {"teacher_evidence_blocks": [material["text"] for material in pack["materials"]],
                             "teacher_evidence_source_ids": [material.get("source_id") for material in pack["materials"]]}
        if include_source_ids and with_blocks and any(not isinstance(value, str) or not value.strip()
                                                      for value in selection["teacher_evidence_source_ids"]):
            raise ValueError("human_session_source_evidence_unavailable")
        pack_identity = pack["prefix_sha256"]
    elif recipe.get("source_line_protocol_version") == 1 and ranges is not None:
        raise ValueError("human_session_source_evidence_unavailable")
    if (context.get("kind") == "conversation" and ("messages" in context
            or not isinstance(text, str) or not text.strip())):
        # Older Agent rows serialized the complete transcript into task.
        # Its assistant answers are still candidates, never source proof.
        text = _recorded_conversation_evidence(context)
        source_kind = "human_provided"
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
            "source_kind": source_kind or ("document" if real_source else "human_provided" if human else "synthetic"),
            **({"evidence_pack_sha256": pack_identity} if pack_identity is not None else {}), **selection}


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
        if context.get("native_regeneration"):
            native_recipe = read_json(recipe_file)
            if (native_recipe.get("native_regeneration_version") != 1
                    or not (native_recipe.get("review_repair") or {}).get("native_regeneration")):
                raise ValueError("human_session_revision_not_authorized")
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
        evidence = revision_evidence(record, parent_recipe, parent_path)
        actual_messages = (record.get("question") if context.get("native_regeneration") else record_messages(record))
        if context.get("native_regeneration") and isinstance(actual_messages, str):
            actual_messages = [{"role": "user", "content": actual_messages}]
        if (digest(record) != context["content_sha256"] or canonical(actual_messages) != canonical(context["messages"])
                or ancestry != context["ancestors"] or context["depth"] != prior.get("depth", 0) + 1
                or context.get("evidence_pack_sha256") != evidence.get("evidence_pack_sha256")
                or any(context[key] != value for key, value in evidence.items())):
            raise ValueError("human_session_result_changed")
        return record
    except (OSError, KeyError, StopIteration, TypeError) as error:
        raise ValueError("human_session_revision_not_authorized") from error
