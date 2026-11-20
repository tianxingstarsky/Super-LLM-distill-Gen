import copy
import json

import pytest

from lib.contexts.workflows.application.review_guided_disclosure import author_next_disclosure


@pytest.mark.parametrize("version,forecast,action,outcome", [
    ("4.10.9", {"action": "unknown", "outcome": "unknown"}, "report_local", "local_values_mismatch"),
    ("4.10.10", {"action": "unknown", "outcome": "unknown"}, "report_local", "local_values_mismatch"),
    ("4.10.10", None, "unknown", "unknown"),
    ("4.10.10", {"action": "report_local", "outcome": "local_values_verified"}, "report_local", "local_values_verified"),
])
def test_followup_tutor_reads_the_submission_and_the_disposition_of_the_forecast(version, forecast, action, outcome):
    premise = "Position 10 now contains zero."
    predicted_reason = "The learner will enumerate (0,0,0) and (1,1,0)."
    if forecast:
        forecast = {**forecast, "reason": predicted_reason}
    values = {"local_values_mismatch": {"position": 8, "former_bit": 0, "current_bit": 1},
              "local_values_verified": {"position": 10, "former_bit": 1, "current_bit": 0}}
    submission = (json.dumps({"action": "report", "values": values[outcome]}) if outcome in values
                  else "I am still working through this.\n" * 55)
    agreement = "matched" if outcome == "local_values_verified" else "unknown"
    prediction = dict(protocol_version=version, disclosure_ordinal=1, forecast=forecast,
                      response={"content": json.dumps(forecast), "reasoning_content": "private prediction notes"},
                      student_guidance=None, guidance_delivery={"status": "no_validated_selection"},
                      observation=dict(action=action, outcome=outcome, action_agreement=agreement, outcome_agreement=agreement))
    coach_phrase = "A separate reviewer's own diagnosis"
    attempt = dict(attempt=2, student={"content": submission, "finish_reason": "stop"}, check={},
                   coach={"evidence": coach_phrase}, behavior_prediction=copy.deepcopy(prediction))
    if outcome.startswith("local_values_"):
        attempt["local_step"] = {"status": "local_value_mismatch" if outcome.endswith("mismatch") else "verified_local_value",
                                 "independent_derivation_checked": False}
    received = {}
    teacher_reason = "The learner has already listed both triples."

    def request(role, index, messages, schema):
        system, packet = messages
        received[role] = (system["content"], json.loads(packet["content"]))
        assert index == 2
        if role == "disclosure_draft":
            proposal = dict(decision="offer", question_quote=premise, reason=teacher_reason,
                            bridge="Look at the changed condition.", hint="Trace this fixed bit through the neighbouring pair.")
        elif role == "disclosure":
            proposal = dict(decision="withhold", citations=[], reason="Show how the changed condition applies.",
                            pairing=dict(evidence_support=True, student_fit=False, history_fit="continuous"))
        else:
            proposal = dict(decision="insufficient", reason="Missing student work.", bridge="", hint="", question_quote="")
        return dict(content=json.dumps(proposal), finish_reason="stop")

    source = {"question": "Count the strings. " + premise, "gold": {"kind": "number", "value": "45"}}
    published = [{"ordinal": 1, "text": "Check the three adjacent positions."}]
    reference = {"kind": "idea", "text": "", "replaces_from": "final_answer"}
    author_next_disclosure(request, source, attempt, [], {}, published, reference, "",
                           grounded=True, paired=True, contextual_operations=True, immutable_review=True)
    assert list(received) == ["disclosure_draft", "disclosure", "disclosure_rewrite"]
    assert attempt["disclosure_work"][0]["draft"]["reason"] == teacher_reason
    assert attempt["behavior_prediction"] == prediction
    for system, packet in received.values():
        observed = packet["current_difficulty"]
        if version == "4.10.9":
            assert "prior_behavior_forecast" not in packet
            assert observed["behavior_prediction_feedback"]["predictor_interpretation"]["text"] == predicted_reason
            assert observed["observed_text_end"] == coach_phrase
            continue
        assert all(text not in json.dumps(packet) for text in (predicted_reason, coach_phrase, teacher_reason, "private prediction notes"))
        reported = observed["learner_submission"]
        assert reported["author"] == "student" and reported["source_field"] == "student.content"
        assert reported["length_characters"] == len(submission)
        excerpts = reported["excerpts"]
        assert excerpts[0]["start"] == 0 and excerpts[-1]["end"] == len(submission)
        assert all(part["text"] == submission[part["start"]:part["end"]] for part in excerpts)
        assert observed["program_observation"] == {"action": action, "outcome": outcome}
        assert packet["prior_behavior_forecast"]["program_comparison"] == {
            "action_agreement": agreement, "outcome_agreement": agreement}
        assert packet["prior_behavior_forecast"]["forecast"] == (
            {key: forecast[key] for key in ("action", "outcome")} if forecast else None)
        assert "learner_submission" in system and "unknown" in system
        assert all(item["kind"] == "question" for item in packet["established_evidence"])
        if "local_step" in observed:
            assert observed["local_step"]["independent_derivation_checked"] is False
    if version == "4.10.10":
        repair = received["disclosure_rewrite"][1]["requested_revision"]
        assert repair["reviewer_feedback"]["author"] == "disclosure_reviewer"
        assert "reason" not in repair["previous_teacher_proposal"]
