"""A real initial-batch envelope must not resemble a dialogue continuation."""
from copy import deepcopy

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.qa_history import QAHistory
from tests.test_workflow_qa_director_engine import config, make_run


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Capture before any model request; no HTTP service is required."""
    yield


@pytest.mark.parametrize("mode", ["balanced", "adaptive"])
@pytest.mark.parametrize("historical", [False, True])
def test_initial_batch_phase_is_explicit_without_reclassifying_history(tmp_path, mode, historical):
    settings = config(planning_mode=mode)
    output, rid, _ = make_run(tmp_path, options=settings)
    worker = engine.Workflow(output, rid, tmp_path)
    assert worker.recipe["qa_batch_phase_version"] == 1
    if historical:
        worker.recipe.pop("qa_batch_phase_version")
    worker.state["qa_director"] = {"coverage": {}, "feedback": {}}
    captured = []

    class CapturedBeforeModel(RuntimeError):
        pass

    def capture(key, role, prompt_id, payload, **kwargs):
        captured.append(deepcopy(payload))
        raise CapturedBeforeModel

    worker.ask = capture
    sample = {"id": "new-source", "source_id": "brief-source", "kind": "brief",
              "text": "设计设备检查训练对话。"}
    prior = {"id": "old-run:sample", "run_id": "old-run", "status": "eligible",
             "question": "先做什么？", "answer": "先断电。", "qa_type": "closed_book",
             "visible_context": "", "answer_policy": "answer"}
    with QAHistory(output / "local-history.sqlite3") as local, QAHistory(output / "published-history.sqlite3") as published:
        assert published.add(prior, namespace="sft")
        with pytest.raises(CapturedBeforeModel):
            worker._director_batch_plan([sample], 0, settings, local, published, ["sft"])
        actual = captured[0]
        assert actual["history"][0]["question"] == prior["question"]
        assert actual["history"][0]["answer"] == prior["answer"]
        assert actual["history"][0]["run_id"] == prior["run_id"]
        assert actual["history_is_not_evidence"] is True
        if historical:
            assert (actual.get("request_phase") == "batch_design") is (mode == "adaptive")
            assert "history_is_current_dialogue" not in actual
            assert "history_role" not in actual
        else:
            assert actual["request_phase"] == "batch_design"
            assert actual["planning_mode"] == mode
            assert actual["history_is_current_dialogue"] is False
            assert actual["history_role"] == "completed_independent_examples_for_coverage_and_deduplication"
