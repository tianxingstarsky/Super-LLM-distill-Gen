import json

import pytest
from jsonschema import Draft202012Validator

from lib.contexts.workflows.application.generate_guided_answer import GenerateGuidedAnswer
from lib.contexts.workflows.domain.experience_evolution import digest
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_behavior_prediction import (
    observe_prediction, place_student_guidance, prediction_due, prediction_feedback, prediction_guidance, prediction_messages, prediction_schema,
)
from lib.contexts.workflows.domain.guided_local_steps import (
    LOCAL_SUBMISSION_SCHEMA, check_local_step, dispatched_local_guidance, local_step_target, local_submission_schema,
    scoped_local_messages,
)
from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
from lib.contexts.workflows.domain.guided_training_targets import guided_training_target
from lib.contexts.workflows.infrastructure.guided_answer_records import export, location, run_guided_cases
from lib.contexts.workflows.infrastructure.guided_teacher_feedback import TeacherFeedbackStore


def signed_source():
    fixed = "The bit at position 2, formerly fixed to 1, is changed to 0"
    pair = "The bits at positions 3 and 4 must be different"
    question = f"Consider a four-bit string. {fixed}. {pair}. How many strings qualify? Give the integer only."
    row = {"id": "four-bits", "question": question, "gold": {"kind": "number", "value": "4"},
           "answer_requirement": "final_only"}
    steps = []
    for ident, quote, op in [
            ("fixed-position", fixed, "Mark the changed position and keep its old value separate from the value now in force."),
            ("permitted-pair", pair, "Draw two slots for the linked positions. List only pairs satisfying the stated equality or inequality; leave the other positions blank.")]:
        start = question.index(quote)
        steps.append(dict(id=ident, kind="operation", quote=quote, span=[start, start+len(quote)],
                          operation=op, verification="Checked against the stated constraint."))
    receipt = verified_steps_digest(row["id"], question, row["gold"], steps)
    row["verified_teaching_steps"] = {"receipt_id": receipt, "steps": steps}
    return validate_source(row, allow_verified_teaching_steps=True, trusted_step_receipts={receipt}), receipt


def test_public_source_choices_and_unknown_outcome_preserve_legacy_forecast_semantics():
    visible = {"original_question":"Find the total.", "ordered_published_disclosures":[
        {"ordinal":1, "text":"List the allowed combinations before counting."}],
        "confirmed_student_work":[], "current_local_task":"Report the position and its old and new settings."}
    def response(action, source, *, legacy=False):
        return {"model":"observer", "finish_reason":"stop", "content":json.dumps({"action":action,"outcome":"unknown",
                "source":source, **({"direction":"apply_latest_disclosure"} if legacy else {}),
                "reason":"The learner might list combinations next."})}
    v7 = prediction_schema(visible, "4.10.7")
    v8 = prediction_schema(visible, "4.10.8")
    assert v7["properties"]["source"] == {"type":"string"}
    assert v8["properties"]["source"]["enum"] == ["disclosure.1","local_task",""]
    assert set(v8["properties"]["action"]["enum"]) == {"report_local","report_obstacle","unknown"}
    assert json.loads(prediction_messages(visible)[1]["content"]) == visible
    choices = json.loads(prediction_messages(visible, "4.10.8")[1]["content"])["available_guidance_sources"]
    assert choices == {"disclosure.1":"apply_latest_disclosure","local_task":"check_submission_fields","":"none"}
    # Shape of the real first forecast: it predicted the whole answer during a typed local report.
    old = response("submit_answer", "<ordinal_1>", legacy=True)
    assert prediction_guidance(old, visible, chinese=False) == (None, None)
    assert not Draft202012Validator(v8).is_valid(json.loads(old["content"]))
    feedback = prediction_feedback({"forecast":None, "response":response("submit_answer", "<ordinal_1>"), "protocol_version":"4.10.8",
        "student_guidance":None, "observation":{}, "disclosure_ordinal":1})
    assert feedback["forecast"] is None and feedback["predictor_interpretation"]["status"] == "unverified_interpretation"
    eligible = response("report_local", "disclosure.1")
    assert prediction_guidance(response("report_local","disclosure.1",legacy=True), visible, chinese=False)[1] is None
    assert "direction" not in v8["properties"] and "direction" not in v8["required"]
    forecast, sent = prediction_guidance(eligible, visible, chinese=False, protocol_version="4.10.8")
    assert forecast["outcome"] == "unknown" and json.loads(sent["content"])["quoted_visible_evidence"] == visible["ordered_published_disclosures"][0]["text"]
    assert prediction_guidance(response("report_local","<ordinal_1>"), visible, chinese=False, protocol_version="4.10.8")[1] is None
    public = {**visible, "current_local_task":None, "confirmed_student_work":[{"id":"finding.1","text":"A checked intermediate."}]}
    schema = prediction_schema(public, "4.10.8")
    assert schema["properties"]["source"]["enum"] == ["disclosure.1","finding.1",""]
    free = response("work_on_disclosure","disclosure.1")
    assert Draft202012Validator(schema).is_valid(json.loads(free["content"]))
    forecast, guide = prediction_guidance(free, public, chinese=False, protocol_version="4.10.8")
    assert guide and forecast["action"] == "work_on_disclosure"
    for content, actual in [("I will try enumerating the combinations.", "unknown"),
                            ("I counted the four allowed arrangements.\nFinal answer: 4", "submit_answer")]:
        observed = observe_prediction(forecast, {"student":{"content":content,"finish_reason":"stop","model":"learner"}},
            expected_model="learner", question=public["original_question"], gold={"kind":"number","value":"4"})
        assert observed["action"] == actual
        assert observed["action_agreement"] == observed["outcome_agreement"] == "unknown"


def test_selected_task_must_occupy_the_native_submission_field_to_omit_a_message():
    source, receipt = signed_source()
    hint = {"source_step_receipt": receipt, "source_step_id": "fixed-position", "text": "Read the changed slot."}
    target = local_step_target(source, hint, {receipt}, chinese=False, scoped=True, fixed_fields=True)
    visible = {"ordered_published_disclosures":[{"ordinal":1,"text":hint["text"]}], "current_local_task":target["request"],
               "confirmed_student_work":[{"id":"finding.1","text":target["request"]}]}
    response = {"model":"observer","finish_reason":"stop","content":json.dumps(
        {"action":"unknown","outcome":"unknown","reason":"The learner might inspect the changed field.","source":"local_task"})}
    _, guidance = prediction_guidance(response, visible, chinese=False, protocol_version="4.10.9")
    messages = scoped_local_messages(source, target, target["request"], False)
    schema = local_submission_schema(target)
    appended, placement = place_student_guidance(guidance, messages, target, schema)
    assert appended is None and placement["status"] == "already_in_current_request"
    system, packet = placement["positions"]
    assert messages[system["message_index"]]["content"][slice(*system["content_span"])] == target["request"]
    assert json.loads(messages[packet["message_index"]]["content"])[packet["json_pointer"][1:]] == target["request"]
    wrong = json.loads(messages[1]["content"])
    wrong["current_local_task"] = "Different task"
    for altered, form in [(messages, None), ([messages[0], {"role":"user","content": json.dumps(wrong)}], schema)]:
        appended, placement = place_student_guidance(guidance, altered, target, form)
        assert appended == guidance and placement["status"] == "appended"
    selection = {**json.loads(response["content"]), "source":"finding.1"}
    _, highlight = prediction_guidance({**response,"content":json.dumps(selection)}, visible, chinese=False, protocol_version="4.10.9")
    assert place_student_guidance(highlight, messages, target, schema)[0] == highlight


@pytest.mark.parametrize("version,first_step,status", [
    ("4.10.3", 'I read the changed slot in the question.\n["2","1","0"]', "verified_local_value"),
    ("4.10.3", '["2","0","1"]', "local_value_mismatch"),
    ("4.10.3", '{"blocked":"Which bit remains fixed?"}', "blocked"),
    ("4.10.3", 'Final answer: 4', "unverified"),
    ("4.10.5", '{"action":"report","values":["2","1","0"],"explanation":"This explanation was not independently checked."}', "verified_local_value"),
    ("4.10.5", '{"action":"blocked","difficulty":"I cannot tell which position changed."}', "blocked"),
    ("4.10.6", '{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"I claim the full count is one less."}', "verified_local_value"),
    ("4.10.7", '{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"I kept the slot settings separate."}', "verified_local_value"),
    ("4.10.8", '{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"I kept the slot settings separate."}', "verified_local_value"),
    ("4.10.9", '{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"I kept the slot settings separate."}', "verified_local_value"),
    ("4.10.10", '{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"I kept the slot settings separate."}', "verified_local_value"),
])
def test_local_reports_and_obstacles_flow_to_the_next_tutor_and_whole_answer(tmp_path, version, first_step, status):
    source, receipt = signed_source()
    steps = source["verified_teaching_steps"]["steps"]
    inputs = {}
    hints = ["Separate the replacement slot from its former setting.", "Try each value of the first linked slot."]
    placement = "already_in_public_reference" if version == "4.10.10" else "already_in_current_request"
    if version == "4.10.10":
        hints[0] = "You have finished counting the alternatives. List all the arrangements next."
    solved = "The first bit has two choices and the final pair has two assignments.\nFinal answer: 4"

    def request(role, index, messages, schema):
        inputs[role, index] = messages
        if role == "student":
            pair_report = ('{"action":"report","values":["10","01"],"explanation":"I tried both orders."}'
                           if version in {"4.10.5", "4.10.6", "4.10.7", "4.10.8", "4.10.9", "4.10.10"} else '["10","01"]')
            reply = {1: "8", 2: first_step, 3: pair_report, 4: solved}[index]
            if version in {"4.10.5", "4.10.6", "4.10.7", "4.10.8", "4.10.9", "4.10.10"} and index in (2, 3):
                if version in {"4.10.6", "4.10.7", "4.10.8", "4.10.9", "4.10.10"} and index == 2:
                    form = schema["anyOf"][0]["properties"]["values"]
                    assert form["required"] == ["position", "former_bit", "current_bit"]
                    assert Draft202012Validator(schema).is_valid(json.loads(reply))
                else:
                    assert schema == LOCAL_SUBMISSION_SCHEMA
                applied = (version in {"4.10.7", "4.10.8"} and index == 2) or (version in {"4.10.9", "4.10.10"} and index == 3)
                assert [m["role"] for m in messages] == (["system", "user", "user"] if applied else ["system", "user"])
                packet = json.loads(messages[1]["content"])
                if version == "4.10.10":
                    ref = packet["public_reference"]
                    published = ref["quoted_teacher_disclosures"]
                    assert published == json.loads(inputs["behavior_prediction", index][-1]["content"])["ordered_published_disclosures"]
                    assert [item["ordinal"] for item in published] == list(range(1, index))
                    assert all(item["text"].endswith(hint) for item, hint in zip(published,hints))
                    assert "published_guidance_and_established_work" not in packet
                    assert hints[0] not in messages[0]["content"]
                    assert bool(ref["confirmed_student_work"]) is (index == 3)
                    assert "Submit only the current_local_task" in messages[0]["content"]
                assert packet["origin"] == "tutoring_program"
                assert packet["quoted_source_material"] == {"original_user_question": source["question"]}
                assert packet["current_local_task"] in messages[0]["content"]
            else:
                assert schema is None
        elif role == "behavior_prediction":
            reply = dict(action="report_local", outcome="unknown" if version in {"4.10.8", "4.10.9", "4.10.10"} else "local_values_verified", reason="PRIVATE_FORECAST_SENTINEL",
                         direction="apply_latest_disclosure", source="disclosure.1") if index == 2 else dict(
                             action="unknown", outcome="unknown", reason="PRIVATE_FORECAST_SENTINEL", direction="none", source="")
            if version in {"4.10.8", "4.10.9", "4.10.10"}:
                del reply["direction"]
                if version in {"4.10.9", "4.10.10"}:
                    reply["source"] = "local_task" if index == 2 else "finding.1"
                    if version == "4.10.10" and index == 2:
                        reply["source"] = "disclosure.1"
                assert Draft202012Validator(schema).is_valid(reply)
        elif role == "disclosure_draft":
            step = steps[index-1]
            if index == 2:
                packet = json.loads(messages[-1]["content"])
                assert packet["current_difficulty"]["local_step"]["status"] == status
                if version in {"4.10.9", "4.10.10"}:
                    feedback = packet["current_difficulty"]["behavior_prediction_feedback"]
                    assert feedback["guidance_delivery"]["status"] == placement
                    assert feedback["student_guidance"] is None
            reply = dict(decision="apply:"+step["id"], question_quote=step["quote"], bridge=step["quote"]+".",
                         hint=hints[index-1], reason="Use the selected constraint.")
        elif role == "disclosure":
            step = steps[index-1]
            citations = [dict(evidence_id="question", quote=step["quote"])]
            if index > 1:
                citations.append(dict(evidence_id="disclosed.1", quote=hints[0]))
            packet = json.loads(messages[-1]["content"])["proposed_next_teacher_disclosure"]
            reply = dict(decision="release", bridge=packet["bridge"], citations=citations, reason="The hint applies.",
                         pairing=dict(evidence_support=True, student_fit=True, history_fit="first" if index == 1 else "continuous"))
            if version in {"4.10.5", "4.10.6", "4.10.7", "4.10.8", "4.10.9", "4.10.10"}:
                del reply["bridge"]
        elif role == "coach":
            assert index == 4
            reply = dict(decision="supported", diagnosis="Both free choices were counted.", reusable=[], independent_approaches=[])
        else:
            assert role == "final_answer" and index == 4
            reply = "4"
        return dict(model="learner" if role in {"student","final_answer"} else "reviewer",
                    content=reply if isinstance(reply, str) else json.dumps(reply), finish_reason="stop",
                    **({"usage":{"prompt_tokens":19, "completion_tokens":5}} if role == "behavior_prediction" else {}))

    calls = iter([("student",1), ("disclosure_draft",1), ("disclosure",1),
                  *([("behavior_prediction",2)] if version in {"4.10.7", "4.10.8", "4.10.9", "4.10.10"} else []), ("student",2),
                  ("disclosure_draft",2), ("disclosure",2),
                  *([("behavior_prediction",3)] if version in {"4.10.7", "4.10.8", "4.10.9", "4.10.10"} else []), ("student",3), ("student",4), ("coach",4), ("final_answer",4)])

    class Endpoint:
        def __init__(self, name):
            self.name = name

        def validate(self):
            return {"model":self.name, "url":f"http://localhost/{self.name}", "slots":1, "enable_thinking":False}

        def payload(self, messages, schema):
            payload = {"messages":messages, "schema":schema, "model":self.name}
            if schema:
                payload["response_format"] = {"type":"json_schema", "json_schema":{"strict":True, "schema":schema}}
            return payload

        def final_answer_payload(self, messages):
            return self.payload(messages, None)

        def complete(self, payload):
            role, index = next(calls)
            assert self.name == ("learner" if role in {"student","final_answer"} else "reviewer")
            return request(role, index, payload["messages"], payload["schema"])

    input_path, registry, run = tmp_path/"source.jsonl", tmp_path/"registry.json", tmp_path/"run"
    input_path.write_text(json.dumps(source), encoding="utf-8")
    registry.write_text(json.dumps({"schema":"guided-teaching-verification/v1", "receipts":[receipt]}), encoding="utf-8")
    run_guided_cases(input_path, run, {"student":Endpoint("learner"), "coach":Endpoint("reviewer")},
                     {"version":"test"}, 3, protocol_version=version, until_correct=True, verified_step_receipts=registry)
    case_path = location(run,source["id"])/"case.json"
    case = json.loads(case_path.read_bytes())
    assert case["status"] == "accepted" and case["verification"]["answer_key_check"]
    assert [a["local_step"]["status"] for a in case["attempts"][1:3]] == [status,"verified_local_value"]
    assert case["attempts"][1]["student"]["content"] == first_step
    for n in (2,3):
        attempt = case["attempts"][n-1]
        assert attempt["check"]["answer_matches"] is None and not attempt["verified_stage"]["complete_student_solution"]
        assert not attempt["local_step"]["independent_derivation_checked"]
        assert "Final answer:" not in inputs["student",n][0]["content"]
    assert "Final answer:" in inputs["student",4][0]["content"]
    fields = case["shared_student_findings"]
    if version == "4.10.10":
        returned = inputs["student",4]
        assert returned[-1] == {"role": "user", "content": source["question"]}
        assert returned[-2]["role"] == "user"
        memory = json.loads(returned[-2]["content"])
        assert memory["source"] == "tutoring_program"
        assert memory["confirmed_student_findings"] == [field["text"] for field in fields]
        assert '["10","01"]' in memory["confirmed_student_findings"]
        assert memory["teacher_disclosures"] == [hint["text"] for hint in case["disclosures"]]
        assert set(memory["teacher_disclosures"]).isdisjoint(memory["confirmed_student_findings"])
    else:
        assert '["10","01"]' in inputs["student",4][-1]["content"]
    assert len(fields) == 1 + (status == "verified_local_value")
    for item in fields:
        raw = case["attempts"][item["source_attempt"]-1]["student"]["content"]
        assert raw[slice(*item["source_span"])] == item["text"]
        assert "explanation" not in item["text"]
    assert "continuation_provenance" not in case
    store = TeacherFeedbackStore(tmp_path/"feedback")
    observed = store.observe(run, input_path)
    assert observed["by_category"] == {"program_framed_local_response": 2}, observed
    assert [e["outcome"] for e in observed["events"]] == [status,"verified_local_value"]
    for event in observed["events"]:
        local = event["evidence"]["local_operation"]
        assert event["author"] == "teacher_authored" and local["task_author"] == "program"
        assert not event["eligible"] and not event["transfer_gain_supported"]
        assert bool(local["verified_submissions"]) is (local["status"] == "verified_local_value")
    if version in {"4.10.7", "4.10.8", "4.10.9", "4.10.10"}:
        summary = export(input_path, run)
        assert summary["behavior_predictions"] == {"observed_student_turns":2, "guidance_applied":1,
            **({"by_guidance_delivery":{placement:1,"appended":1}} if version in {"4.10.9", "4.10.10"} else {}),
            "action_agreement":{"matched":1,"unknown":1},
            "outcome_agreement":({"unknown":2} if version in {"4.10.8", "4.10.9", "4.10.10"} else {"matched":1,"unknown":1})}
        assert summary["usage_by_role"]["new"]["behavior_prediction"] == {
            "responses":2,"usage_unreported":0,"prompt_tokens":38,"completion_tokens":10}
        assert [event["evidence"]["behavior_prediction"]["guidance_applied"] for event in observed["events"]] == (
            [False, True] if version in {"4.10.9", "4.10.10"} else [True, False])
        assert "PRIVATE_FORECAST_SENTINEL" not in (run/"training-targets.jsonl").read_text(encoding="utf8")
        predict_path = case_path.parent/"002-behavior_prediction.json"
        assert json.loads(predict_path.read_bytes())["role"] == "behavior_prediction"
        # Completed checkpoints also survive a normal resume without drawing another response.
        assert run_guided_cases(input_path, run, {"student":Endpoint("learner"), "coach":Endpoint("reviewer")},
            {"version":"test"}, 3, verified_step_receipts=registry)["behavior_predictions"] == summary["behavior_predictions"]
        archived = json.loads(case_path.read_bytes())
        if version == "4.10.10":
            student_path = case_path.parent/"002-student.json"
            saved = student_path.read_bytes()
            receipt_row = json.loads(saved)
            packet = json.loads(receipt_row["request"]["messages"][1]["content"])
            packet["public_reference"]["confirmed_student_work"] = [{"id":"made-up", "text":hints[0]}]
            receipt_row["request"]["messages"][1]["content"] = json.dumps(packet,ensure_ascii=False)
            receipt_row["identity"] = digest({"binding": case["binding"], "id": case["id"], "payload": receipt_row["request"]})
            case["attempts"][1]["student_request_messages"] = receipt_row["request"]["messages"]
            student_path.write_text(json.dumps(receipt_row), encoding="utf8")
            case_path.write_text(json.dumps(case), encoding="utf8")
            with pytest.raises(ValueError, match="scoped local request"):
                export(input_path, run)
            student_path.write_bytes(saved)
            case = json.loads(json.dumps(archived))
        if version in {"4.10.9", "4.10.10"}:
            case["attempts"][1]["behavior_prediction"]["guidance_delivery"]["positions"][-1]["message_index"] = 2
        else:
            case["attempts"][1]["behavior_prediction"]["student_guidance"] = None
        case_path.write_text(json.dumps(case), encoding="utf8")
        with pytest.raises(ValueError, match="behavior (prediction|guidance)"):
            export(input_path, run)
        case = archived
        case_path.write_text(json.dumps(case), encoding="utf8")
    if status == "verified_local_value":
        case["attempts"][1]["local_step"]["status"] = "blocked"
        case_path.write_text(json.dumps(case), encoding="utf-8")
        assert store.observe(run, input_path)["events"][0]["category"] == "unverified_receipt"


def test_local_operation_needs_the_bound_source_and_a_matching_verifier():
    source, receipt = signed_source()
    hint = {"source_step_receipt":receipt, "source_step_id":"permitted-pair", "text":"List the pairs."}
    target = local_step_target(source, hint, {receipt}, chinese=False)
    for report in ('["00","11"]', '["01","10","10"]', '["01","10"]\nI am unsure.'):
        response = {"content":report, "finish_reason":"stop"}
        action, finding = check_local_step(target, response, 2)
        assert not finding and action["status"] != "verified_local_value"
    assert local_step_target(source, hint, set(), chinese=False) is None
    assert local_step_target({**source,"question":"another task"}, hint, {receipt}, chinese=False) is None
    assert local_step_target(source, {**hint,"source_step_id":"another-target"}, {receipt}, chinese=False) is None


def test_scoped_submissions_bind_only_reported_values_and_attributed_native_request():
    source, receipt = signed_source()
    hint = {"source_step_receipt":receipt, "source_step_id":"fixed-position", "text":"Mark the slot."}
    target = local_step_target(source, hint, {receipt}, chinese=False, scoped=True)
    messages = scoped_local_messages(source, target, "Published material", False)
    payload = {"messages":messages, "response_format":{"type":"json_schema",
               "json_schema":{"strict":True, "schema":LOCAL_SUBMISSION_SCHEMA}}}
    assert dispatched_local_guidance(payload, source, target, False) == "Published material"
    for altered in ({**payload, "response_format":{}}, {**payload, "messages":[messages[0], {"role":"tool","content":messages[1]["content"]}]},
                    {**payload, "messages":[messages[0], {"role":"user","content":messages[1]["content"].replace("tutoring_program", "original_user")}]}):
        with pytest.raises(ValueError):
            dispatched_local_guidance(altered, source, target, False)

    # The same values can occur in prose. Bind to the actual JSON array, including its whitespace.
    report = r'{"action":"report","explanation":"I considered [\"2\",\"1\",\"0\"]","val\u0075es": ["2", "1", "0"]}'
    def check(raw):
        return check_local_step(target, {"content":raw, "finish_reason":"stop"}, 2, scoped=True)
    action, facts = check(report)
    assert action["status"] == "verified_local_value" and not action["independent_derivation_checked"]
    finding = next(iter(facts.values()))
    assert report[slice(*finding["source_span"])] == finding["text"] == '["2", "1", "0"]'
    assert check(report.replace('["2", "1", "0"]', '["2","0","1"]'))[0]["status"] == "local_value_mismatch"
    for invalid in (report.replace('"action":"report"', '"action":"blocked","action":"report"'),
                    report.replace('["2", "1", "0"]', '{"blocked":"unrelated"}'),
                    report + " trailing content", '["2","1","0"]'):
        assert check(invalid)[0]["status"] == "unverified" and not check(invalid)[1]


def test_portfolio_substep_checks_the_selected_projects_hours_and_updated_cap():
    quote = "The hours limit falls from 20 to 16"
    question = ("The projects have (cost, hours, benefit) triples E=(4, 3, 19), G=(2, 8, 15). "
                "In E→G, choosing the left project requires choosing the right. " + quote + ".")
    op = ("As a trial, take the projects E and G from the previous dependency. Count each project's hours once "
          "and compare the sum with the new cap. This checks this resource alone.")
    gold = {"kind":"exact", "value":"EG"}

    def target_for(text, operation, *, named=False):
        start = text.index(quote)
        steps = [dict(id="updated-hours", kind="operation", operation=operation, quote=quote,
                      span=[start,start+len(quote)], verification="The named pair was checked against the cap.")]
        receipt = verified_steps_digest("trial", text, gold, steps)
        source = dict(id="trial", question=text, gold=gold, verified_teaching_steps={"receipt_id":receipt,"steps":steps})
        hint = {"text":operation, "source_step_id":"updated-hours", "source_step_receipt":receipt}
        return local_step_target(source, hint, {receipt}, chinese=False, scoped=named, fixed_fields=named)

    target = target_for(question, op)
    for content, status in [('["11","16","<"]', "verified_local_value"), ('["6","16","<"]', "local_value_mismatch"),
                            ('["11","20","<"]', "local_value_mismatch"), ('["11","16",">"]', "local_value_mismatch")]:
        action, _ = check_local_step(target, {"content":content,"finish_reason":"stop"}, 2)
        assert action["status"] == status
    assert target_for(question.replace("(cost, hours, benefit)", "(hours, cost, benefit)"), op) is None
    assert target_for(question, op.replace("E and G", "E and H")) is None
    assert target_for(question, "Now optimize the whole portfolio.") is None
    named = target_for(question, op, named=True)
    schema = local_submission_schema(named)
    other = target_for(question.replace("G=(2, 8, 15)", "G=(2, 5, 15)"), op, named=True)
    assert other["expected"] != named["expected"] and local_submission_schema(other) == schema
    validator = Draft202012Validator(schema)
    submitted = {"action":"report", "values":{"new_limit":16, "comparison":"<", "total_hours":11},
                 "explanation":"I also claim the optimal portfolio is EG."}
    for vals, valid, outcome in (
            (submitted["values"], True, "verified_local_value"),
            ({**submitted["values"], "total_hours":11.0}, True, "verified_local_value"),
            ({**submitted["values"], "total_hours":12}, True, "local_value_mismatch"),
            ({**submitted["values"], "new_limit":True}, False, "unverified"),
            ({"new_limit":16, "comparison":"<"}, False, "unverified"),
            (["total is 11, limit is 16, below"], False, "unverified")):
        report = {**submitted, "values":vals}
        assert validator.is_valid(report) is valid
        action, facts = check_local_step(named, {"content":json.dumps(report), "finish_reason":"stop"}, 2, scoped=True)
        assert action["status"] == outcome and not action["independent_derivation_checked"]
        assert all("optimal" not in item["text"] for item in facts.values())
    duplicate = json.dumps(submitted).replace('"new_limit": 16', '"new_limit": 16, "new_limit": 16')
    assert check_local_step(named, {"content":duplicate,"finish_reason":"stop"}, 2, scoped=True)[0]["status"] == "unverified"


def test_predictor_observes_only_public_work_and_actual_learner_feedback_reaches_next_tutor():
    source, receipt = signed_source()
    source["source_reasoning"] = "PRIVATE_REFERENCE_SENTINEL"
    steps = source["verified_teaching_steps"]["steps"]
    hints = ["Separate the replacement slot from its former setting.", "Try each value of the first linked slot."]
    calls = []
    student_requests, predictions = {}, {}
    def request(role, index, messages, schema):
        calls.append((role, index))
        if role == "student":
            student_requests[index] = messages
            assert all("PRIVATE_FORECAST" not in m["content"] for m in messages)
            if index == 2:
                assert [m["role"] for m in messages] == ["system", "user", "user"]
                guide = json.loads(messages[-1]["content"])
                assert guide["origin"] == "behavior_predictor_guidance"
                assert guide["quoted_visible_evidence"] == json.loads(messages[1]["content"])["current_local_task"]
            if index == 3:
                assert [m["role"] for m in messages] == ["system", "user"]
            reply = {1:"8", 2:'{"action":"report","values":{"position":2,"former_bit":1,"current_bit":0},"explanation":"PRIVATE_LEARNER_DRAFT: I also guess seven."}',
                     3:"I am unsure.", 4:"I have two choices for the first bit and two pairs.\nFinal answer: 4"}[index]
        elif role == "behavior_prediction":
            visible = json.loads(messages[-1]["content"])
            predictions[index] = visible
            assert [m["role"] for m in messages] == ["system", "user"]
            assert set(visible) == {"original_question", "ordered_published_disclosures", "confirmed_student_work", "current_local_task"}
            assert visible["original_question"] == source["question"] and "PRIVATE" not in messages[-1]["content"]
            assert [item["ordinal"] for item in visible["ordered_published_disclosures"]] == list(range(1,index))
            assert hints[index-2] in visible["ordered_published_disclosures"][-1]["text"]
            if index == 2:
                assert visible["confirmed_student_work"] == []
                reply = dict(action="report_obstacle", outcome="obstacle_submitted", reason="PRIVATE_FORECAST",
                             direction="check_submission_fields", source="local_task")
            else:
                assert json.loads(visible["confirmed_student_work"][0]["text"])["current_bit"] == 0
                reply = dict(action="report_local", outcome="local_values_verified", reason="PRIVATE_FORECAST",
                             direction="continue_checked_work", source="nonexistent")
        elif role == "disclosure_draft":
            if index == 2:
                observation = json.loads(messages[-1]["content"])["current_difficulty"]["behavior_prediction_feedback"]
                assert observation["forecast"]["action"] == "report_obstacle"
                assert observation["predictor_interpretation"] == {"author":"behavior_predictor",
                        "status":"unverified_interpretation", "text":"PRIVATE_FORECAST"}
                assert observation["agreement_scope"] == "observable_action_and_outcome_categories_only"
                assert observation["observed_student_submission"] == dict(action="report_local", outcome="local_values_verified",
                        action_agreement="mismatched", outcome_agreement="mismatched")
                assert observation["student_guidance"] == student_requests[2][-1]
            step = steps[index-1]
            reply = dict(decision="apply:"+step["id"], question_quote=step["quote"], bridge=step["quote"]+".",
                         hint=hints[index-1], reason="PRIVATE_TUTOR_NOTE")
        elif role == "disclosure":
            citations=[dict(evidence_id="question", quote=steps[index-1]["quote"])]
            if index == 2:
                citations.append(dict(evidence_id="disclosed.1", quote=hints[0]))
            reply=dict(decision="release", citations=citations, reason="PRIVATE_TUTOR_NOTE",
                       pairing=dict(evidence_support=True, student_fit=True, history_fit="first" if index==1 else "continuous"))
        elif role == "coach":
            assert index == 4
            reply=dict(decision="supported", diagnosis="The choices were counted.", reusable=[], independent_approaches=[])
        else:
            assert (role,index) == ("final_answer",4)
            reply="4"
        model = "student" if role in {"student","final_answer"} else "observer" if role=="behavior_prediction" else "tutor"
        return {"model":model,"finish_reason":"stop","content":reply if isinstance(reply,str) else json.dumps(reply),
                **({"reasoning_content":"PRIVATE_FORECAST_TRACE"} if role=="behavior_prediction" else {})}

    case = GenerateGuidedAnswer(request).run(source, {"version":"test"}, 3, protocol_version="4.10.7",
        until_correct=True, trusted_solution_receipts={receipt}, expected_first_model="student")
    assert case["status"] == "accepted" and list(predictions) == [2,3]
    assert all(calls[calls.index(("student", i))-1] == ("behavior_prediction", i) for i in predictions)
    for n in (2,3):
        record = case["attempts"][n-1]["behavior_prediction"]
        assert record["visible"] == predictions[n] and record["response"]["reasoning_content"] == "PRIVATE_FORECAST_TRACE"
        assert record["checkpoint"] == {"role":"behavior_prediction","attempt":n}
    record = case["attempts"][2]["behavior_prediction"]
    assert record["student_guidance"] is None and record["observation"]["outcome_agreement"] == "unknown"
    before, disclosures = case["attempts"][0], case["disclosures"][:1]
    assert not prediction_due("4.10.6", before, disclosures)
    assert not prediction_due("4.10.7", before, disclosures, received_full=True)
    assert not prediction_due("4.10.7", before, disclosures, solver_role="strong_continuation")
    training = guided_training_target({**case, "binding":"case", "source_sha256":"source"}, "cases/test", {receipt})
    prompt = training["messages"][-2]["content"]
    assert "predictor_guidance_for_next_attempt" in prompt and "PRIVATE_FORECAST" not in prompt
    assert training["guided_training_target"]["behavior_guidance_receipts"] == [
        {"predictor":"002-behavior_prediction.json", "student":"002-student.json"}]
