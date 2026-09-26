from lib.domain.open_task_plan import MAX_TASK_CHARS, task_identity, task_plan_issue


def test_normalized_duplicates_are_rejected_within_and_across_batches():
    assert task_plan_issue(["Describe café", " Describe cafe\u0301 "], 2, set()) == "duplicate_normalized_task"
    assert task_plan_issue(["Explain\n power  isolation"], 1, {task_identity("Explain power isolation")}) == "duplicate_normalized_task"
    assert task_plan_issue(["Explain X", "Explain x"], 2, set()) is None


def test_count_type_and_length_are_bounded():
    assert task_plan_issue([None], 1, set()) == "empty_text"
    assert task_plan_issue([""], 1, set()) == "empty_text"
    assert task_plan_issue(["A" * (MAX_TASK_CHARS + 1)], 1, set()) == "task_too_long"
    assert task_plan_issue(["A" * MAX_TASK_CHARS], 1, set()) is None
    assert task_plan_issue({"task": "A"}, 1, set()) == "wrong_task_count"
