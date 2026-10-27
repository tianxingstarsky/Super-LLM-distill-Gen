"""Real local Math-Verify; no paid requests and no inference about prose or units."""
from copy import deepcopy
import pytest

from lib.contexts.review.api import VerifyReferenceAnswer
from lib.contexts.review.infrastructure.math_reference_adapter import MathVerifyReferenceAdapter
from lib.contexts.workflows.application.generate_cot import GenerateCot
from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING
from test_integrated_reflection import recipe


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("expected,candidate,status", [
    (r"\frac{1}{2}", "0.5", "matched"), ("1", "1.00000001", "mismatched"),
    ("10", "10cm", "unresolved"), ("1", "unknown", "unresolved"),
    ("2", "$999$ $2$", "unresolved"), ("2", r"999 \boxed{2}", "unresolved"),
])
def test_reference_sdk_keeps_numeric_precision_and_never_strips_units(expected, candidate, status):
    receipt = VerifyReferenceAnswer(MathVerifyReferenceAdapter()).execute(
        {"version": 1, "kind": "numeric_expression", "expected": expected}, candidate)
    assert receipt["status"] == status and not receipt["reasoning_verified"]
    if status != "unresolved":
        assert receipt["verifier"] == "math-verify" and receipt["verifier_version"] == "0.9.0"


def test_wrong_fixed_answer_stops_before_paid_restyle_keeps_body_and_reuses_receipt():
    class CountVerifier(VerifyReferenceAnswer):
        calls = 0
        def execute(self, *args):
            self.calls += 1
            return super().execute(*args)
    verifier = CountVerifier(MathVerifyReferenceAdapter())
    sample = conversation()
    sample["messages"][-1]["content"] = "3"
    sample["source_context"] = {"answer_reference": {"version": 1, "kind": "numeric_expression", "expected": "4"}}
    before = deepcopy(sample)
    config = recipe("faithful_restyle"); config["cot_style_transfer_version"] = 2
    port = MemoryGenerationPort()
    usecase = GenerateCot(port, config, reference_verifier=verifier)
    first, = usecase.generate_styled(sample, {"instruction": "自然表达。"})
    assert usecase.generate_styled(sample, {"instruction": "自然表达。"}) == [first]
    assert first["reason"] == "cot_original_answer_reference_mismatched"
    assert first["reasoning"] == [REASONING] and first["answer"] == "3"
    assert first["style_transfer"]["original"]["reasoning"] == REASONING
    assert verifier.calls == 1 and port.calls == [] and sample == before


def test_missing_reference_is_not_success_and_worker_timeout_is_unresolved():
    verifier = VerifyReferenceAnswer(MathVerifyReferenceAdapter(timeout_seconds=0.001))
    assert verifier.execute(None, "2")["status"] == "not_applicable"
    assert verifier.execute({"version": 1, "kind": "numeric_expression", "expected": "2"}, "2")["reason"] == "math_verifier_timeout"
