from lib.contexts.workflows.domain.guided_answers import draft_fragment
from lib.contexts.workflows.domain.guided_student_calculations import recovered_student_calculations


def test_recover_only_the_students_exact_intermediate_equation_and_source_position():
    examples = (
        (r"*   Total games attended: $60 \times 4 = 240$", "<<4*60=240>>", "16", "60 * 4 = 240"),
        (r"Total recording time = $10 \text{ (songs)} \times 12 \text{ mins/song} = 120$ minutes.",
         "<<12*10=120>>", "15", "10 * 12 = 120"),
        (r"Paint Green = $(3/5) \times 10 = 6$. Purple must cover 90 walls.",
         "<<3/5*10=6>>", "32", "(3/5) * 10 = 6"),
    )
    for student_text, annotation, gold, statement in examples:
        draft = "A mistaken first attempt.\n" + student_text
        source = {"question": "Find the total.", "gold": {"kind": "number", "value": gold},
                  "source_reasoning": "Independent calculation " + annotation}
        pieces, _ = draft_fragment(draft, 1)
        finding, = recovered_student_calculations(source, pieces).values()
        assert finding["student_quote"] == draft[slice(*finding["source_span"])]
        assert finding["student_quote"] in student_text
        assert source["source_reasoning"][slice(*finding["source_calculation_span"])] == annotation
        assert finding["source_calculation_quote"] == annotation
        assert finding["author"] == "student" and finding["faithfulness_checked"]
        assert finding["text"] == statement


def test_uncertain_wrong_copied_or_final_work_cannot_authenticate_an_interior_expression():
    source = {"question": "Given 20 + 3 = 23, find the total.",
              "source_reasoning": "<<4*60=240>> <<8*4=32>> <<20+3=23>> <<4+8=13>> <<20-5=15>>",
              "gold": {"kind": "number", "value": "32"}}
    rejected = (
        "I got 240.", "Guess: 60 * 4 = 240", "60 * 4 = 240 is wrong.", "Suppose instead:\n60 * 4 = 240",
        "60 * 4 = 2400", "210 + 30 = 240", "6 * 40 = 240", "8 * 4 = 32", "3 + 20 = 23",
        "4 + 8 = 13", "5 - 20 = 15", "x^ 60 * 4 = 240", "60 * 4 = 240 %", "z*60 * 4 = 240",
        "60 * 4 = 240 + x", "60 * 4 = 240.5", "60 * 4 = 240e2", r"6\text{games}0 * 4 = 240",
    )
    for text in rejected:
        pieces, _ = draft_fragment(text, 1)
        assert not recovered_student_calculations(source, pieces), text
    pieces, _ = draft_fragment("4 * 60 = 240\n60 * 4 = 240", 1)
    assert list(recovered_student_calculations(source, pieces)) == ["a1.s1"]
    assert not recovered_student_calculations(source, pieces, prior_hints=iter([r"Start from $60 \times 4 = 240$."]))
    assert recovered_student_calculations(source, pieces, prior_hints=["Compute the games in four groups."])
    assert not recovered_student_calculations({**source, "gold": None}, pieces)
    assert not recovered_student_calculations(source, [dict(piece, author="teacher") for piece in pieces])
