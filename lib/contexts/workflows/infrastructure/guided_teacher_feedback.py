"""Record observed teaching outcomes against frozen local model requests and source material."""

import hashlib
import json
import sqlite3
from contextlib import closing
from filelock import FileLock

from ..application.guided_experience import observed_teacher_disclosure
from ..application.guided_teacher_feedback import MIXED_INTERMEDIATE, OBSERVED_REWARDS, observed_followup
from ..domain.experience_evolution import digest
from ..domain.guided_answers import inspect_answer, read_teacher_submission
from ..domain.guided_local_steps import check_local_step, dispatched_local_guidance, local_step_material, local_step_target
from ..domain.guided_student_calculations import attributable_mixed_calculations, recovered_student_calculations
from ..domain.guided_training_targets import GUIDED_TARGET_FILE
from .guided_answer_records import (
    disclosure_delivery, guided_port_response, inputs, location, read, replay_first, replay_matching_call, verified_step_registry,
)


def archive_receipt(folder, binding, ident, role, index, expected=None, protocol=None, parents=None):
    path = folder / f"{index:03}-{role}.json"
    row = read(path)
    if (row["role"] != role or row["attempt"] != index or row["status"] != "complete" or
            row["identity"] != digest({"binding": binding, "id": ident, "payload": row["request"]}) or
            not row["attempts"] or
            (expected is not None and guided_port_response(row["response"], row["request"], role, protocol) != expected)):
        raise ValueError(f"unbound teaching feedback receipt {ident}/{path.name}")
    origin = row.get("origin")
    if not origin and row["attempts"][-1]["status"] == "complete":
        return row
    parent = (parents or {}).get((origin or {}).get("source_binding"))
    if not parent or row["attempts"][-1].get("status") != "replayed":
        raise ValueError(f"original completed receipt required for {ident}/{path.name}")
    binding = origin["source_binding"]
    saved_hash = origin["source_receipt_sha256"]
    if role == "student" and index == 1 and origin["kind"] == "replayed_first":
        replay = replay_first(parent, ident, row["request"], binding, expected_receipt_sha256=saved_hash)
    elif origin["kind"] == "replayed_matching_call":
        replay = replay_matching_call(parent, ident, role, index, row["request"], binding,
                                     expected_sha256=saved_hash, original_runs=parents)
    else:
        raise ValueError(f"unsupported source receipt origin for {ident}/{path.name}")
    if not replay or replay != (row["response"], origin):
        raise ValueError(f"replayed teaching observation differs from original {ident}/{path.name}")
    return row


def fingerprint(receipt):
    # Different child manifests can replay exactly the same original operation.
    return digest({"role": receipt["role"], "request": receipt["request"], "response": receipt["response"]})


def teaching_events(case, folder, protocol, parents, experience, *, source, trusted, learner_model):
    for position, before in enumerate(case["attempts"]):
        number = before["attempt"]
        hint = before.get("disclosure")
        work = before.get("disclosure_work") or []
        if not hint and not work:
            continue
        event = {"case_id": case["id"], "round": number, "question": case["question"],
                 "training_disposition": case["status"], "author": None, "hint": None,
                 "evidence": {}, "student_excerpt": None, "receipts": {}}
        refs = event["receipts"]
        if not hint:
            event["category"] = "proposal_not_delivered"
        else:
            event["hint"] = hint["text"]
            item = next((w for w in work if w.get("review_checkpoint") and
                         w["review_checkpoint"] == hint.get("review_checkpoint")), None)
            origin = "program" if not item or item.get("proposed_by") == "program" else "teacher"
            event["author"] = origin
            if item and origin == "teacher":
                revision = item["revision"]
                observation = observed_teacher_disclosure(case, number, revision)
                if (observation["review_observation"]["verdict"]["keep"] is not True
                        or item.get("public_text") not in {hint.get("candidate_text"), hint["text"]}):
                    raise ValueError(f"released teacher text differs from its reviewed proposal: {case['id']}/{number}")
                role = "disclosure_rewrite" if revision else "disclosure_draft"
                author = archive_receipt(folder, case["binding"], case["id"], role,
                                         number, item["draft_response"], protocol, parents)
                if read_teacher_submission(author["response"]) != item["draft"]:
                    raise ValueError("teacher proposal differs from its submitted response")
                system = [m["content"] for m in author["request"]["messages"] if m["role"] == "system"]
                if experience.get("disclosure_teacher") and (len(system) != 1 or
                        "\n\n" + experience["disclosure_teacher"] not in system[0]):
                    raise ValueError("teacher action did not consume its frozen guidance")
                refs[role] = fingerprint(author)
                event["teacher_edition_text_sha256"] = digest(experience.get("disclosure_teacher", ""))
                event["author"] = "teacher_selected" if item.get("verified_operation") else "teacher_authored"
            if item:
                check = item["review_checkpoint"]
                review = archive_receipt(folder, case["binding"], case["id"], check["role"],
                                         check["index"], item["review_response"], protocol, parents)
                if read_teacher_submission(review["response"]) != item["assessment"]:
                    raise ValueError("disclosure assessment differs from its submitted response")
                refs["disclosure_review"] = fingerprint(review)
            event["action_properties"] = {"grounded_in_quoted_premise": bool(hint.get("question_quotations")),
                "linked_to_verifiable_operation": bool(hint.get("source_step_id")),
                "continues_a_previous_hint": hint["ordinal"] > 1,
                "cites_learner_result": any(str(c.get("evidence_id", "")).startswith(("student", "retained", "finding"))
                                            for c in hint.get("citations", []))}
            if position + 1 >= len(case["attempts"]):
                event["category"] = "awaiting_student"
            else:
                after = case["attempts"][position + 1]
                if after.get("solving_role", "student") != "student":
                    event["category"] = "changed_solver"
                else:
                    disclosure_delivery(case, before, folder, protocol)
                    for label, turn in (("previous_student", before), ("next_student", after)):
                        receipt = archive_receipt(folder, case["binding"], case["id"], "student",
                                                  turn["attempt"], turn["student"], protocol, parents)
                        refs[label] = fingerprint(receipt)
                    if "coach_response" in after:
                        refs["next_coach"] = fingerprint(archive_receipt(folder, case["binding"],
                            case["id"], "coach", after["attempt"], after["coach_response"], protocol, parents))
                    raw = after["student"]["content"]
                    check = inspect_answer(raw, case.get("gold"), question=case["question"])
                    matched = check.get("answer_matches") if check.get("format_ok") and (
                        check.get("answer_matches") is after["check"].get("answer_matches")) else None
                    stage = after.get("verified_stage") or {}
                    pieces, offset = [], 0
                    for line in raw.splitlines(keepends=True):
                        pieces.append({"id": str(offset), "text": line, "start": offset, "end": offset + len(line),
                                       "author": "student"})
                        offset += len(line)
                    calculations = recovered_student_calculations(case, pieces,
                        prior_hints=[h["text"] for h in case["disclosures"][:hint["ordinal"]]])
                    reproduced = sorted(set(stage.get("new_fact_ids") or ()).intersection(
                        "calculation:" + finding["calculation_key"] for finding in calculations.values()))
                    outcome, evidence = observed_followup(before, after, certified_value=matched,
                        exposed_answer=bool(stage.get("reference_answer_was_visible") or after.get("solving_role")),
                        replayed_calculations=reproduced)
                    event.update(outcome=outcome, evidence=evidence,
                        student_excerpt={"tail": raw[-900:], "chars": len(raw), "sha256": digest(raw),
                                         "is_excerpt": len(raw) > 900})
                    event["category"] = outcome if origin == "teacher" else "program_disclosure"
                    if protocol in {"4.10.3", "4.10.4", "4.10.5"} and not after.get("solving_role"):
                        language = str(source.get("source", {}).get("language") or "").lower()
                        chinese = language.startswith("zh") if language else any("\u4e00" <= c <= "\u9fff" for c in source["question"])
                        scoped = protocol == "4.10.5"
                        target = local_step_target(source, hint, trusted, chinese=chinese, scoped=scoped)
                        if target:
                            actual = (dispatched_local_guidance(receipt["request"], source, target, chinese)
                                      if scoped else receipt["request"]["messages"][-1]["content"])
                            if (not actual.startswith(local_step_material(target))
                                    or receipt["response"].get("model") != learner_model):
                                raise ValueError("local reply lacks the original dispatched operation or verified learner")
                            replay, facts = check_local_step(target, receipt["response"], after["attempt"], scoped=scoped)
                            if after.get("local_step") != replay:
                                raise ValueError("archived local outcome differs from the authenticated response")
                            evidence["local_operation"] = {**replay, "task_author": "program",
                                "verified_submissions": [{"text": f["text"], "source_span": f["source_span"]} for f in facts.values()]}
                            event.update(category="program_framed_local_response", outcome=replay["status"])
                        elif after.get("local_step"):
                            raise ValueError("local reply lacks its independently attested source operation")
                    incomplete_delivery = (matched is False or not after["check"].get("format_ok")
                                           or stage.get("unresolved_review_evidence") or after.get("reviewed_derivation_conflict")
                                           or after.get("coach_feedback_check")
                                           or (position + 2 == len(case["attempts"]) and case["status"] == "quarantine"))
                    if (reproduced and incomplete_delivery and event["author"] == "teacher_authored"
                            and event["category"] != "program_framed_local_response" and not after.get("solving_role")
                            and not stage.get("reference_answer_was_visible") and after["student"]["finish_reason"] == "stop"
                            and receipt["response"].get("model_identity_source") in {"response_model", "single_served_model"}
                            and receipt["response"].get("model") == learner_model):
                        seen_texts = [source["question"], *(m["content"] for m in receipt["request"]["messages"] if m["role"] != "system")]
                        seen_texts.extend(h["text"] for h in case["disclosures"][:hint["ordinal"]])
                        seen_texts.append(before["student"]["content"])
                        for earlier in case["attempts"][:position]:
                            earlier_call = archive_receipt(folder, case["binding"], case["id"], "student", earlier["attempt"],
                                                           earlier["student"], protocol, parents)
                            seen_texts.append(earlier_call["response"]["content"])
                        mixed = attributable_mixed_calculations(raw,
                            (f for f in calculations.values() if "calculation:" + f["calculation_key"] in reproduced),
                            case.get("shared_student_findings", []), seen_texts, after["attempt"])
                        if mixed:
                            event["learning_scope"] = MIXED_INTERMEDIATE
                            evidence["attributable_intermediate_calculations"] = mixed
        ident = digest({"case": case["source_sha256"], "round": number, "hint": event["hint"],
                        "receipts": refs, "category": event["category"]})
        yield {**event, "id": ident, "eligible": event["category"] in OBSERVED_REWARDS or event.get("learning_scope") == MIXED_INTERMEDIATE}


class TeacherFeedbackStore:
    def __init__(self, root):
        root.mkdir(exist_ok=True, parents=True)
        self.path = root / "teacher-feedback.sqlite3"
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, eligible INTEGER, category TEXT, record TEXT, consumed TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS induced (edition TEXT, event TEXT, PRIMARY KEY(edition,event))")
            db.execute("INSERT OR IGNORE INTO induced SELECT consumed,id FROM events WHERE consumed IS NOT NULL")
            db.execute("CREATE TABLE IF NOT EXISTS coverage (binding TEXT, edition TEXT, case_id TEXT, PRIMARY KEY(binding,case_id))")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def induction_lock(self):
        return FileLock(str(self.path.parent / "teacher-induction.lock"))

    def observe(self, run, source_path, selected=(), source_runs=()):
        manifest = read(run / "manifest.json")
        with source_path.open("rb") as source_file:
            if hashlib.file_digest(source_file, "sha256").hexdigest() != manifest["input_sha256"]:
                raise ValueError("source file differs from the run manifest")
        _, trust = verified_step_registry(run, None, manifest)
        binding, protocol = digest(manifest), manifest["template_version"]
        parents = {digest(read(path / "manifest.json")): path for path in source_runs}
        selected, found, pending, events = set(selected), set(), [], []
        released = {}
        if (run / GUIDED_TARGET_FILE).is_file():
            with (run / GUIDED_TARGET_FILE).open(encoding="utf-8") as target_file:
                for line in target_file:
                    target = json.loads(line)
                    if target.get("status") == "eligible" and (not selected or target["id"] in selected):
                        released[target["id"]] = target["guided_training_target"]
        with closing(self.connect()) as db, db:
            for source in inputs(source_path, protocol, trust):
                ident = source["id"]
                if selected and ident not in selected:
                    continue
                found.add(ident)
                db.execute("INSERT OR IGNORE INTO coverage VALUES(?,?,?)", (binding, manifest["experience"]["version"], ident))
                folder = location(run, ident)
                if not (folder / "case.json").is_file():
                    pending.append(ident)
                    continue
                case = read(folder / "case.json")
                try:
                    if (case["binding"] != binding or case["source_sha256"] != digest(source) or case["id"] != ident or
                            any(case.get(k) != source.get(k) for k in ("question", "gold", "source_reasoning", "source"))):
                        raise ValueError("case and original source/manifest differ")
                    observed = list(teaching_events(case, folder, protocol, parents, manifest["experience"],
                                                    source=source, trusted=trust, learner_model=manifest["endpoints"]["student"]["model"]))
                except (ValueError, KeyError, FileNotFoundError) as exc:
                    observed = [{"id": digest({"case": ident, "binding": binding, "unbound": str(exc)}),
                                 "case_id": ident, "eligible": False, "category": "unverified_receipt",
                                 "detail": str(exc).split("] ")[0] if isinstance(exc, FileNotFoundError) else str(exc)}]
                for event in observed:
                    record = {**event, "run_binding": binding, "experience_version": manifest["experience"]["version"]}
                    if record.get("receipts", {}).get("next_student"):
                        evidence, exported = record["evidence"], released.get(ident, {})
                        supported_delivery = (event["round"] + 1 == len(case["attempts"]) and case["status"] == "accepted"
                            and exported.get("case_binding") == binding and exported.get("source_sha256") == case["source_sha256"]
                            and exported.get("objective") not in {"teacher_demonstration", "calculation_demonstration"})
                        record["transfer_gain_supported"] = bool(event.get("learning_scope") != MIXED_INTERMEDIATE and event["eligible"] and (
                            evidence.get("replayed_new_calculations") or
                            (record["category"] == "newly_delivered_key_match" and (
                                source.get("answer_requirement") == "final_only" or supported_delivery))))
                    if record.get("receipts", {}).get("previous_student"):
                        record["comparison_key"] = digest({"source": case["source_sha256"], "round": record["round"],
                            "previous_student": record["receipts"]["previous_student"],
                            "other_role_settings": {k:v for k,v in manifest["experience"].items() if k not in (
                                "version", "disclosure_teacher", "disclosure_teacher_adoption")},
                            "contract": {k:v for k,v in manifest.items() if k not in (
                                "experience", "input_sha256", "source_count", "concurrency", "reuse_matching",
                                "reuse_first", "reuse_worked_first")}})
                    count = db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,NULL)", (event["id"],
                        event["eligible"], event["category"], json.dumps(record, ensure_ascii=False))).rowcount
                    if not count and event.get("action_properties"):
                        db.execute("UPDATE events SET eligible=?, record=? WHERE id=? AND json_extract(record,'$.run_binding')=?",
                                   (event["eligible"], json.dumps(record, ensure_ascii=False), event["id"], binding))
                    events.append({**record, "new": bool(count)})
            if selected - found:
                raise ValueError("case absent from frozen input: " + ", ".join(sorted(selected - found)))
        return {"status": "observed", "run_binding": binding, "experience_version": manifest["experience"]["version"],
                "selected": len(found), "awaiting_case": len(pending), "events": events, **self.status()}

    def status(self):
        with closing(self.connect()) as db:
            return {"by_category": dict(db.execute("SELECT category, COUNT(*) FROM events GROUP BY category")),
                    "eligible_unconsumed": db.execute("SELECT COUNT(*) FROM events WHERE eligible=1 AND consumed IS NULL").fetchone()[0]}

    def batch(self, size):
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record FROM events WHERE eligible=1 AND consumed IS NULL ORDER BY rowid LIMIT ?", (size,))]

    def consumed(self, events, receipt):
        with closing(self.connect()) as db, db:
            db.executemany("INSERT OR IGNORE INTO induced VALUES(?,?)", [(receipt, event["id"]) for event in events])
            db.executemany("UPDATE events SET consumed=? WHERE id=? AND consumed IS NULL",
                           [(receipt, event["id"]) for event in events])

    def edition_events(self, edition):
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record FROM events JOIN induced ON event=id WHERE edition=? ORDER BY events.rowid", (edition,))]

    def transfer_pairs(self, edition, parent, source_cases, teacher_text):
        with closing(self.connect()) as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT record FROM events WHERE json_extract(record,'$.comparison_key') IS NOT NULL")]
            cohort = {r[0] for r in db.execute("SELECT DISTINCT case_id FROM coverage WHERE edition=?", (edition,))} - source_cases
        old = {r["comparison_key"]:r for r in rows if r.get("teacher_edition_text_sha256") and r["experience_version"] == parent}
        pairs = []
        for new in rows:
            control = old.get(new.get("comparison_key"))
            if (control and new["experience_version"] == edition and new["case_id"] in cohort
                    and new.get("teacher_edition_text_sha256") == digest(teacher_text)):
                pairs.append({"id": new["case_id"], "old": control["category"], "new": new["category"],
                              "old_gain_supported": bool(control.get("transfer_gain_supported")),
                              "new_gain_supported": bool(new.get("transfer_gain_supported")),
                              "comparable": control["eligible"] and new["eligible"] and
                              all(r.get("learning_scope") != MIXED_INTERMEDIATE for r in (control, new))})
        for ident in sorted(cohort - {p["id"] for p in pairs}):
            pairs.append({"id": ident, "comparable": False, "old": None, "new": None})
        return pairs


def verify_applied(run, experience, selected=()):
    manifest = read(run / "manifest.json")
    if not experience.get("disclosure_teacher") or manifest.get("experience") != experience:
        raise ValueError("the run did not freeze this teacher experience")
    binding = digest(manifest)
    folders = [(ident, location(run, ident)) for ident in selected] if selected else [
        (read(path)["id"], path.parent) for path in (run / "cases").glob("*/case.json")]
    witnessed = []
    for ident, folder in folders:
        for path in sorted(folder.glob("*.json")):
            index, _, role = path.stem.partition("-")
            if role not in {"disclosure_draft", "disclosure_rewrite"} or not index.isdigit():
                continue
            row = read(path)
            if row.get("status") != "complete" or row.get("origin"):
                continue
            receipt = archive_receipt(folder, binding, ident, role, int(index))
            system = [m["content"] for m in receipt["request"]["messages"] if m["role"] == "system"]
            if len(system) != 1 or "\n\n" + experience["disclosure_teacher"] not in system[0]:
                raise ValueError("teacher request differs from its frozen edition")
            witnessed.append({"id": ident, "receipt": path.name, "request_id": receipt["identity"],
                              "system_sha256": digest(system[0]), "response_sha256": digest(receipt["response"]),
                              "usage": receipt["response"].get("usage")})
    return {"status": "applied" if witnessed else "pending_teacher_request", "run_binding": binding,
            "version": experience["version"], "completed_teacher_requests": witnessed}
