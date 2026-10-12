"""Template arithmetic has its own deterministic lineage, never document seeds."""
from pathlib import Path
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_workflow_production import model_request_data, Reviewer


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model operations are injected or forbidden; no shared port is needed."""
    yield


class NoModels:
    model = "unused-offline"
    usage = {}

    def chat(self, *args, **kwargs):
        pytest.fail("Deterministic arithmetic must not call a model.")


def production(*targets, goal=5):
    return {"version": 2, "quantity_policy": "quality_first", "goals": {target: goal for target in targets},
            "round_size": 2, "max_rounds": 5, "max_attempts": 10}


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("parser", ["model", "vision"])
def test_pure_arithmetic_does_not_parse_documents_or_require_parser_service(tmp_path, monkeypatch, batch, parser):
    source = tmp_path / "unused.pdf"
    source.write_bytes(b"Not a readable PDF; template arithmetic does not consume it.")
    def forbidden(*args, **kwargs):
        pytest.fail("Inactive parsing must not probe, load, or call a model.")
    monkeypatch.setattr(engine.Workflow, "parse_source", forbidden)
    monkeypatch.setattr(engine.Workflow, "iter_source_units", forbidden)
    monkeypatch.setattr(engine, "snapshot_backend_endpoint", forbidden)
    monkeypatch.setattr("lib.infrastructure.document_vision.require_vision_model", forbidden)
    binding = {"backend": "deleted-parser", "model": "missing-model"}
    run_id = engine.create_run(tmp_path, sources=[source], targets=["gsm8k"], tasks=5, max_units=1,
        document_parser={"mode": parser, "binding": binding},
        node_models={"ingest": {"generation": binding}}, settings_root=Path(__file__).resolve().parents[1],
        production=production("gsm8k") if batch else None)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert workflow.recipe["arithmetic_seed_protocol_version"] == 1
    assert "ingest" not in workflow.recipe["node_models"]
    assert state["stages"]["ingest"]["status"] == "skipped"
    assert state["input_summary"]["units"] == 0
    assert not state["usage"]
    records = engine.read_json(workflow.path / "artifacts/gsm8k.records.json")
    assert len(records) == 5 and all(row["status"] == "eligible" for row in records)
    assert all(row["source_id"] != workflow.recipe["sources"][0]["sha256"] for row in records)
    assert all("unused.pdf" not in row["question"] for row in records)


@pytest.mark.parametrize("batch", [False, True])
def test_pure_arithmetic_accepts_empty_sources_and_theme(tmp_path, batch):
    run_id = engine.create_run(tmp_path, targets=["gsm8k"], tasks=5, max_units=1,
        production=production("gsm8k") if batch else None)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert engine.verify_artifacts(workflow.path)["counts"] == {"gsm8k": 5}
    assert state["stages"]["ingest"]["status"] == "skipped"


@pytest.mark.parametrize("batch", [False, True])
def test_quarantined_sources_do_not_suppress_independent_arithmetic(tmp_path, batch):
    source = tmp_path / "broken.jsonl"
    source.write_text("not valid json\n", encoding="utf-8")
    run_id = engine.create_run(tmp_path, sources=[source], targets=["sft", "gsm8k"], tasks=5,
        production=production("sft", "gsm8k") if batch else None)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["input_summary"]["quarantined"] == 1
    assert engine.verify_artifacts(workflow.path)["counts"] == {"sft": 0, "gsm8k": 5}
    records = engine.read_json(workflow.path / "artifacts/gsm8k.records.json")
    assert all(row["source_id"] != workflow.recipe["sources"][0]["sha256"] for row in records)
    if batch:
        assert state["production"]["goals"]["gsm8k"]["attempted"] == 5
        assert state["production"]["goals"]["sft"]["attempted"] == 0
    assert not state["usage"]


def test_math_batch_resume_reuses_committed_independent_seeds(tmp_path):
    run_id = engine.create_run(tmp_path, targets=["gsm8k"], production=production("gsm8k"))
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    original_event = workflow.event
    def interrupt(kind, **fields):
        original_event(kind, **fields)
        if kind == "production_round_completed" and fields["round"] == 1:
            engine.cancel(tmp_path, run_id)
    workflow.event = interrupt
    state = workflow.execute()
    assert state["status"] == "cancelled", state.get("error")
    first_path = workflow.path / "production/round-000001/gsm8k.jsonl"
    first_bytes = first_path.read_bytes()
    restored = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert first_path.read_bytes() == first_bytes
    records = engine.read_json(restored.path / "artifacts/gsm8k.records.json")
    assert len(records) == len({row["id"] for row in records}) == 5
    assert state["production"]["attempted"] == 5
    assert len(restored._production_receipts(restored.path / "production")) == 3


def test_exhausted_open_planning_does_not_stop_independent_math_batches(tmp_path):
    class ExhaustedPlanner(NoModels):
        def __init__(self):
            self.calls = 0
            self.usage = {"calls": 0}

        def chat(self, messages, **kwargs):
            data = json.loads(messages[-1]["content"])
            assert "count" in data and "candidate" not in data
            self.calls += 1
            self.usage["calls"] += 1
            return json.dumps({"tasks": [], "exhausted": True, "stop_reason": "source_exhausted"})

    run_id = engine.create_run(tmp_path, targets=["sft", "gsm8k"], brief="A limited arithmetic lesson.",
        production=production("sft", "gsm8k"))
    planner = ExhaustedPlanner()
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=planner, jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert planner.calls == 1
    assert engine.verify_artifacts(workflow.path)["counts"] == {"sft": 0, "gsm8k": 5}
    assert state["input_summary"]["units"] == 0
    assert state["production"]["goals"]["sft"]["stop_reason"] == "source_exhausted"
    assert state["production"]["goals"]["gsm8k"]["remaining"] == 0
    assert len(workflow._production_receipts(workflow.path / "production")) == 3


class CountingWriter:
    model = "offline-bounded-generation"

    def __init__(self, *, short_first=False):
        self.usage = {"calls": 0}
        self.plans, self.answers = [], []
        self.short_first = short_first

    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        self.usage["calls"] += 1
        if "count" in data:
            self.plans.append((data["count"], data["offset"]))
            size = 1 if self.short_first and len(self.plans) == 1 else data["count"]
            return json.dumps({"tasks": [f"Explain safe maintenance scenario {index}."
                for index in range(data["offset"], data["offset"] + size)], "exhausted": False})
        source = data["source"]
        self.answers.append(source["id"])
        return json.dumps({"question": "Explain this operation " + source["id"][:12],
            "answer": "Check the documented conditions before performing the operation.",
            "reasoning": "The procedure depends on the documented conditions.",
            "quotes": [source["text"]]})


@pytest.mark.parametrize("policy", ["quality_first", "bounded_replenishment"])
@pytest.mark.parametrize("documents", [False, True])
def test_large_arithmetic_goal_cannot_expand_paid_generation_window(tmp_path, documents, policy):
    sources = []
    if documents:
        for name, text in (("safety", "维修设备前必须断电。线路检查完成后，记录检修时间与发现的故障。"),
                           ("delivery", "仓库发货时核对订单中的商品编码和数量。装箱后记录包裹重量与承运单号。")):
            source = tmp_path / f"{name}.txt"
            source.write_text(text, encoding="utf-8")
            sources.append(source)
    run_id = engine.create_run(tmp_path, sources=sources, brief="" if documents else "Maintenance examples.",
        targets=["sft", "gsm8k"], production={"version": 2, "quantity_policy": policy,
            "goals": {"sft": 2, "gsm8k": 1000}, "round_size": 100, "max_rounds": 1, "max_attempts": 100})
    writer = CountingWriter()
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=writer, jev=Reviewer())
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(writer.answers) == 2
    assert len(set(writer.answers)) == 2
    assert writer.plans == ([] if documents else [(2, 0)])
    assert state["production"]["goals"]["sft"]["attempted"] == 2
    assert state["production"]["attempted"] == 100  # Arithmetic still respects the finite run cap.
    records = engine.read_json(workflow.path / "artifacts/sft.records.json")
    assert len(records) == 2 and all(row["status"] == "eligible" for row in records)
    receipts = workflow._production_receipts(workflow.path / "production")
    assert receipts[0]["generation_attempted"] == 2
    assert receipts[0]["arithmetic_attempted"] == 100


@pytest.mark.parametrize("policy", ["quality_first", "bounded_replenishment"])
def test_short_generation_window_resumes_with_its_own_offset_and_no_repeated_answers(tmp_path, policy):
    run_id = engine.create_run(tmp_path, brief="Maintenance examples.", targets=["sft", "gsm8k"],
        production={"version": 2, "quantity_policy": policy, "goals": {"sft": 3, "gsm8k": 8},
                    "round_size": 4, "max_attempts": 12, "max_rounds": 4})
    writer = CountingWriter(short_first=True)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=writer, jev=Reviewer())
    original_event = workflow.event
    def interrupt(kind, **fields):
        original_event(kind, **fields)
        if kind == "production_round_completed" and fields["round"] == 1:
            engine.cancel(tmp_path, run_id)
    workflow.event = interrupt
    state = workflow.execute()
    assert state["status"] == "cancelled", state.get("error")
    assert writer.plans == [(3, 0)] and len(writer.answers) == 1
    first_path = workflow.path / "production/round-000001/sft.jsonl"
    first_bytes = first_path.read_bytes()

    restored = engine.Workflow(tmp_path, run_id, tmp_path, generator=writer, jev=Reviewer())
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert writer.plans == [(3, 0), (2, 1)]
    assert len(writer.answers) == len(set(writer.answers)) == 3
    assert first_path.read_bytes() == first_bytes
    assert engine.verify_artifacts(restored.path)["counts"] == {"sft": 3, "gsm8k": 8}
    receipts = restored._production_receipts(restored.path / "production")
    assert [row["generation_attempted"] for row in receipts] == [1, 2]
    assert [row["arithmetic_attempted"] for row in receipts] == [4, 4]
    assert [row["planning"]["offset"] for row in receipts] == [0, 1]


def test_unflagged_legacy_arithmetic_retains_original_ingest_and_seed_identity(tmp_path, monkeypatch):
    source = tmp_path / "legacy-source.txt"
    source.write_text("Retained immutable legacy source.", encoding="utf-8")
    run_id = engine.create_run(tmp_path, sources=[source], targets=["gsm8k"], tasks=5, max_units=1)
    path = engine.run_path(tmp_path, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("arithmetic_seed_protocol_version")
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    calls = []
    original = engine.Workflow.iter_source_units
    def parsed(self, source):
        calls.append(source["sha256"])
        yield from original(self, source)
    monkeypatch.setattr(engine.Workflow, "iter_source_units", parsed)
    state = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels()).execute()
    assert state["status"] == "completed", state.get("error")
    assert len(calls) == 1
    records = engine.read_json(path / "artifacts/gsm8k.records.json")
    assert len(records) == 1
    assert records[0]["id"] == engine.digest([engine.canonical(recipe["sources"]), "gsm8k", 0])
