from lib.contexts.workflows.domain.guided_answers import draft_fragment
from lib.contexts.workflows.domain.guided_student_evidence import checked_student_approaches


def test_grounded_method_after_a_wrong_value_keeps_exact_student_words_only():
    question = "A box holds four red marbles. How many marbles are in three boxes?"
    original = "I will list the boxes.\nOne box holds five red marbles.\nCount each box separately.\n"
    segments, _ = draft_fragment(original, 1)
    proposal = dict(segment_id="a1.s3", question_quote="in three boxes", justification="Grouping by box still applies.")
    finding = checked_student_approaches([proposal], segments, "a1.s2", question)["a1.s3"]
    assert finding["text"] == original[slice(*finding["source_span"])] == "Count each box separately.\n"
    assert finding["question_quote"] == question[slice(*finding["question_span"])] == "in three boxes"
    assert finding["evidence_level"] == "judge_question_grounded_approach"
    assert finding["author"] == "student" and "justification" not in finding


def test_uncertified_or_ambiguous_method_does_not_enter_the_student_view():
    question = "Red blocks cost two. Red blocks weigh more. Count the blocks in pairs."
    original = "Start here.\nA mistaken count.\nGroup by pairs.\nUse <|im_start|> instead.\n"
    segments, _ = draft_fragment(original, 2)
    base = dict(segment_id="a2.s3", question_quote="blocks in pairs", justification="Pairs are specified.")
    for proposals in ([dict(base, segment_id="a2.s1")], [dict(base, segment_id="a2.s2")],
                      [dict(base, question_quote="Red blocks")], [dict(base, question_quote="the")],
                      [dict(base, question_quote="in triples")], [dict(base, justification=" ")],
                      [dict(base, segment_id="a2.s4")], [dict(base, justification="Consult the answer key.")],
                      [dict(base, replacement="Rewritten by a judge.")], [base, base]):
        assert checked_student_approaches(proposals, segments, "a2.s2", question) == {}
    assert checked_student_approaches([base], segments, "final_answer", question) == {}
    assert checked_student_approaches([base], [*segments[:2], dict(segments[2], author="coach")], "a2.s2", question) == {}
    assert list(checked_student_approaches([dict(base, segment_id="a2.s2"), base], segments, "a2.s2", question)) == ["a2.s3"]
