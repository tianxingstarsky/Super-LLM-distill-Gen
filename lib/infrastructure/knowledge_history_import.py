"""Import question-only history only from sealed, identical clean knowledge.

Legacy QA summaries identify candidates; they do not authenticate questions,
source scope or publication. Each run is verified once and its relevant record
files are streamed once, keeping at most twenty requested identities in memory.
No index, ledger, source file or training record is written here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from lib.domain.workflow_quality import canonical
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.knowledge_agent_session import knowledge_identity
from lib.infrastructure.local_input_cache import _assert_unlinked
from lib.infrastructure.verified_preview import inventory_identity
from lib.infrastructure.workflow_source_context import load_context_pack


MAX_IMPORT_ROWS = 20
_TARGETS = {"sft": ("sft", "cot", "dpo", "orpo", "rlaif"), "multiturn": ("multiturn",)}


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pack_receipts(record, run, expected):
    node, receipts = record, []
    for _ in range(9):
        if not isinstance(node, dict):
            break
        if "source_context_pack" in node:
            try:
                pack = load_context_pack(run, node["source_context_pack"])
                identity = knowledge_identity(pack["materials"])
            except ValueError as error:
                if str(error) == "source_context_pack_missing":
                    return None  # Older retained exports may no longer have their source cache.
                raise ValueError("knowledge_history_source_integrity_error") from None
            except (KeyError, TypeError, OSError):
                raise ValueError("knowledge_history_source_integrity_error") from None
            # An older matching ancestor cannot override a different current
            # evidence pack. Every referenced pack in this source chain agrees.
            if identity != expected:
                return None
            receipts.append(pack["id"])
        if "source_context" not in node:
            break
        node = node["source_context"]
    else:
        return None  # Ambiguous, excessively deep provenance is not imported.
    return sorted(set(receipts)) if receipts else None


def _question(record, target):
    from lib.domain.workflow_qa_director import QA_TYPES, validate_dialogue_design
    from lib.infrastructure.knowledge_agent_session import knowledge_task_identity
    from lib.infrastructure.workflow_qa_director import actual_qa_input_messages, learner_prompt
    contract = record.get("qa_contract")
    if not isinstance(contract, dict):
        return None
    question, visible, qa_type = (contract.get(key) for key in ("question", "visible_context", "qa_type"))
    if (not isinstance(question, str) or not question.strip() or len(question) > 12_000
            or not isinstance(visible, str) or len(visible) > 40_000 or qa_type not in QA_TYPES):
        return None
    try:
        actual = actual_qa_input_messages(record, target)
        first = next((message.get("content") for message in actual or [] if message.get("role") == "user"), None)
        if first != learner_prompt(contract):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    result = {"question": question, "visible_context": visible, "qa_type": qa_type}
    if "dialogue_design" in contract:
        try:
            validate_dialogue_design(contract["dialogue_design"])
        except (ValueError, TypeError):
            return None
        # Identity uses the actual stored design, not a normalised rewrite.
        # Its schema admits only bounded procedural notes, never answers.
        result["dialogue_design"] = deepcopy(contract["dialogue_design"])
    # Revision lineage is relevant to native task identity but may contain old
    # answers. Authenticate its identity from the sealed contract without
    # returning any of that private history to the planning/tool projection.
    namespace = "multiturn" if target == "multiturn" else "sft"
    result["task_identity"] = knowledge_task_identity(contract, namespace)
    return result


def verified_knowledge_history(output: Path, examples: list, knowledge_id: str) -> list:
    """Authenticate at most the first twenty summary references, in that order.

    Missing/incomplete legacy runs and records without accepted source packs
    are not migrated. Existing corrupt complete artifacts or altered pack
    receipts fail closed. Shared SFT/CoT projections count once; conflicting
    eligible records with the same ID are ambiguous and are not imported.
    """
    from lib.infrastructure.training_workflow import run_path, verify_artifacts
    if not isinstance(examples, list) or not isinstance(knowledge_id, str) or not re.fullmatch(r"[a-f0-9]{64}", knowledge_id):
        raise ValueError("knowledge_history_invalid_request")
    selected, groups, seen = [], defaultdict(set), set()
    for example in examples[:MAX_IMPORT_ROWS]:
        if not isinstance(example, dict):
            continue
        run_id, identity, namespace = (example.get(key) for key in ("run_id", "id", "namespace"))
        if (not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
                or not isinstance(identity, str) or not identity.startswith(run_id + ":")
                or not isinstance(namespace, str) or namespace not in _TARGETS):
            continue
        candidate = identity[len(run_id) + 1:]
        if (not candidate or len(candidate) > 500 or any(ord(char) < 32 for char in candidate)
                or (run_id, candidate, namespace) in seen):
            continue
        seen.add((run_id, candidate, namespace))
        selected.append((run_id, candidate, namespace))
        groups[run_id].add(candidate)

    verified = {}
    for run_id, wanted in groups.items():
        run = run_path(output, run_id)
        _assert_unlinked(run)
        _assert_unlinked(run / "artifacts")
        manifest_path = run / "artifacts/manifest.json"
        _assert_unlinked(manifest_path)
        if not manifest_path.is_file():
            continue
        try:
            header = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(header, dict) or header.get("status") != "complete":
                continue
            before = inventory_identity(run)
            manifest = verify_artifacts(run)
            if inventory_identity(run) != before:
                raise ValueError("knowledge_history_result_changed")
        except (OSError, ValueError, TypeError):
            raise ValueError("knowledge_history_artifact_integrity_error") from None
        if manifest.get("run_id") != run_id:
            raise ValueError("knowledge_history_artifact_integrity_error")
        namespaces = {namespace for parent, _, namespace in selected if parent == run_id}
        targets = tuple(dict.fromkeys(target for namespace in sorted(namespaces) for target in _TARGETS[namespace]))
        candidates, ambiguous = {}, set()
        for target in targets:
            filename = f"{target}.records.json"
            if filename not in manifest["sha256"]:
                continue
            path = run / "artifacts" / filename
            _assert_unlinked(path)
            records = iter_json_records(path, max_record_chars=20_000_000)
            occurrences = set()
            try:
                for record in records:
                    if not isinstance(record, dict) or record.get("id") not in wanted:
                        continue
                    candidate_id = record["id"]
                    namespace = "multiturn" if target == "multiturn" else "sft"
                    key = (candidate_id, namespace)
                    if candidate_id in occurrences:
                        ambiguous.add(key)
                    occurrences.add(candidate_id)
                    if record.get("status") != "eligible":
                        continue
                    question = _question(record, target)
                    packs = _pack_receipts(record, run, knowledge_id)
                    if not question or not packs:
                        ambiguous.add(key)
                        continue
                    candidate = {"id": run_id + ":" + candidate_id, "candidate_id": candidate_id,
                        "run_id": run_id, "namespace": namespace, **question,
                        "status": "final_eligible", "stage": target,
                        "knowledge_id": knowledge_id, "history_is_not_evidence": True,
                        "verification": {"scope": "sealed_record_and_identical_accepted_knowledge",
                            "manifest_sha256": next(value[-1] for value in before if value[0] == "manifest.json"),
                            "record_artifact": filename, "record_artifact_sha256": manifest["sha256"][filename],
                            "record_sha256": _sha(canonical(record)), "source_pack_ids": packs}}
                    old = candidates.get(key)
                    if old is not None and any(old.get(field) != candidate.get(field) for field in (
                            "question", "visible_context", "qa_type", "dialogue_design", "task_identity")):
                        ambiguous.add(key)
                    else:
                        candidates.setdefault(key, candidate)
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError("knowledge_history_record_integrity_error") from None
            finally:
                records.close()
        if inventory_identity(run) != before:
            raise ValueError("knowledge_history_result_changed")
        for key, value in candidates.items():
            if key not in ambiguous:
                verified[(run_id, *key)] = value
    return [verified[key] for key in selected if key in verified]
