import json
from pathlib import Path

import pytest

from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_calculation_plan import (
    calculation_for_step, calculation_plan_digest, next_calculation_step, public_calculation_step,
)


CONFIG = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture
def ribbon():
    return json.loads((CONFIG / "guided-calculation-plan.example.jsonl").read_text(encoding="utf-8"))


def validate(row):
    plan = row["verified_calculation_plan"]
    receipt = calculation_plan_digest(row, plan["steps"])
    plan["receipt_id"] = receipt
    return validate_source(row, allow_calculation_plan=True, trusted_step_receipts={receipt})


def test_certified_ribbon_example_releases_only_the_current_operation(ribbon):
    registry = json.loads((CONFIG / "guided-calculation-plan.receipts.json").read_text())
    source = validate_source(ribbon, allow_calculation_plan=True, trusted_step_receipts=set(registry["receipts"]))
    first, final = source["verified_calculation_plan"]["steps"]
    # Independent check by counting disjoint marked metre segments in the original ribbon.
    uncut = set(range(18)) - set(range(0, 2)) - set(range(2, 4)) - set(range(4, 6))
    assert calculation_for_step(source, final) == ("18-6", len(uncut))
    hint = public_calculation_step(source, first, set())
    assert hint["operation"] == "3*2" and hint["question_quote"] == "It cuts 3 strips of 2 metres each."
    assert "6" not in hint["operation"] and not hint["requires"] and not hint["is_final"]
    with pytest.raises(ValueError, match="current certified"):
        public_calculation_step(source, final, set())
    with pytest.raises(ValueError, match="missing a prerequisite"):
        next_calculation_step(source, {"remaining"})
    assert public_calculation_step(source, final, {"cut"})["requires"] == ["cut"]
    assert next_calculation_step(source, {"cut", "remaining"}) is None


def test_input_cannot_attest_itself_or_reuse_a_receipt_after_changing_the_quantity(ribbon):
    receipt = ribbon["verified_calculation_plan"]["receipt_id"]
    with pytest.raises(ValueError, match="independently trusted"):
        validate_source(ribbon, allow_calculation_plan=True)
    ribbon["verified_calculation_plan"]["steps"][0]["unit"] = "inches"
    with pytest.raises(ValueError, match="independently trusted"):
        validate_source(ribbon, allow_calculation_plan=True, trusted_step_receipts={receipt})
    assert "verified_calculation_plan" not in validate_source(ribbon)


def test_even_attested_arithmetic_needs_valid_source_operands_and_a_closed_derivation(ribbon):
    steps = ribbon["verified_calculation_plan"]["steps"]
    steps[1]["inputs"][0], steps[1]["inputs"][1] = steps[1]["inputs"][1], steps[1]["inputs"][0]
    with pytest.raises(ValueError, match="operands differ"):
        validate(ribbon)
    steps.pop()
    with pytest.raises(ValueError, match="independent answer key"):
        validate(ribbon)


@pytest.mark.parametrize("old,new", [("3*2=6", "3*2=7"), ("3*2=6", "3**2=9"), ("3*2=6", "+3*2=6")])
def test_does_not_trust_false_or_unsupported_source_equations(ribbon, old, new):
    # Replace the cited source verbatim and re-attest the new bytes like a faulty source verifier.
    ribbon["source_reasoning"] = ribbon["source_reasoning"].replace(old, new)
    for step in ribbon["verified_calculation_plan"]["steps"]:
        term = new if step["id"] == "cut" else "18-6=12"
        start = ribbon["source_reasoning"].index(term)
        step["equation_span"] = [start, start + len(term)]
    with pytest.raises(ValueError, match="does not establish"):
        validate(ribbon)


def test_language_span_must_cover_the_actual_question_quantity(ribbon):
    question = "共18米，剪3段，每段2米。剩多少米？"
    ribbon["question"] = question
    first, final = ribbon["verified_calculation_plan"]["steps"]
    first["question_span"], final["question_span"] = [5, 14], [14, len(question)]
    for item, value in [(first["inputs"][0], "3"), (first["inputs"][1], "2"), (final["inputs"][0], "18")]:
        start = question.index(value)
        item["question_span"] = [start, start + len(value)]
    assert validate(ribbon)["gold"]["value"] == "12"
    final["inputs"][0]["question_span"][0] += 1
    with pytest.raises(ValueError, match="part of a quantity"):
        validate(ribbon)


def test_batch_authoring_recreates_reviewed_example_and_reports_ambiguous_quotes(tmp_path, ribbon):
    from lib.guided_plan_candidates import prepare_batch

    example = (CONFIG / "guided-calculation-author.example.jsonl").read_text(encoding="utf-8")
    ambiguous = json.loads(example)
    ambiguous["id"] = "ambiguous"
    ambiguous["calculation_steps"][0]["question_quote"] = "metres"
    repaired = json.loads(json.dumps(ambiguous))
    repaired["id"] = "resolved"
    repaired["calculation_steps"][0]["question_quote"] = {"text": "metres", "within": "2 metres each"}
    lines = tmp_path / "source.jsonl"
    lines.write_text(example + json.dumps(ambiguous) + "\n" + json.dumps(repaired) + "\n", encoding="utf-8")
    destination = tmp_path / "candidates"
    assert prepare_batch(lines, destination) == {"candidates": 2, "unresolved": 1,
                                                "review_status": "requires_independent_attestation"}
    first, second = map(json.loads, (destination / "candidates.jsonl").read_text().splitlines())
    assert first == ribbon
    p = second["verified_calculation_plan"]["steps"][0]["question_span"]
    assert p[0] == second["question"].index("2 metres each") + 2
    issue = json.loads((destination / "unresolved.jsonl").read_text())
    assert issue["input_line"] == 2 and issue["id"] == "ambiguous" and "ambiguous quote" in issue["issue"]
    assert {path.name for path in destination.iterdir()} == {"candidates.jsonl", "unresolved.jsonl", "summary.json"}
    with pytest.raises(ValueError, match="independently trusted"):
        validate_source(first, allow_calculation_plan=True)
    with pytest.raises(ValueError, match="preserve"):
        prepare_batch(lines, destination)
