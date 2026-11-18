"""Learn a reusable answering strategy from one observed guided attempt."""


def observed_attempt(case, number):
    if number < 1 or number > len(case["attempts"]):
        raise ValueError("Select a recorded attempt, numbered from one")
    attempt = case["attempts"][number - 1]
    check = attempt["check"]
    coach = attempt.get("coach") or {}
    match = check.get("answer_matches")
    if case.get("gold") is None or not isinstance(match, bool):
        raise ValueError("This attempt has no comparable reference answer")
    if match and (not check.get("format_ok") or coach.get("verdict") != "supported"):
        raise ValueError("Matching the key alone does not establish a successful explanation")
    if "student_request_messages" in attempt:
        # Independent continuations saw only their current program snapshot.
        messages = [dict(m) for m in attempt["student_request_messages"] if m["role"] != "system"]
        messages.append({"role": "assistant", "content": attempt["student"]["content"],
                         "reasoning_content": attempt["student"].get("reasoning_content") or ""})
    else:
        # Original continuous-dialogue records did replay the earlier replies.
        messages = []
        replies = 0
        for entry in case["public_dialogue"]:
            messages.append(dict(entry))
            if entry["role"] == "assistant":
                replies += 1
                if replies == number:
                    break
        if replies != number:
            raise ValueError("The public dialogue does not contain the selected attempt")
    reason = ("The program matched the reference answer; the tutor accepted the explanation."
              if match else "The program found that the final answer differs from the reference.")
    if coach.get("diagnosis"):
        reason += " Tutor observation: " + coach["diagnosis"]
    return {"id": f"{case['id']}-attempt-{number}", "conversation": {"messages": messages},
            "guided_answer_projection_version": 1,
            "review_observation": {"verdict": {"keep": match, "reason": reason}}}


def learn_guided_experience(bank, case, attempt, query, parent_id=None):
    observation = observed_attempt(case, attempt)
    learned = bank.learn("reasoning", observation, parent_id=parent_id)
    identifier = parent_id if learned.get("status") == "no_update" else learned["id"]
    receipt = {"source_case": case["id"], "attempt": attempt, "parent": parent_id,
               "learning_status": learned["status"], "source_binding": case["binding"],
               "model_calls": learned["model_request_receipt"]}
    if not identifier:
        return {"version": "initial", "student": "", "receipt": receipt}
    indexed = bank.bank.index_edition(identifier)
    if indexed["status"] != "indexed":
        raise ValueError(f"Could not prepare the learned rules for retrieval: {indexed['status']}")
    selected = bank.bank.retrieve("reasoning", query, edition_ids=[identifier])
    if selected["status"] != "retrieved" or not selected["matches"]:
        raise ValueError(f"No guidance retrieved for this task: {selected['status']}")
    rules = []
    for match in selected["matches"]:
        entry = match["entry"]
        rules.append("\n".join(filter(None, [entry["when"], entry["principle"], entry["avoid"]])))
    return {"version": identifier, "student": "\n\n".join(rules),
            "receipt": {**receipt, "selection": selected["receipt"]}}
