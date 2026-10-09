"""Actual package sampling expands safely and reuses completed AI verdicts."""
from collections import Counter
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.llm_client import BudgetExceeded


class Reviewer:
    model = "offline-escalation-reviewer"

    def __init__(self, *, fail_at=None):
        self.usage, self.payloads = {"calls": 0}, []
        self.fail_at, self.bad_text = fail_at, None

    def chat(self, messages, **kwargs):
        payload = json.loads(messages[1]["content"])["answer"]
        self.payloads.append(payload)
        self.usage["calls"] += 1
        if self.bad_text is None:
            self.bad_text = payload["text"]
        if len(self.payloads) == self.fail_at:
            raise BudgetExceeded("provider private text must not be saved")
        keep = payload["text"] != self.bad_text
        score = 5 if keep else 1
        return json.dumps({"keep": keep, "grounded": keep, "reasoning_valid": keep,
            "correctness": score, "scores": {key: score for key in
                ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Consistent." if keep else "Needs source correction."})


def make_run(tmp_path, reviewer, *, count=6, percent=1., threshold=None, production=True):
    source = tmp_path / "source.txt"
    source.write_text("Disconnect power before inspection.", encoding="utf-8")
    review = {"enabled": True, "mode": "sample", "sample_percent": percent,
              "max_samples_per_target": 100}
    if threshold is not None:
        review["escalate_failure_percent"] = threshold
    output = tmp_path / "out"
    rid = engine.create_run(output, sources=[source], targets=["cpt"], package_review=review,
        batch_size=1, **({"production": {"goals": {"cpt": count}}} if production else {}))
    path = engine.run_path(output, rid)
    (path / "input_records.json").write_text("[]", encoding="utf-8")
    rows = [{"id": f"sample-{index}", "source_id": "source", "status": "eligible",
             "text": f"Maintenance case {index}: disconnect power before inspecting the cable."}
            for index in range(count)]
    return engine.Workflow(output, rid, tmp_path, jev=reviewer), rows, output, rid, path


def summary(path):
    return engine.read_json(path / "artifacts/quality.json")["package_review"]["targets"]["cpt"]


def test_any_failed_sample_expands_current_target_and_does_not_pay_twice(tmp_path):
    reviewer = Reviewer()
    workflow, rows, _, _, path = make_run(tmp_path, reviewer)
    workflow.package({"cpt": rows})
    result = summary(path)
    assert result["planned"] == result["reviewed"] == 6
    assert result["accepted"] == 5 and result["rejected"] == 1
    assert result["unreviewed"] == 0 and result["coverage_percent"] == 100.
    assert result["escalation"]["initial_sampled"] == 1
    assert result["escalation"]["additional_reviews"] == 5
    assert result["escalation"]["scope"] == "target_current_round"
    assert len(reviewer.payloads) == 6
    assert all(count == 1 for count in Counter(p["text"] for p in reviewer.payloads).values())


def test_custom_threshold_does_not_expand_below_failure_rate(tmp_path):
    reviewer = Reviewer()
    workflow, rows, _, _, path = make_run(tmp_path, reviewer, percent=30., threshold=100.)
    workflow.package({"cpt": rows})
    result = summary(path)
    assert result["planned"] == result["reviewed"] == 2
    assert len(reviewer.payloads) == 2 and result["unreviewed"] == 4
    assert not result.get("escalation")


def test_legacy_sampling_keeps_coverage_even_with_a_rejected_sample(tmp_path):
    reviewer = Reviewer()
    workflow, rows, _, _, path = make_run(tmp_path, reviewer, production=False)
    workflow.package({"cpt": rows})
    assert summary(path)["reviewed"] == 1
    assert len(reviewer.payloads) == 1
    assert not summary(path).get("escalation")


def test_budget_stop_in_expansion_reuses_prior_verdicts_after_resume(tmp_path):
    reviewer = Reviewer(fail_at=3)
    workflow, rows, output, rid, path = make_run(tmp_path, reviewer)
    with pytest.raises(BudgetExceeded):
        workflow.package({"cpt": rows})
    completed = [payload["text"] for payload in reviewer.payloads[:2]]
    assert not (path / "artifacts/manifest.json").exists()
    reviewer.fail_at = None
    restored = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    restored.package({"cpt": rows})
    counts = Counter(payload["text"] for payload in reviewer.payloads)
    assert all(counts[text] == 1 for text in completed)
    assert len(reviewer.payloads) == 7
    assert summary(path)["reviewed"] == 6
