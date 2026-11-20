"""Checkpoint individual calls and export finished cases in original JSONL order."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from copy import copy
import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore

from filelock import FileLock

from lib.contexts.models.contracts import ModelQueueTimeout
from ..application.generate_guided_answer import GenerateGuidedAnswer
from ..application.guided_verified_delivery import validate_completed_calculation
from ..application.solve_guided_calculations import EXPRESSION_INPUT
from ..domain.experience_evolution import digest
from ..domain.reasoning_bank import no_new_memory
from ..domain.guided_answers import read_teacher_submission, validate_source
from ..domain.guided_behavior_prediction import (
    BEHAVIOR_VERSIONS, observe_prediction, place_student_guidance, prediction_due, prediction_guidance, prediction_messages,
    prediction_schema, prediction_visible,
)
from ..domain.guided_disclosure import RENDERED_CONTINUATION_VERSIONS, render_student_continuation
from ..domain.guided_disclosure_text import checked_reviewer_pairing, locate_teacher_premise, render_authored_disclosure, render_operation_aliases
from ..domain.guided_protocol_versions import (
    PUBLIC_OPERATION_RENDERING_VERSIONS,
    ATTRIBUTED_TUTORING_VERSIONS, GUIDANCE_PLACEMENT_VERSIONS, SCOPED_SUBMISSION_VERSIONS, TYPED_SUBMISSION_VERSIONS, snapshot_version,
)
from ..domain.guided_local_steps import check_local_step, dispatched_local_guidance, local_public_reference, local_step_material, local_step_target
from ..domain.guided_teacher_delivery import (
    CALCULATION_PLAN_VERSIONS, CHECKED_TUTOR_VERSIONS, EVIDENCED_PROGRESS_VERSIONS, GUIDED_DELIVERY_VERSIONS, SOLVER_FINAL_VERSIONS,
    TRUNCATED_TUTORING_VERSIONS, requested_delivery, uses_direct_first,
)
from ..domain.guided_final_experience import adopted_final_teacher_text
from ..domain.guided_training_targets import (
    GUIDED_TARGET_FILE, GUIDED_TARGET_SCHEMA, GUIDED_TARGET_VERSIONS, GuidedTargetHeld, guided_training_target,
)
from .guided_llama import DIRECT_ANSWER_GRAMMARS, GuidedAdmissionTimeout, GuidedChatEndpoint, GuidedPromptOverflow, wire_body
from .guided_context_recovery import endpoint_role, freeze_context_recovery
from .guided_historical_answer import (
    HISTORICAL_FIRST, checked_historical_answer, historical_source_manifest, historical_source_membership,
    import_historical_answer,
)
from lib.io_utils import atomic_json
from lib.prompts.workflow_guided_answers import BARE_ANSWER_OPERATION_VERSION, VERSION

PAIRED_DISCLOSURE_VERSIONS = {"4.2.0", "4.2.1", "4.3.0"} | GUIDED_DELIVERY_VERSIONS
VERIFIED_STEP_VERSIONS = {"4.1.0", "4.1.1"} | PAIRED_DISCLOSURE_VERSIONS
TEACHER_DISCLOSURE_VERSIONS = {"3.9.0", "4.0.0"} | VERIFIED_STEP_VERSIONS
DIRECT_SYNTAX_VERSIONS = {"3.8.0"} | TEACHER_DISCLOSURE_VERSIONS
FINAL_CONTINUATION_VERSIONS = {"3.7.0"} | DIRECT_SYNTAX_VERSIONS
FIRST_ANSWER_VERSIONS = {"3.6.0"} | FINAL_CONTINUATION_VERSIONS
SUPPORTED_VERSIONS = {"3.0.0", "3.1.0", "3.2.0", "3.3.0", "3.4.0", "3.5.0"} | FIRST_ANSWER_VERSIONS


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def guided_port_response(response, payload, role, template_version):
    """Attach dispatch evidence to the use case after a completed call or authenticated replay."""
    if (template_version in TRUNCATED_TUTORING_VERSIONS and role == "student"
            and response.get("finish_reason") == "length"
            and payload.get("chat_template_kwargs", {}).get("enable_thinking") is False):
        return {**response, "completed_student_thinking_disabled": True}
    return response


def inputs(path, protocol_version=VERSION, trusted_step_receipts=frozenset()):
    verified_options = ({"allow_verified_teaching_steps": True, "trusted_step_receipts": trusted_step_receipts}
                        if protocol_version in VERIFIED_STEP_VERSIONS else {})
    with path.open(encoding="utf-8-sig") as source:
        for line in source:
            if line.strip():
                yield validate_source(json.loads(line), allow_answer_requirement=protocol_version in FIRST_ANSWER_VERSIONS,
                                      allow_task_system=protocol_version in FINAL_CONTINUATION_VERSIONS,
                                      allow_direct_syntax=protocol_version in DIRECT_SYNTAX_VERSIONS,
                                      allow_calculation_plan=protocol_version in CALCULATION_PLAN_VERSIONS, **verified_options)


def verified_step_registry(output, receipt_path, frozen_config):
    """An explicitly supplied verifier registry grants trust; a row cannot grant trust to itself."""
    archived = output / "verified-step-receipts.json"
    pinned = (frozen_config or {}).get("verified_step_registry_id")
    if not receipt_path and not pinned:
        return None, frozenset()
    registry = read(receipt_path or archived)
    if not isinstance(registry, dict) or registry.get("schema") != "guided-teaching-verification/v1":
        raise ValueError("unsupported teaching-step verifier receipt schema")
    hashes = registry.get("receipts")
    if not isinstance(hashes, list) or not hashes or not all(isinstance(h, str) and re.fullmatch("[0-9a-f]{64}", h) for h in hashes):
        raise ValueError("teaching-step verification requires SHA-256 receipts")
    canonical = {"schema": registry["schema"], "receipts": sorted(set(hashes))}
    if frozen_config is not None and digest(canonical) != pinned:
        raise ValueError("teaching-step verifier registry differs from the frozen run")
    return canonical, frozenset(hashes)


def location(root, ident):
    return root / "cases" / hashlib.sha256(ident.encode()).hexdigest()


def unsent_admission(attempt):
    # Old guided transports could raise this typed scheduler code only on lease entry.
    return attempt == {"status": "interrupted", "error": "ModelQueueTimeout", "http_status": ModelQueueTimeout.code} or (
        attempt.get("status") == "awaiting_capacity" and attempt.get("model_request_sent") is False
        and attempt.get("code") == ModelQueueTimeout.code
    )


def can_resume_admission(receipt, binding, ident, role, index):
    attempts = receipt.get("attempts")
    return (
        receipt.get("status") in {"interrupted", "awaiting_capacity"} and "response" not in receipt
        and "origin" not in receipt and receipt.get("role") == role and receipt.get("attempt") == index
        and isinstance(receipt.get("request"), dict)
        and receipt.get("identity") == digest({"binding": binding, "id": ident, "payload": receipt["request"]})
        and bool(attempts) and all(unsent_admission(item) for item in attempts)
    )


def capacity_stop(stop, outstanding, binding, ident):
    if not outstanding or not all(outstanding.values()):
        return False
    if not stop:
        return True
    if stop.get("binding") != binding or stop.get("id") != ident:
        return False
    if "checkpoint" in stop and stop["checkpoint"] not in outstanding:
        return False
    return (stop.get("status") == "awaiting_capacity"
            or (stop.get("error") == "ModelQueueTimeout" and stop.get("detail") == ModelQueueTimeout.code)
            or stop.get("detail") == "request outcome unresolved; explicit retry required")


def _check_rendered_guidance(case, attempt, payload, previous, hint, *, visible=None, experience=None, trusted=()):
    """Check the rendered tool frame against the ordered, reviewed guidance."""
    view = {
        "retained_solution": "",
        "confirmed_findings_from_your_own_work": [],
        "previously_disclosed_clues": previous,
        "your_last_formal_answer": (attempt.get("formal_answer") or {}).get("response", {}).get("content"),
        "new_clue": hint["text"],
    }
    language = str(case.get("source", {}).get("language") or "").lower()
    chinese = language.startswith("zh") if language else any("\u4e00" <= ch <= "\u9fff" for ch in case["question"])
    suffix = render_student_continuation(view, {}, chinese=chinese)
    content = payload["messages"][-1]["content"]
    fixed_fields = snapshot_version(case) in TYPED_SUBMISSION_VERSIONS
    scoped = fixed_fields or case.get("protocol") == "guided-draft-snapshot/v4.10.5"
    if scoped or case.get("protocol") in {"guided-draft-snapshot/v4.10.3", "guided-draft-snapshot/v4.10.4"}:
        task_receipts = trusted if snapshot_version(case) in ATTRIBUTED_TUTORING_VERSIONS else {hint.get("source_step_receipt")}
        target = local_step_target(case, hint, task_receipts, chinese=chinese,
                                   scoped=scoped, fixed_fields=fixed_fields)
        if target:
            if snapshot_version(case) in ATTRIBUTED_TUTORING_VERSIONS:
                if visible is None:
                    raise ValueError("missing verified public context for the scoped local submission")
                student = (experience or {}).get("student")
                dispatched_local_guidance(payload, case, target, chinese, public_reference=local_public_reference(visible, attempt),
                                          experience="\n\n" + student if student else "")
                return
            if scoped:
                content = dispatched_local_guidance(payload, case, target, chinese)
            frame = local_step_material(target)
            if not content.startswith(frame):
                raise ValueError("student request differs from the published local operation")
            content = content[len(frame):]
        elif payload["messages"][-1]["role"] != "tool":
            raise ValueError("a program-framed local task needs a verified operation")
    if content == suffix:
        return
    if content.endswith("\n\n" + suffix):
        # Only learner evidence may precede the known guidance sections.
        prefix = content[:-(len(suffix) + 2)]
        reserved = {"此前收到的提示", "上一次需要重新判断的答复", "本轮提示"} if chinese else {
            "Earlier guidance", "Your previous answer to reconsider", "Next hint",
        }
        learner_sections = {"你已证实的步骤", "你已得到的结果和依据", "与题意相符、可继续尝试的思路"} if chinese else {
            "Your established steps", "Results established in your work", "Approaches grounded in the question",
        }
        if (prefix.partition("\n")[0] in learner_sections
                and all(section.partition("\n")[0] not in reserved for section in prefix.split("\n\n"))):
            return
    raise ValueError("student request differs from its reviewed disclosure history")


def _behavior_delivery(case, before, after, folder, student_receipt, trusted, endpoints, protocol_version):
    """Rebuild the predictor's public context and match its dispatch, adoption and subsequent observation."""
    index, hint = after["attempt"], before["disclosure"]
    disclosed = case["disclosures"][:hint["ordinal"]]
    if not disclosed or disclosed[-1] != hint or hint.get("issued_after_attempt") != before["attempt"]:
        raise ValueError("behavior prediction has no matching published disclosure")
    frame = student_receipt["request"]["messages"][-1]
    prefix = "Continue the solution from this current view.\n"
    full_view = (frame.get("role") == "tool" and frame["content"].startswith(prefix)
                 and bool(json.loads(frame["content"][len(prefix):]).get("complete_disclosure")))
    if bool(after.get("solving_role")) != full_view:
        raise ValueError("complete disclosure role differs from the dispatched student view")
    due = prediction_due(protocol_version, before, disclosed, received_full=full_view,
                         solver_role=after.get("solving_role", "student"))
    record = after.get("behavior_prediction")
    path = folder / f"{index:03}-behavior_prediction.json"
    payload = student_receipt["request"]
    if not due:
        if record is not None or path.exists():
            raise ValueError("unexpected behavior prediction outside a published tutoring turn")
        return payload, None, None
    if not isinstance(record, dict) or record.get("checkpoint") != {"role": "behavior_prediction", "attempt": index}:
        raise ValueError("missing or mismatched behavior prediction checkpoint")
    if record.get("protocol_version", "4.10.7") != protocol_version:
        raise ValueError("behavior prediction differs from its frozen protocol")
    versions = case["draft_versions"][:index - 1]
    prior = versions[-1]
    if prior["attempt"] != before["attempt"] or record.get("disclosure_ordinal") != hint["ordinal"]:
        raise ValueError("behavior prediction does not follow the bound disclosure")
    segments = {s["id"]: s for version in versions for s in version["student_suffix"]}
    retained = [segments[key] for key in prior["retained_after"]]
    language = str(case.get("source", {}).get("language") or "").lower()
    chinese = language.startswith("zh") if language else any("\u4e00" <= c <= "\u9fff" for c in case["question"])
    target = local_step_target(case, hint, trusted, chinese=chinese, scoped=True, fixed_fields=True)
    visible = prediction_visible(case["question"], disclosed, retained, prior["shared_findings"],
                                 local_task=target["request"] if target else None)
    if record.get("visible") != visible:
        raise ValueError("behavior prediction context differs from the published work")
    receipt = read(path)
    _check_target_receipt(receipt, ("behavior_prediction", index, record["response"], prediction_messages(visible, protocol_version)),
                          case, protocol_version)
    native = receipt["request"].get("response_format") or {}
    if (native.get("type") != "json_schema" or native.get("json_schema") !=
            {"name": "learner_behavior_prediction", "strict": True, "schema": prediction_schema(visible, protocol_version)}
            or receipt["request"].get("model") != endpoints["coach"]["model"]):
        raise ValueError("behavior prediction differs from its frozen model or submission schema")
    forecast, guidance = prediction_guidance(receipt["response"], visible, chinese=chinese, protocol_version=protocol_version)
    placement = None
    if protocol_version in GUIDANCE_PLACEMENT_VERSIONS:
        sent = bool(guidance and payload["messages"][-1] == guidance)
        base_messages = payload["messages"][:-1] if sent else payload["messages"]
        schema = (payload.get("response_format") or {}).get("json_schema", {}).get("schema")
        guidance, placement = place_student_guidance(guidance, base_messages, target, schema, single_task=protocol_version in ATTRIBUTED_TUTORING_VERSIONS)
        if sent != (guidance is not None) or record.get("guidance_delivery") != placement:
            raise ValueError("behavior guidance placement differs from the dispatched student request")
    _check_target_receipt(student_receipt, ("student", index, after["student"], after["student_request_messages"]), case, protocol_version)
    # Identity failure or truncation may have ended this turn before the local checker ran.
    if after.get("local_step") is not None:
        if not target or check_local_step(target, student_receipt["response"], index, scoped=True)[0] != after["local_step"]:
            raise ValueError("behavior outcome differs from the replayed local student submission")
    elif (target and after["student"].get("finish_reason") == "stop" and after["student"]["content"].strip()
          and after["student"].get("model") == endpoints["student"]["model"] and not after["student"].get("reasoning_content")):
        raise ValueError("missing local learner outcome for a completed behavior prediction")
    observed = observe_prediction(forecast, after, question=case["question"], gold=case.get("gold"),
                                  expected_model=endpoints["student"]["model"])
    if any(key not in record or record[key] != value for key, value in (
            ("forecast", forecast), ("student_guidance", guidance), ("observation", observed))):
        raise ValueError("behavior prediction, applied guidance or observed learner action differs from its receipt")
    if guidance is not None:
        if payload["messages"][-1] != guidance:
            raise ValueError("the student did not receive the selected behavior guidance")
        payload = {**payload, "messages": payload["messages"][:-1]}
    if not target:
        initial = case["attempts"][0]["student_request_messages"]
        if (payload["messages"][0].get("role") != "system" or payload["messages"][1:-1] != initial[1:]
                or payload["messages"][-1].get("role") != "tool"):
            raise ValueError("unexpected material alongside the public behavior guidance")
    return payload, {"receipt": path.name, "role": "behavior_prediction", "requested_model": receipt["request"]["model"],
                     "reported_model": receipt["response"].get("model"), "forecast": forecast,
                     "guidance_applied": guidance is not None, "student_guidance": guidance, "observation": observed,
                     **({"guidance_delivery": placement} if placement is not None else {})}, visible


def verify_teacher_rendering(case, attempt, work, folder, trusted):
    """Bind a display substitution to the author's native submission, signed operation and actual review."""
    protocol = snapshot_version(case)
    if protocol not in PUBLIC_OPERATION_RENDERING_VERSIONS:
        return None
    recorded = work.get("operation_aliases", [])
    if not work.get("draft") or "draft_response" not in work:
        if recorded or work.get("review_checkpoint") and work.get("proposed_by") != "program":
            raise ValueError("missing original teacher draft for reviewed wording")
        return None
    number = attempt["attempt"]
    revision = work["revision"]
    author = "disclosure_rewrite" if revision else "disclosure_draft"
    native = read(folder / f"{number:03}-{author}.json")
    _check_target_receipt(native, (author, number, work["draft_response"], None), case, protocol)
    raw = read_teacher_submission(native["response"])
    if raw != work["draft"]:
        raise ValueError("teacher draft differs from its model submission")
    decision = raw.get("decision") if isinstance(raw, dict) else None
    changes = []
    if isinstance(decision, str) and decision.startswith("apply:"):
        step_id = decision.removeprefix("apply:")
        display, changes = render_operation_aliases(raw, case["question"], step_id)
    if not changes and not recorded:
        return None
    if not changes:
        raise ValueError("operation aliases differ from the original teacher wording")
    catalog = case.get("verified_teaching_steps") or {}
    reference = {"source_step_id": step_id, "source_step_receipt": catalog.get("receipt_id"), "text": ""}
    language = (case.get("source") or {}).get("language")
    chinese = (str(language).lower().startswith("zh") if language else
               any("\u4e00" <= c <= "\u9fff" for c in case["question"]))
    target = local_step_target(case, reference, trusted, chinese=chinese, scoped=True, fixed_fields=True)
    if target is None:
        if recorded:
            raise ValueError("displayed operation lacks its trusted local task")
        return None
    if changes != recorded:
        raise ValueError("operation aliases differ from the original teacher wording")
    task = {k: target[k] for k in ("request", "submission_fields") if k in target}
    offered = json.loads(native["request"]["messages"][-1]["content"])["available_operations"]
    if not any(o["id"] == step_id and o.get("student_task") == task for o in offered):
        raise ValueError("displayed operation was not offered to the author")
    if "public_text" not in work and not work.get("review_checkpoint"):
        return None  # An earlier validation withheld the wording.
    anchor = locate_teacher_premise(case["question"], raw["question_quote"])["quote"]
    bridge, hint, public, quotations = render_authored_disclosure(
        display["bridge"].strip(), display["hint"].strip(), case["question"], anchor)
    if public != work.get("public_text") or quotations != work.get("question_quotations"):
        raise ValueError("recorded public wording differs from the rendered teacher draft")
    checkpoint = work.get("review_checkpoint")
    if checkpoint:
        reviewer = "disclosure_recheck" if revision else "disclosure"
        if checkpoint != {"role": reviewer, "index": number, "student_attempt": number}:
            raise ValueError("rendered disclosure has an unbound reviewer")
        review = read(folder / f"{number:03}-{reviewer}.json")
        _check_target_receipt(review, (reviewer, number, work["review_response"], None), case, protocol)
        proposal = json.loads(review["request"]["messages"][-1]["content"])["proposed_next_teacher_disclosure"]
        applied = proposal.get("applied_operation") or {}
        step = next(s for s in catalog["steps"] if s["id"] == step_id)
        signed = {k: step[k] for k in ("id", "operation", "quote", "span")}
        if (proposal.get("public_text") != public or proposal.get("bridge") != bridge or proposal.get("hint") != hint
                or proposal.get("question_quote") != anchor or applied != {**signed, "student_task": task}):
            raise ValueError("independent reviewer did not receive the rendered teacher wording and task")
        if work.get("assessment") and work["assessment"] != read_teacher_submission(review["response"]):
            raise ValueError("rendered teacher assessment differs from its reviewer response")
    published = attempt.get("disclosure") or {}
    if published and work.get("validated_citations") and checkpoint != published.get("review_checkpoint"):
        raise ValueError("released operation wording lacks its independent review checkpoint")
    if checkpoint and published.get("review_checkpoint") == checkpoint:
        verdict = checked_reviewer_pairing(work.get("assessment"), work["latest_disclosure_id"])
        if (verdict.get("decision") != "release" or not work.get("validated_citations") or published.get("text") != public
                or published.get("bridge") != bridge or published.get("candidate_text") != hint
                or published.get("source_step_id") != step_id or published.get("source_step_receipt") != target["receipt"]):
            raise ValueError("published disclosure differs from the reviewed operation wording")
    return public


def disclosure_delivery(case, attempt, folder, protocol_version=None, *, trusted=(), endpoints=None, experience=None):
    """Pair a published hint with the exact next student request and completed response."""
    hint = attempt.get("disclosure")
    if not hint:
        return None
    protocol_version = protocol_version or str(case.get("protocol", "")).removeprefix("guided-draft-snapshot/v")
    index = attempt["attempt"] + 1
    filename = f"{index:03}-student.json"
    receipt = read(folder / filename)
    payload = receipt["request"]
    if (receipt["identity"] != digest({"binding": case["binding"], "id": case["id"], "payload": payload})
            or receipt["status"] != "complete" or receipt["role"] != "student" or receipt["attempt"] != index):
        raise ValueError("next student response is not bound to the published disclosure")
    previous = [h["text"] for h in case["disclosures"][:hint["ordinal"] - 1]]
    rendered_protocol = protocol_version in RENDERED_CONTINUATION_VERSIONS
    if rendered_protocol:
        attempts = case["attempts"]
        if len(attempts) < index or attempts[index - 1]["student_request_messages"] != payload["messages"]:
            raise ValueError("student request differs from the recorded continuation")
    behavior = None
    visible = None
    if protocol_version in BEHAVIOR_VERSIONS:
        payload, behavior, visible = _behavior_delivery(case, attempt, case["attempts"][index - 1], folder, receipt,
                                                       trusted, endpoints, protocol_version)
    frame = payload["messages"][-1]
    prefix = "Continue the solution from this current view.\n"
    if frame["role"] != "tool" and not (protocol_version in SCOPED_SUBMISSION_VERSIONS and frame["role"] == "user"):
        raise ValueError("next student request is missing its disclosure")
    if frame["role"] == "tool" and frame["content"].startswith(prefix):
        view = json.loads(frame["content"][len(prefix):])
        if rendered_protocol and "complete_disclosure" not in view:
            raise ValueError("next student request is missing its rendered disclosure")
        if view["new_clue"] != hint["text"] or view["previously_disclosed_clues"] != previous:
            raise ValueError("student request differs from its reviewed disclosure history")
    elif rendered_protocol:
        _check_rendered_guidance(case, attempt, payload, previous, hint, visible=visible, experience=experience, trusted=trusted)
    else:
        raise ValueError("next student request is missing its disclosure")
    return {"ordinal": hint["ordinal"], "student_attempt": index, "student_receipt": filename,
            "student_finish_reason": receipt["response"]["finish_reason"],
            **({"behavior_prediction": behavior} if behavior is not None else {})}


def replay_first(path, ident, payload, prior_binding, *, expected_receipt_sha256=None):
    relative = location(Path("."), ident) / "001-student.json"
    receipt_bytes = (path / relative).read_bytes()
    prior = json.loads(receipt_bytes)
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    if expected_receipt_sha256 is not None and (receipt_sha256 != expected_receipt_sha256 or prior.get("origin")):
        raise ValueError("worked first answer must match its frozen original receipt")
    wire = wire_body(payload)
    identity = digest({"binding": prior_binding, "id": ident, "payload": payload})
    if (
        prior.get("status") != "complete"
        or prior.get("role") != "student"
        or prior.get("attempt") != 1
        or prior.get("identity") != identity
        or wire_body(prior["request"]) != wire
        or prior.get("origin", {}).get("kind") == "replayed_first"
        or not prior.get("attempts")
        or prior["attempts"][-1]["status"] != "complete"
    ):
        raise ValueError(
            "first-student replay requires an actual completed request with the same bound id and identical wire body"
        )
    return prior["response"], {
        "kind": "replayed_first",
        "source_binding": prior_binding,
        "source_receipt": relative.as_posix(),
        "source_receipt_sha256": receipt_sha256,
        "source_identity": identity,
        "wire_request_sha256": hashlib.sha256(wire).hexdigest(),
        "response_sha256": digest(prior["response"]),
    }


def replay_matching_call(root, ident, role, index, payload, prior_binding, *, expected_sha256=None,
                         original_runs=None, visited=frozenset()):
    relative = location(Path("."), ident) / f"{index:03}-{role}.json"
    path = root / relative
    if not path.exists():
        if expected_sha256:
            raise ValueError("pinned context recovery response is missing")
        return None
    raw = path.read_bytes()
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("pinned context recovery response has changed")
    prior = json.loads(raw)
    envelope = {"binding": prior_binding, "id": ident, "payload": prior["request"]}
    if prior["identity"] != digest(envelope) or (prior["role"], prior["attempt"]) != (role, index):
        raise ValueError("prior guided receipt does not match its original task and role")
    wire = wire_body(payload)
    if wire != wire_body(prior["request"]):
        if expected_sha256:
            raise ValueError("context recovery cannot regenerate a previously completed request")
        return None
    origin = prior.get("origin")
    if origin:
        if not original_runs:
            raise ValueError("matching receipt is itself a replay; supply its original run for this call")
        source_binding = origin.get("source_binding")
        if prior_binding in visited or source_binding in visited or source_binding == prior_binding:
            raise ValueError("cycle in frozen replay provenance")
        last = (prior.get("attempts") or [{}])[-1]
        if (prior.get("status") != "complete" or last.get("status") != "replayed"
                or last.get("new_model_calls") != 0):
            raise ValueError("unconfirmed replay provenance")
        parent = original_runs.get(source_binding)
        if parent is None:
            raise ValueError("a required original run was not supplied")
        current_manifest = read(root / "manifest.json")
        parent_raw = (parent / "manifest.json").read_bytes()
        parent_manifest = json.loads(parent_raw)
        keys = (("reuse_first", "reuse_worked_first") if origin.get("kind") == "replayed_first"
                else ("reuse_matching", "context_recovery"))
        pins = [current_manifest.get(key) or {} for key in keys]
        if (digest(current_manifest) != prior_binding or digest(parent_manifest) != source_binding
                or not any(pin.get("source_binding") == source_binding
                           and pin.get("source_manifest_sha256") == hashlib.sha256(parent_raw).hexdigest() for pin in pins)
                or not re.fullmatch(r"[a-f0-9]{64}", str(origin.get("source_receipt_sha256", "")))):
            raise ValueError("original replay evidence differs from its bound manifest")
        parent_sha = origin["source_receipt_sha256"]
        if origin.get("kind") == "replayed_matching_call":
            original = replay_matching_call(parent, ident, role, index, payload, source_binding,
                                            expected_sha256=parent_sha, original_runs=original_runs,
                                            visited=visited | {prior_binding})
        elif origin.get("kind") == "replayed_first" and (role, index) == ("student", 1):
            original = replay_first(parent, ident, payload, source_binding, expected_receipt_sha256=parent_sha)
        else:
            raise ValueError("unsupported original receipt provenance")
        if original != (prior["response"], origin):
            raise ValueError("archived replay differs from its original response or provenance")
    elif can_resume_admission(prior, prior_binding, ident, role, index):
        return None
    elif prior["status"] != "complete" or prior["attempts"][-1]["status"] != "complete":
        raise ValueError("identical prior call has no confirmed response")
    return prior["response"], {
        "kind": "replayed_matching_call", "source_binding": prior_binding,
        "source_receipt": relative.as_posix(), "source_receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "source_identity": prior["identity"], "wire_request_sha256": hashlib.sha256(wire).hexdigest(),
        "response_sha256": digest(prior["response"]),
    }


def run_guided_cases(
    input_path,
    output,
    endpoints,
    experience,
    max_attempts,
    *,
    export_only=False,
    first_pass_only=False,
    prefetch_worked_first=False,
    case_ids=None,
    protocol_version=None,
    until_correct=None,
    retry_interrupted=False,
    workers=4,
    reuse_first_from=None,
    reuse_worked_first_from=None,
    verified_step_receipts=None,
    reuse_matching_from=None,
    reuse_original_runs=None,
    recover_context_from=None,
    context_proof=None,
    context_proof_sha256=None,
    historical_first_from=None,
    historical_first_input=None,
):
    prefetch_limit = prefetch_worked_first if type(prefetch_worked_first) is int else None
    if type(prefetch_worked_first) not in (bool, int) or (prefetch_limit is not None and prefetch_limit < 1):
        raise ValueError("worked first-answer prefetch count must be positive")
    if prefetch_worked_first and (first_pass_only or export_only or retry_interrupted):
        raise ValueError("worked first-answer prefetch cannot also export, run final-only admission or retry an unresolved request")
    if case_ids is not None and (not isinstance(case_ids, (list, tuple, set, frozenset))
                                 or not case_ids or not all(isinstance(ident, str) and ident for ident in case_ids)):
        raise ValueError("case selection requires one or more source IDs")
    selected = frozenset(case_ids) if case_ids is not None else None
    if ((context_proof is None) != (context_proof_sha256 is None)
            or (context_proof is not None and (not recover_context_from or export_only))):
        raise ValueError("a sealed recovery requires its proof file, pinned SHA-256 and original run")
    if reuse_original_runs and not reuse_matching_from:
        raise ValueError("original replay runs require an immediate matching source")
    if recover_context_from and (not selected or any((reuse_matching_from, reuse_first_from, reuse_worked_first_from,
            historical_first_from, historical_first_input, retry_interrupted, first_pass_only, prefetch_worked_first))
            or recover_context_from.resolve() == output.resolve()):
        raise ValueError("context recovery requires selected cases and a separate output without other replay or retry modes")
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / "run.lock"), timeout=0):
        manifest = output / "manifest.json"
        frozen_config = read(manifest) if manifest.exists() else None
        template_version = frozen_config["template_version"] if frozen_config else (protocol_version or VERSION)
        historical_mode = historical_first_from is not None or bool((frozen_config or {}).get("historical_first"))
        if historical_first_input is not None and not historical_mode:
            raise ValueError("the historical input file requires a historical source run")
        if reuse_worked_first_from and reuse_first_from:
            raise ValueError("worked first-answer replay requires a single source run")
        if historical_mode and (reuse_first_from or reuse_worked_first_from):
            raise ValueError("choose historical first answers or exact request replay")
        if protocol_version and protocol_version != template_version:
            raise ValueError("the requested protocol differs from the frozen run")
        if until_correct is None:
            until_correct = frozen_config.get("until_correct", False) if frozen_config else template_version in PAIRED_DISCLOSURE_VERSIONS
        if until_correct and template_version not in TEACHER_DISCLOSURE_VERSIONS:
            raise ValueError("progress-based continuation requires guided protocol 3.9.0 or later")
        registry, trusted = verified_step_registry(output, verified_step_receipts, frozen_config)
        if registry and template_version not in VERIFIED_STEP_VERSIONS:
            raise ValueError("verified teaching steps require guided protocol 4.1 or later")
        with input_path.open("rb") as fp:
            source_sha = hashlib.file_digest(fp, "sha256").hexdigest()
        seen = set()
        has_direct_questions = False
        for source in inputs(input_path, template_version, trusted):
            if source["id"] in seen:
                raise ValueError("duplicate guided source id")
            seen.add(source["id"])
            if uses_direct_first(source, template_version):
                has_direct_questions = True
            elif historical_mode:
                raise ValueError("historical first answers are supported only for explicit direct-first questions")
            elif first_pass_only:
                raise ValueError("the independent first pass requires final_only on every input question and its delivery instructions")
        if not seen:
            raise ValueError("empty source file")
        if selected and selected - seen:
            raise ValueError(f"case IDs absent from the frozen input: {sorted(selected - seen)}")
        if export_only:
            if read(manifest)["input_sha256"] != source_sha:
                raise ValueError("input differs from frozen guided run")
            return export(input_path, output, selected=selected)
        if (
            not isinstance(experience, dict)
            or not isinstance(experience.get("version"), str)
            or not experience["version"].strip()
        ):
            raise ValueError("experience must declare its version")
        if not frozen_config and no_new_memory(experience.get("disclosure_teacher")):
            raise ValueError("A no-update reply contains no teacher advice for a new guided run")
        if any(
            not isinstance(experience.get(role, ""), str)
            for role in ("student", "coach", "disclosure_teacher")
        ):
            raise ValueError("experience role instructions must be strings")
        adopted_final_teacher_text(experience, template_version)
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError("worker limit must be between one and four")
        if template_version not in SUPPORTED_VERSIONS:
            raise ValueError(
                "unsupported frozen generator version; use offline export or its original release"
            )
        if "continuation" in endpoints and template_version not in EVIDENCED_PROGRESS_VERSIONS:
            raise ValueError("a stronger continuation student requires guided protocol 4.8")
        if frozen_config and endpoints.keys() != frozen_config["endpoints"].keys():
            raise ValueError("configured roles differ from the frozen run endpoints")
        coach = endpoints["coach"]
        if isinstance(coach, GuidedChatEndpoint):
            if template_version in BEHAVIOR_VERSIONS and not coach.local:
                raise ValueError("behavior prediction requires a local tutor endpoint")
            if coach is endpoints["student"]:
                # One server may serve both roles, but thinking is a per-role request setting.
                coach = copy(coach)
                endpoints = {**endpoints, "coach": coach}
            prior_coach = frozen_config["endpoints"]["coach"] if frozen_config else None
            coach.configure_teacher_reasoning(template_version, prior_coach)
        for role, ep in endpoints.items():
            if isinstance(ep, GuidedChatEndpoint):
                if template_version in SOLVER_FINAL_VERSIONS and role in {"student", "continuation"} and not ep.local:
                    raise ValueError(f"the {role} endpoint requires a native llama.cpp server for guided answer continuation")
                prior = frozen_config["endpoints"][role] if frozen_config else None
                ep.configure_sampling(template_version, prior)
        capabilities = {role: ep.validate() for role, ep in endpoints.items()}
        stronger = capabilities.get("continuation")
        if stronger and stronger["model"] == capabilities["student"]["model"]:
            raise ValueError("the continuation student must name a distinct model")
        capacities = {}
        role_servers = {}
        for role, spec in capabilities.items():
            server = spec.get("url", "").rstrip("/").removesuffix("/v1").rstrip("/")
            role_servers[role] = server
            slots = spec.get("slots") or workers
            capacities[server] = min(slots, capacities.get(server, slots))
        server_limits = {server: BoundedSemaphore(slots) for server, slots in capacities.items()}
        if template_version in FIRST_ANSWER_VERSIONS:
            concurrency = min(workers, sum(capacities.values()))
        else:
            concurrency = min(workers, *capacities.values())
        config = dict(
            schema="guided-answer-run/v3",
            input_sha256=source_sha,
            source_count=len(seen),
            max_attempts=max_attempts,
            concurrency=concurrency,
            template_version=template_version,
            experience=experience,
            endpoints=capabilities,
        )
        if template_version in BEHAVIOR_VERSIONS:
            config["behavior_prediction"] = {"endpoint": "coach", "role": "behavior_prediction",
                                             "checkpoint_index": "following_student_attempt"}
        if template_version in TEACHER_DISCLOSURE_VERSIONS:
            config["until_correct"] = until_correct
        if (template_version in CALCULATION_PLAN_VERSIONS
                and (frozen_config is None or "draft_candidate_observation" in frozen_config)):
            config["draft_candidate_observation"] = "marked-student-draft/v1"
        if registry:
            config["verified_step_registry_id"] = digest(registry)
            if (template_version in CALCULATION_PLAN_VERSIONS
                    and (frozen_config is None or "student_calculation_format" in frozen_config)):
                config["student_calculation_format"] = EXPRESSION_INPUT
            if (template_version in CALCULATION_PLAN_VERSIONS and stronger
                    and (frozen_config is None or "strong_calculation_readout" in frozen_config)):
                config["strong_calculation_readout"] = "two-verified-mismatches/v1"
            if (template_version in CHECKED_TUTOR_VERSIONS
                    and (frozen_config is None or "bare_answer_operation_version" in frozen_config)):
                config["bare_answer_operation_version"] = BARE_ANSWER_OPERATION_VERSION
        historical_rows = None
        if historical_mode:
            archived_manifest = output / "historical-first-manifest.json"
            if historical_first_from is not None and historical_first_from.resolve() == output.resolve():
                raise ValueError("historical answers must come from a different frozen run")
            prior_bytes = ((historical_first_from / "manifest.json") if historical_first_from else archived_manifest).read_bytes()
            config["historical_first"] = historical_source_manifest(prior_bytes, config)
            members_file = output / "historical-first-membership.json"
            expected_members = (frozen_config or {}).get("historical_first", {}).get("source_membership_sha256")
            if historical_first_input is not None or expected_members:
                members = (historical_source_membership(historical_first_input, seen, config["historical_first"]["source_input_sha256"])
                           if historical_first_input is not None else read(members_file))
                checksum = digest(members)
                if frozen_config and checksum != expected_members:
                    raise ValueError("historical input membership differs from the frozen run")
                if members_file.is_file() and digest(read(members_file)) != checksum:
                    raise ValueError("archived historical input membership differs")
                if not members_file.exists():
                    atomic_json(members_file, members)
                config["historical_first"]["source_membership_sha256"] = checksum
                historical_rows = members["rows"]
            if archived_manifest.exists():
                if archived_manifest.read_bytes() != prior_bytes:
                    raise ValueError("archived historical source manifest differs")
            elif not frozen_config:
                pending_manifest = archived_manifest.with_suffix(".pending")
                pending_manifest.write_bytes(prior_bytes)
                pending_manifest.replace(archived_manifest)
        matching_roles = set()
        recovery = None
        if recover_context_from:
            recovery = freeze_context_recovery(recover_context_from, config, selected, endpoints, location,
                                               context_proof, context_proof_sha256)
            config["context_recovery"] = recovery
        original_runs = {}
        if reuse_matching_from:
            if reuse_matching_from.resolve() == output.resolve():
                raise ValueError("cross-run replay requires a different source run")
            prior_bytes = (reuse_matching_from / "manifest.json").read_bytes()
            prior = json.loads(prior_bytes)
            prior_endpoints = prior["endpoints"]
            matching_roles = set(prior_endpoints)
            comparable = config["endpoints"]
            # A new optional reader has no historical calls. Keep every original role and
            # capability identical, then authenticate each reused request against its receipt.
            if (template_version in CALCULATION_PLAN_VERSIONS and prior["template_version"] == template_version
                    and set(prior_endpoints) == {"student", "coach"}
                    and set(comparable) == {"student", "coach", "continuation"}):
                comparable = {role: comparable[role] for role in prior_endpoints}
            if prior_endpoints != comparable:
                raise ValueError("cross-run replay requires the same endpoints for every previously configured role")
            for key in ("input_sha256", "experience", "verified_step_registry_id"):
                if prior.get(key) != config.get(key):
                    raise ValueError(f"cross-run replay requires the same {key}")
            config["reuse_matching"] = {"source_binding": digest(prior),
                                        "source_manifest_sha256": hashlib.sha256(prior_bytes).hexdigest()}
            if reuse_original_runs:
                pins = {}
                for original in reuse_original_runs:
                    raw = (original / "manifest.json").read_bytes()
                    ancestor = json.loads(raw)
                    identity = digest(ancestor)
                    if (original.resolve() in {output.resolve(), reuse_matching_from.resolve()}
                            or identity == config["reuse_matching"]["source_binding"] or identity in original_runs
                            or ancestor.get("input_sha256") != config["input_sha256"]):
                        raise ValueError("original replay runs must be distinct ancestors of the same input")
                    original_runs[identity] = original
                    pins[identity] = hashlib.sha256(raw).hexdigest()
                config["reuse_matching"]["original_manifests"] = pins
        prior_first_root = reuse_first_from or reuse_worked_first_from
        if prior_first_root:
            if prior_first_root.resolve() == output.resolve():
                raise ValueError(
                    "the first-answer replay source must be a different frozen run"
                )
            prior_bytes = (prior_first_root / "manifest.json").read_bytes()
            prior_manifest = json.loads(prior_bytes)
            if (
                prior_manifest.get("endpoints", {}).get("student")
                != capabilities["student"]
            ):
                raise ValueError(
                    "first-student replay requires the same endpoint, served model and generation capabilities"
                )
            replay_source = {
                "source_binding": digest(prior_manifest),
                "source_manifest_sha256": hashlib.sha256(prior_bytes).hexdigest(),
            }
            if reuse_worked_first_from:
                if prior_manifest.get("input_sha256") != source_sha:
                    raise ValueError("worked first-answer replay requires the same input file")
                previous = (frozen_config or {}).get("reuse_worked_first")
                if previous:
                    receipts = previous["receipts"]
                else:
                    receipts = {}
                    with FileLock(str(prior_first_root / "run.lock"), timeout=0):
                        for source in inputs(input_path, template_version, trusted):
                            if source.get("answer_requirement") == "worked_solution" and source["gold"] is not None:
                                first = location(prior_first_root, source["id"]) / "001-student.json"
                                if first.is_file():
                                    raw = first.read_bytes()
                                    if not can_resume_admission(json.loads(raw), replay_source["source_binding"],
                                                                source["id"], "student", 1):
                                        receipts[source["id"]] = hashlib.sha256(raw).hexdigest()
                config["reuse_worked_first"] = {**replay_source, "receipts": receipts}
            else:
                config["reuse_first"] = replay_source
        if frozen_config is not None and frozen_config != config:
            raise ValueError(
                "configuration, input or experience differs from the frozen run"
            )
        if not manifest.exists():
            if registry:
                atomic_json(output / "verified-step-receipts.json", registry)
            atomic_json(manifest, config)
        binding = digest(config)

        def process(source, first_pass):
            folder = location(output, source["id"])
            receipt = folder / "case.json"
            interruption = folder / "interruption.json"
            current_checkpoint = None
            resumed_interruption = False
            parent_case = recovery["cases"][source["id"]] if recovery else None
            visited_parents = set()
            if receipt.exists():
                if read(receipt)["binding"] != binding:
                    raise ValueError("completed case has different run binding")
                return

            def request(role, index, messages, schema):
                nonlocal current_checkpoint, resumed_interruption
                checkpoint = folder / f"{index:03}-{role}.json"
                current_checkpoint = checkpoint.name
                native_final = role in {"final_answer", "strong_final_answer"}
                serving_role = endpoint_role(role)
                endpoint = endpoints[serving_role]
                payload = (endpoint.final_answer_payload(messages) if native_final
                           else endpoint.payload(messages, schema))
                if role == "behavior_prediction":
                    payload["response_format"]["json_schema"]["name"] = "learner_behavior_prediction"
                syntax = source.get("direct_answer_syntax")
                first_direct = role == "student" and index == 1 and uses_direct_first(source, template_version)
                final_grammar = native_final and (template_version not in SOLVER_FINAL_VERSIONS
                                                  or requested_delivery(source) == "final_only")
                calculation_probe = (template_version in CALCULATION_PLAN_VERSIONS and role == "student" and index == 1
                                     and source.get("verified_calculation_plan") and requested_delivery(source) == "worked_solution")
                if role in {"verified_answer", "verified_answer_retry", "verified_strong_answer"} or calculation_probe:
                    payload["grammar"] = DIRECT_ANSWER_GRAMMARS["keyed_rational"]
                elif syntax and (first_direct or final_grammar):
                    payload["grammar"] = DIRECT_ANSWER_GRAMMARS[syntax]
                identity = digest(
                    {"binding": binding, "id": source["id"], "payload": payload}
                )
                saved = read(checkpoint) if checkpoint.exists() else {}
                pinned = parent_case["receipts"] if parent_case else {}
                previous_hash = pinned.get(checkpoint.name)
                if parent_case and (checkpoint.name == parent_case["failed"] or previous_hash is None):
                    required = set(pinned)
                    if checkpoint.name == parent_case["failed"]:
                        required.remove(checkpoint.name)
                    if not required.issubset(visited_parents):
                        raise ValueError("context recovery did not replay the original calls in execution order")
                    if previous_hash:
                        raw = (location(recover_context_from, source["id"]) / checkpoint.name).read_bytes()
                        if (hashlib.sha256(raw).hexdigest() != previous_hash or
                                wire_body(json.loads(raw)["request"]) != wire_body(payload)):
                            raise ValueError("recovered request differs from the exact preflight rejection")
                if previous_hash:
                    visited_parents.add(checkpoint.name)
                if first_direct and historical_mode:
                    history = config["historical_first"]
                    if not saved:
                        if historical_first_from is None:
                            raise ValueError("missing frozen historical answer; supply its original run directory")
                        saved = import_historical_answer(
                            location(historical_first_from, source["id"]), source, payload, history, binding,
                            same_input=history["source_input_sha256"] == source_sha,
                            membership=historical_rows[source["id"]] if historical_rows is not None else None)
                        atomic_json(checkpoint, saved)
                    response = checked_historical_answer(saved, source, history, binding, proposed=payload,
                                                         membership=historical_rows[source["id"]] if historical_rows is not None else None)
                    return guided_port_response(response, saved["request"], role, template_version)
                if saved and saved["identity"] != identity:
                    raise ValueError("request differs from its saved checkpoint")
                if saved.get("status") == "complete":
                    if parent_case and previous_hash and checkpoint.name != parent_case["failed"]:
                        original, origin = replay_matching_call(recover_context_from, source["id"], role, index, payload,
                                                                recovery["source_binding"], expected_sha256=previous_hash)
                        if saved.get("origin") != origin or saved["response"] != original:
                            raise ValueError("saved recovery response does not match its pinned parent")
                    return guided_port_response(saved["response"], payload, role, template_version)
                if saved and not retry_interrupted and not can_resume_admission(saved, binding, source["id"], role, index):
                    raise RuntimeError(
                        "request outcome unresolved; explicit retry required"
                    )
                if saved and interruption.exists():
                    prior_stop = read(interruption)
                    resumed_interruption = (
                        prior_stop.get("binding") == binding and prior_stop.get("id") == source["id"]
                        and prior_stop.get("checkpoint", current_checkpoint) == current_checkpoint
                    )
                previous = saved.get("attempts", [])
                replayed = None
                if parent_case and previous_hash and checkpoint.name != parent_case["failed"]:
                    replayed = replay_matching_call(recover_context_from, source["id"], role, index, payload,
                                                    recovery["source_binding"], expected_sha256=previous_hash)
                elif (reuse_first_from and role == "student" and index == 1
                        and (template_version not in GUIDED_DELIVERY_VERSIONS or first_direct)):
                    replayed = replay_first(
                        reuse_first_from,
                        source["id"],
                        payload,
                        config["reuse_first"]["source_binding"],
                    )
                elif (reuse_worked_first_from and role == "student" and index == 1
                      and source["id"] in config["reuse_worked_first"]["receipts"]):
                    parent = config["reuse_worked_first"]
                    replayed = replay_first(reuse_worked_first_from, source["id"], payload, parent["source_binding"],
                                            expected_receipt_sha256=parent["receipts"][source["id"]])
                elif reuse_matching_from and serving_role in matching_roles:
                    replayed = replay_matching_call(reuse_matching_from, source["id"], role, index, payload,
                                                    config["reuse_matching"]["source_binding"], original_runs=original_runs)
                    # Legacy native receipts may have copied the requested model into the response.
                    if (replayed and native_final and template_version in SOLVER_FINAL_VERSIONS
                            and not replayed[0].get("model_identity_source")):
                        replayed = None
                if replayed:
                    response, origin = replayed
                    atomic_json(
                        checkpoint,
                        dict(
                            identity=identity,
                            role=role,
                            attempt=index,
                            request=payload,
                            status="complete",
                            origin=origin,
                            response=response,
                            attempts=[*previous, {"status": "replayed", "new_model_calls": 0}],
                        ),
                    )
                    return guided_port_response(response, payload, role, template_version)
                event = dict(
                    identity=identity,
                    role=role,
                    attempt=index,
                    request=payload,
                    status="pending",
                    attempts=[*previous, {"status": "pending"}],
                )
                atomic_json(checkpoint, event)
                try:
                    with server_limits[role_servers[serving_role]]:
                        response = endpoint.complete(payload)
                except GuidedAdmissionTimeout:
                    event["status"] = "awaiting_capacity"
                    event["attempts"][-1] = {
                        "status": "awaiting_capacity", "code": ModelQueueTimeout.code, "model_request_sent": False,
                    }
                    atomic_json(checkpoint, event)
                    raise
                except GuidedPromptOverflow as overflow:
                    event["status"] = "interrupted"
                    event["attempts"][-1] = {"status":"preflight_rejected","error":"GuidedPromptOverflow",
                                            "model_request_sent":False,**overflow.counts}
                    atomic_json(checkpoint, event)
                    raise
                except Exception as err:
                    event["status"] = "interrupted"
                    event["attempts"][-1] = {
                        "status": "interrupted",
                        "error": type(err).__name__,
                        "http_status": getattr(err, "code", None),
                        "dispatch": "unknown",
                    }
                    atomic_json(checkpoint, event)
                    raise
                event.update(status="complete", response=response)
                event["attempts"][-1] = {
                    "status": "complete",
                    "usage": response.get("usage", {}),
                }
                atomic_json(checkpoint, event)
                return guided_port_response(response, payload, role, template_version)

            try:
                result = GenerateGuidedAnswer(request).run(
                    source,
                    experience,
                    max_attempts,
                    protocol_version=template_version,
                    first_pass_only=first_pass and not prefetch_worked_first,
                    stop_after_first_student_receipt=bool(prefetch_worked_first),
                    until_correct=until_correct,
                    bare_answer_operations="bare_answer_operation_version" in config,
                    expression_input=config.get("student_calculation_format") == EXPRESSION_INPUT,
                    observe_draft_candidates="draft_candidate_observation" in config,
                    allow_strong_calculation_readout="strong_calculation_readout" in config,
                    expected_first_model=capabilities["student"]["model"] if template_version in CALCULATION_PLAN_VERSIONS else None,
                    **({"continuation_model": stronger["model"] if stronger else None,
                        "trusted_solution_receipts": trusted}
                       if template_version in EVIDENCED_PROGRESS_VERSIONS else {}),
                )
            except Exception as error:
                atomic_json(
                    interruption,
                    dict(
                        id=source["id"],
                        binding=binding,
                        error=type(error).__name__,
                        detail=str(error),
                        status="awaiting_capacity" if isinstance(error, GuidedAdmissionTimeout) else "interrupted",
                        checkpoint=current_checkpoint,
                    ),
                )
                return source["id"]
            if result is None:
                if resumed_interruption:
                    interruption.unlink(missing_ok=True)
                return
            if parent_case and set(parent_case["receipts"]) != visited_parents:
                raise ValueError("recovered case ended without consuming its bound parent history")
            if historical_mode:
                result["first_answer_origin"] = read(folder / "001-student.json")["origin"]
            if template_version in GUIDED_TARGET_VERSIONS:
                result["training_target_file"] = GUIDED_TARGET_FILE
            atomic_json(
                receipt, {**result, "binding": binding, "source_sha256": digest(source)}
            )
            interruption.unlink(missing_ok=True)

        failed_first = set()
        def run_pass(first_pass):
            scheduled = 0
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                pending = set()
                for source in inputs(input_path, template_version, trusted):
                    if selected is not None and source["id"] not in selected:
                        continue
                    if prefetch_worked_first:
                        if prefetch_limit is not None and scheduled >= prefetch_limit:
                            break
                        if source.get("answer_requirement") != "worked_solution" or source["gold"] is None:
                            continue
                    elif first_pass and source.get("answer_requirement") != "final_only":
                        continue
                    if source["id"] in failed_first:
                        continue
                    pending.add(pool.submit(process, source, first_pass))
                    scheduled += 1
                    if len(pending) == concurrency:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for item in done:
                            failed = item.result()
                            if first_pass and failed:
                                failed_first.add(failed)
                for item in pending:
                    failed = item.result()
                    if first_pass and failed:
                        failed_first.add(failed)
            return scheduled

        if prefetch_worked_first:
            eligible = run_pass(True)
            return {"prefetch_complete": not failed_first, "eligible_worked_questions": eligible,
                    "unresolved_first_answers": sorted(failed_first)}
        if has_direct_questions:
            run_pass(True)
            first_summary = export(input_path, output)
            if historical_mode and failed_first:
                first_summary["first_pass_complete"] = False
                atomic_json(output / "summary.json", first_summary)
                return first_summary
            if first_pass_only:
                first_summary["first_pass_complete"] = not failed_first
                atomic_json(output / "summary.json", first_summary)
                return first_summary
        run_pass(False)
        return export(input_path, output, selected=selected)


def _target_receipts(case):
    """Identify the original author, student input, and final response behind a projection."""
    expected = {}
    for attempt in case["attempts"]:
        index = attempt["attempt"]
        role = attempt.get("solving_role", "student")
        expected[f"{index:03}-{role}.json"] = (role, index, attempt["student"], attempt["student_request_messages"])
        if "coach_response" in attempt:
            expected[f"{index:03}-coach.json"] = ("coach", index, attempt["coach_response"], None)
        if "formal_answer" in attempt:
            delivery = "strong_final_answer" if role == "strong_continuation" else "final_answer"
            name = f"{index:03}-{delivery}.json"
            if attempt["formal_answer"].get("provenance", {}).get("receipt") != name:
                raise ValueError("solver delivery points to a different receipt")
            expected[name] = (delivery, index, attempt["formal_answer"]["response"], None)
    course = case.get("calculation_tutoring")
    if course:
        for action in course["actions"]:
            expected[action["receipt"]] = (action["role"], action["index"], action["response"], action["request_messages"])
        delivery = course["final_delivery"]
        prior = delivery.get("prior") or {}
        for item in (prior.get("prior"), prior, delivery):
            if item:
                expected[item["receipt"]] = (item["role"], item["index"], item["response"], item["messages"])
    return expected


def _check_target_receipt(event, expectation, case, protocol):
    role, index, response, messages = expectation
    payload = event["request"]
    if (event["status"] != "complete" or event["attempt"] != index or event["role"] != role
            or event["identity"] != digest({"binding": case["binding"], "id": case["id"], "payload": payload})
            or guided_port_response(event["response"], payload, role, protocol) != response
            or (messages is not None and payload.get("messages") != messages)):
        raise ValueError(f"guided training case {case['id']} differs from its archived model receipt {index:03}-{role}.json")


def export(input_path, output, *, selected=None):
    manifest = read(output / "manifest.json")
    history = manifest.get("historical_first")
    historical_rows = {}
    if history:
        raw = (output / "historical-first-manifest.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != history["source_manifest_sha256"]:
            raise ValueError("historical source manifest no longer matches the frozen run")
        if "source_membership_sha256" in history:
            members = read(output / "historical-first-membership.json")
            if digest(members) != history["source_membership_sha256"]:
                raise ValueError("historical input membership differs from the frozen run")
            historical_rows = members["rows"]
    _, trusted = verified_step_registry(output, None, manifest)
    binding = digest(manifest)
    project_targets = manifest["template_version"] in GUIDED_TARGET_VERSIONS
    target_counts, target_holds, evidence_counts = {}, {}, {}
    calculation_operations, calculation_readers = {}, {}
    usage_by_role = {}
    behavior_counts = {"observed_student_turns": 0, "guidance_applied": 0, "action_agreement": {}, "outcome_agreement": {}}
    if manifest["template_version"] in GUIDANCE_PLACEMENT_VERSIONS:
        behavior_counts["by_guidance_delivery"] = {}
    counts = dict(accepted=0, unverified=0, quarantine=0, interrupted=0, pending=0)
    accepted_routes = dict(nonthinking_direct=0, reviewed_solution=0)
    if manifest["template_version"] in TEACHER_DISCLOSURE_VERSIONS:
        counts["awaiting_guidance"] = 0
    if manifest["template_version"] in FINAL_CONTINUATION_VERSIONS:
        accepted_routes["guided_reasoning"] = 0
        accepted_routes["unaided_reasoning"] = 0
    calls = dict(new_completed=0, replayed_first=0, interrupted_or_pending=0)
    historical_tokens = {}
    historical_outcomes = dict(accepted_old_direct_answers=0, accepted_after_new_student_turn=0,
                               accepted_after_processing_old_derivation=0, new_sampling_first_answers=0)
    disclosed_outcomes = {"weak_student": 0, "strong_continuation": 0}
    admission_timeouts = 0
    with TemporaryDirectory(dir=output) as temp:
        scratch = Path(temp)
        with ExitStack() as stack:
            streams = {
                name: stack.enter_context(
                    (scratch / f"{name}.jsonl").open(
                        "w", encoding="utf-8", newline="\n"
                    )
                )
                for name in ("accepted", "unverified", "quarantine", "feedback",
                             *((Path(GUIDED_TARGET_FILE).stem,) if project_targets else ()),
                             *(("awaiting_guidance", "teacher_feedback") if "awaiting_guidance" in counts else ()))
            }

            def emit(name, row):
                streams[name].write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )

            for source in inputs(input_path, manifest["template_version"], trusted):
                folder = location(output, source["id"])
                archive = folder / "case.json"
                case_bytes = archive.read_bytes() if archive.is_file() else None
                case = json.loads(case_bytes) if case_bytes is not None else None
                expected = _target_receipts(case) if project_targets and case and case["status"] == "accepted" else {}
                outstanding = {}
                historical_origin = None
                for cp in folder.glob("[0-9]*-*.json"):
                    event = read(cp)
                    if manifest["template_version"] in CALCULATION_PLAN_VERSIONS and event["status"] == "complete":
                        group = "reused" if event.get("origin") else "new"
                        roles = usage_by_role.setdefault(group, {})
                        totals = roles.setdefault(event["role"], {"responses": 0, "usage_unreported": 0,
                                                                 "prompt_tokens": 0, "completion_tokens": 0})
                        totals["responses"] += 1
                        usage = event["response"].get("usage") or {}
                        if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
                            totals["prompt_tokens"] += usage["prompt_tokens"]
                            totals["completion_tokens"] += usage["completion_tokens"]
                        else:
                            totals["usage_unreported"] += 1
                    if cp.name in expected:
                        _check_target_receipt(event, expected.pop(cp.name), case, manifest["template_version"])
                    if history and cp.name == "001-student.json":
                        checked_historical_answer(event, source, history, binding, membership=historical_rows.get(source["id"]))
                        historical_origin = event["origin"]
                    if event["status"] != "complete":
                        serial, role = cp.stem.split("-", 1)
                        outstanding[cp.name] = can_resume_admission(event, binding, source["id"], role, int(serial))
                    for item in event["attempts"]:
                        status = item["status"]
                        if unsent_admission(item):
                            admission_timeouts += 1
                        elif status == "historical_input" and event.get("origin", {}).get("kind") == HISTORICAL_FIRST:
                            calls["historical_first_inputs"] = calls.get("historical_first_inputs", 0) + 1
                            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                value = event["response"].get("usage", {}).get(field)
                                if type(value) is int:
                                    historical_tokens[field] = historical_tokens.get(field, 0) + value
                        elif status == "replayed":
                            kind = ("replayed_matching" if event.get("origin", {}).get("kind") == "replayed_matching_call"
                                    else "replayed_first")
                            calls[kind] = calls.get(kind, 0) + 1
                        elif status == "complete":
                            calls["new_completed"] += 1
                        elif status in {"interrupted", "pending"}:
                            calls["interrupted_or_pending"] += 1
                if expected:
                    raise ValueError(f"missing archived model receipt for guided training case {source['id']}: {', '.join(sorted(expected))}")
                if case is None:
                    path = folder / "interruption.json"
                    stop = read(path) if path.exists() else {}
                    if capacity_stop(stop, outstanding, binding, source["id"]):
                        counts["awaiting_capacity"] = counts.get("awaiting_capacity", 0) + 1
                    else:
                        counts["interrupted" if stop or outstanding else "pending"] += 1
                        if stop:
                            emit("quarantine", stop)
                    continue
                if case["binding"] != binding or case["source_sha256"] != digest(
                    source
                ):
                    raise ValueError("case differs from its original source or run")
                if manifest["template_version"] in BEHAVIOR_VERSIONS:
                    predictions = {f"{a['attempt']:03}-behavior_prediction.json" for a in case["attempts"]
                                   if "behavior_prediction" in a}
                    if (any(case.get(key) != value for key, value in source.items())
                            or predictions != {p.name for p in folder.glob("*-behavior_prediction.json")}
                            or any("behavior_prediction" in a and (n == 0 or not case["attempts"][n - 1].get("disclosure"))
                                   for n, a in enumerate(case["attempts"]))):
                        raise ValueError("behavior predictions differ from the frozen source or published teaching turns")
                status = case["status"]
                provenance = case.get("continuation_provenance", {})
                if status == "accepted" and provenance.get("reference_answer_disclosed"):
                    solver = "strong_continuation" if provenance["solver_role"] == "strong_continuation" else "weak_student"
                    disclosed_outcomes[solver] += 1
                if "historical_first" in manifest:
                    if historical_origin is None or case.get("first_answer_origin") != historical_origin:
                        raise ValueError("case differs from its historical first-answer evidence")
                    if status == "accepted":
                        if case["training_route"] == "nonthinking_direct":
                            historical_outcomes["accepted_old_direct_answers"] += 1
                        elif provenance.get("reference_answer_disclosed"):
                            key = "accepted_with_disclosed_reference"
                            historical_outcomes[key] = historical_outcomes.get(key, 0) + 1
                        elif len(case["attempts"]) > 1:
                            historical_outcomes["accepted_after_new_student_turn"] += 1
                        else:
                            historical_outcomes["accepted_after_processing_old_derivation"] += 1
                counts[status] += 1
                if status == "accepted" and manifest["template_version"] in FIRST_ANSWER_VERSIONS:
                    accepted_routes[case["training_route"]] += 1
                public_keys = (
                    "id",
                    "question",
                    "source",
                    "source_reasoning",
                    "gold",
                    "mode",
                    "answer_requirement",
                    "training_route",
                    "final_answer_provenance",
                    "first_answer_origin",
                    "status",
                    "verification",
                    "answer",
                    "messages",
                    "experience_version",
                    "disclosures",
                    "shared_student_findings",
                    "continuation",
                    "continuation_provenance",
                )
                archive_ref = archive.relative_to(output).as_posix()
                target_info = {}
                if project_targets and status == "accepted":
                    if any(case.get(key) != value for key, value in source.items()):
                        raise ValueError("guided training case differs from its frozen question or reference")
                    if (source.get("verified_calculation_plan")
                            and case["attempts"][0]["student"].get("model") != manifest["endpoints"]["student"]["model"]):
                        raise ValueError("guided calculation first solver differs from the frozen student endpoint")
                    target_info = {"training_target_file": GUIDED_TARGET_FILE}
                    try:
                        strong_reader = (manifest["endpoints"].get("continuation", {}).get("model")
                                         if manifest.get("strong_calculation_readout") == "two-verified-mismatches/v1" else None)
                        steps = (validate_completed_calculation(case, trusted, continuation_model=strong_reader)
                                 if "calculation_tutoring" in case else None)
                        target = guided_training_target(case, archive_ref, trusted, calculation_steps=steps)
                    except GuidedTargetHeld as hold:
                        reason = str(hold)
                        target_info["training_target_held"] = reason
                        target_holds[reason] = target_holds.get(reason, 0) + 1
                    else:
                        target["guided_training_target"]["case_sha256"] = hashlib.sha256(case_bytes).hexdigest()
                        target_info["training_target_sha256"] = digest(target)
                        objective = target["guided_training_target"]["objective"]
                        target_counts[objective] = target_counts.get(objective, 0) + 1
                        if manifest["template_version"] in CALCULATION_PLAN_VERSIONS:
                            origin = target["guided_training_target"]
                            basis = origin["evidence_basis"]
                            evidence_counts[basis] = evidence_counts.get(basis, 0) + 1
                            if "operations" in origin:
                                reader = origin["answer_author"]
                                calculation_readers[reader] = calculation_readers.get(reader, 0) + 1
                                for step in origin["operations"]:
                                    kind = step["operation_origin"]
                                    calculation_operations[kind] = calculation_operations.get(kind, 0) + 1
                        emit(Path(GUIDED_TARGET_FILE).stem, target)
                emit(
                    status,
                    {
                        **{key: case[key] for key in public_keys if key in case},
                        "case_archive": archive_ref,
                        **target_info,
                    },
                )
                for attempt in case["attempts"]:
                    paired_protocol = manifest["template_version"] in PAIRED_DISCLOSURE_VERSIONS
                    delivery = (disclosure_delivery(case, attempt, folder, manifest["template_version"],
                                                    trusted=trusted, endpoints=manifest["endpoints"],
                                                    experience=manifest["experience"]) if paired_protocol else None)
                    behavior = (delivery or {}).get("behavior_prediction")
                    if behavior:
                        behavior_counts["observed_student_turns"] += 1
                        behavior_counts["guidance_applied"] += int(behavior["guidance_applied"])
                        if "guidance_delivery" in behavior:
                            placements = behavior_counts["by_guidance_delivery"]
                            placement_status = behavior["guidance_delivery"]["status"]
                            placements[placement_status] = placements.get(placement_status, 0) + 1
                        for key in ("action_agreement", "outcome_agreement"):
                            level = behavior["observation"][key]
                            totals = behavior_counts[key]
                            totals[level] = totals.get(level, 0) + 1
                    for work in attempt.get("disclosure_work", []):
                        verify_teacher_rendering(case, attempt, work, folder, trusted)
                        published = bool(work.get("validated_citations") and attempt.get("disclosure"))
                        if paired_protocol:
                            reviewed = work.get("review_checkpoint")
                            published = bool(delivery and reviewed and reviewed == attempt["disclosure"].get("review_checkpoint"))
                        emit("teacher_feedback", {
                            "id": case["id"], "case_archive": (folder / "case.json").relative_to(output).as_posix(),
                            "attempt": attempt["attempt"], "revision": work["revision"],
                            "draft": work.get("draft"), "assessment": work.get("assessment"),
                            **({"verified_operation": work["verified_operation"]} if "verified_operation" in work else {}),
                            "validated_citations": work.get("validated_citations", []),
                            "issue": work.get("issue"),
                            "published": published,
                            **({key: work[key] for key in ("review_checkpoint", "public_text", "question_quotations", "arithmetic_errors", "operation_aliases")
                                if key in work} if paired_protocol else {}),
                            **({"delivery": delivery} if paired_protocol and published else {}),
                            "experience_version": case["experience_version"],
                        })
                    review = attempt.get("coach") or {}
                    check = attempt["check"]
                    emit(
                        "feedback",
                        dict(
                            id=case["id"],
                            question=case["question"],
                            attempt=attempt["attempt"],
                            student_content=attempt.get(
                                "candidate_content", attempt["student"]["content"]
                            ),
                            student_suffix=attempt["student"]["content"],
                            answer_matches=check["answer_matches"],
                            format_ok=check["format_ok"],
                            issue=check["issue"],
                            verdict=review.get("verdict"),
                            diagnosis=review.get("diagnosis"),
                            hint_to_student=attempt["hint_to_student"],
                            mode=case["mode"],
                            status=status,
                            experience_version=case["experience_version"],
                            **({"following_behavior_prediction": behavior} if behavior else {}),
                            **({"verified_stage": attempt["verified_stage"]} if "verified_stage" in attempt else {}),
                            **({"solving_role": attempt["solving_role"], "solver_model": attempt["student"].get("model")}
                               if "solving_role" in attempt else {}),
                        ),
                    )
        for path in scratch.glob("*.jsonl"):
            os.replace(path, output / path.name)
    summary = {
        "binding": binding,
        "source_count": manifest["source_count"],
        "counts": counts,
        **({"accepted_by_route": accepted_routes} if manifest["template_version"] in FIRST_ANSWER_VERSIONS else {}),
        "model_calls": calls,
        **({"usage_by_role": usage_by_role} if manifest["template_version"] in CALCULATION_PLAN_VERSIONS else {}),
        **({"behavior_predictions": behavior_counts} if manifest["template_version"] in BEHAVIOR_VERSIONS else {}),
        **({"training_targets": {"schema": GUIDED_TARGET_SCHEMA, "file": GUIDED_TARGET_FILE,
                                 "by_objective": target_counts, "held_by_reason": target_holds,
                                 **({"by_evidence_basis": evidence_counts,
                                     "calculation_operations_by_origin": calculation_operations,
                                     "calculation_readouts_by_role": calculation_readers}
                                    if manifest["template_version"] in CALCULATION_PLAN_VERSIONS else {})}}
           if project_targets else {}),
        **({"accepted_after_full_disclosure": disclosed_outcomes} if manifest["template_version"] in EVIDENCED_PROGRESS_VERSIONS else {}),
        **({"historical_first_outcomes": historical_outcomes} if "historical_first" in manifest else {}),
        **({"historical_first_usage": historical_tokens} if historical_tokens else {}),
        **({"admission_timeouts": admission_timeouts} if admission_timeouts else {}),
        "complete": counts["pending"] == counts["interrupted"] == counts.get("awaiting_capacity", 0) == 0,
    }
    atomic_json(output / "summary.json", summary)
    if selected is not None:
        return {
            **summary,
            "selection_complete": all((location(output, ident) / "case.json").is_file() for ident in selected),
            "selected_case_count": len(selected),
        }
    return summary
