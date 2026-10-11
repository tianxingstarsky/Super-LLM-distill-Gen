"""Sealed cross-paragraph source packs survive authenticated review-only rework."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.domain.human_augmentation import validate_revision_context
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_revision import revision_evidence, verified_revision_batch, verified_revision_parent
from lib.infrastructure.workflow_source_context import load_context_pack
from test_human_sessions import Writer as RepairWriter, Reviewer


PREPARATION = ("巡检准备阶段先确认设备已停机，核查维护记录和工单，佩戴防护用具。"
               "随后检查现场照明、通道与警示标记，确认作业负责人和设备编号一致。"
               "将检查结果填入工单；发现编号不一致时先核对身份，不进入后续操作。"
               "准备阶段的人员负责记录时间和环境条件，不负责完成复位。")
COMPLETION = ("巡检完成阶段先核对阀门与连接件的状态，确认无遗留工具后签署检查记录。"
              "最后复位时先关闭隔离开关，再由作业负责人确认启动许可。"
              "若许可尚未确认，保持设备停止并联系负责人，不得自行启动。"
              "完成复位后观察设备运行状态，记录温度和异常声音，发现异常应停机报告。")
QUOTE = "最后复位时先关闭隔离开关，再由作业负责人确认启动许可。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield None


class ParentWriter:
    model = "offline-parent-context-writer"
    usage = {}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        if "candidates" in data:
            return json.dumps({"tasks": [{"id": candidate["id"], "qa_type": "grounded",
                "question": "巡检结束后，复位和启动许可如何处理？", "visible_context": QUOTE,
                "answer_policy": "answer", "guidance": "依据完成阶段说明复位顺序。",
                "evidence_quotes": [QUOTE]} for candidate in data["candidates"]]})
        if "qa_contract" in data:
            return json.dumps({"question": data["qa_contract"]["question"],
                "answer": "先关闭隔离开关，再请作业负责人确认启动许可。",
                "reasoning": "复位操作与启动许可必须按照既定顺序完成。", "quotes": [QUOTE]})
        raise AssertionError("unexpected parent request")


def prepared(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs", dirs_exist_ok=True)
    source = tmp_path / "manual.txt"
    source.write_text(PREPARATION + "\n\n" + COMPLETION, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], chunk_chars=200, sample_count=1,
        qa_director={"enabled": True, "batch_size": 1, "history_limit": 0,
                     "type_weights": {"closed_book": 0, "grounded": 100, "partial": 0,
                                      "multi_source": 0, "distractor": 0}})
    state = engine.Workflow(output, run_id, tmp_path, generator=ParentWriter(), judge=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    app = human_augmentation_application(tmp_path, output)
    sid = app.create_session(from_run_id=run_id, review_only=True)
    app.configure_review_models(sid, {"generation": {"backend": "ollama", "model": "offline-rework"}},
                                expected_version=app.session(sid)["version"])
    candidate = app.results(sid)["rows"][0]
    parent = app.session(sid)["rounds"][0]
    saved = app.save_issues(sid, [{"round_id": parent["id"], "target": "sft",
                                 "candidate_id": candidate["candidate_id"]}],
        instruction="使用安全提醒的口吻。", expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="packed-parent-rework", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    return app, sid, output, run_id, child, recipe["repair_inputs"][0]


def test_rework_authenticates_full_parent_pack_and_passes_neighbor_fact_to_child(tmp_path):
    app, sid, output, parent_id, child, item = prepared(tmp_path)
    context = item["revision_context"]
    anchor = item["record"]["source_context"]
    assert QUOTE not in anchor["text"]
    parent_path = engine.run_path(output, parent_id)
    pack = load_context_pack(parent_path, anchor["source_context_pack"])
    expected = "\n\n".join(material["text"] for material in pack["materials"])
    assert context["teacher_evidence"] == expected and QUOTE in expected
    assert context["evidence_pack_sha256"] == pack["prefix_sha256"]
    with verified_revision_batch(output, child["run_id"], [context]):
        assert verified_revision_parent(output, child["run_id"], context) == item["record"]
    assert not (engine.run_path(output, child["run_id"]) / "source-context").exists()
    writer = RepairWriter()
    state = engine.Workflow(output, child["run_id"], tmp_path, generator=writer, judge=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    source_reviews = [call for call in writer.calls if isinstance(call.get("source_evidence"), dict)]
    assert source_reviews and all(call["source_evidence"]["teacher_evidence"] == expected for call in source_reviews)
    result = app.results(sid, round_id=child["id"])["rows"][0]["record"]
    assert result["review_repair"]["status"] == "accepted"
    # A later repair uses the prior child's fixed evidence snapshot. It must
    # not resolve a parent's relative pack path under the child directory.
    child_recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    inherited = revision_evidence(result, child_recipe, engine.run_path(output, child["run_id"]))
    assert inherited["teacher_evidence"] == expected
    assert inherited["evidence_pack_sha256"] == context["evidence_pack_sha256"]


def test_rework_rejects_changed_source_pack_inside_authenticated_batch(tmp_path):
    _, _, output, parent_id, child, item = prepared(tmp_path)
    context = item["revision_context"]
    reference = item["record"]["source_context"]["source_context_pack"]
    pack_path = engine.run_path(output, parent_id) / reference["relative_path"]
    with verified_revision_batch(output, child["run_id"], [context]):
        verified_revision_parent(output, child["run_id"], context)
        text = pack_path.read_text(encoding="utf-8")
        pack_path.write_text(text.replace("启动许可", "错误命令", 1), encoding="utf-8")
        with pytest.raises(ValueError, match="source_context_pack_(changed|invalid)"):
            verified_revision_parent(output, child["run_id"], context)


def test_packed_revision_requires_fixed_parent_path_and_exact_evidence_identity(tmp_path):
    _, _, output, parent_id, child, item = prepared(tmp_path)
    recipe = engine.read_json(engine.run_path(output, parent_id) / "recipe.json")
    with pytest.raises(ValueError, match="human_session_source_evidence_unavailable"):
        revision_evidence(item["record"], recipe)
    changed = {**item["revision_context"], "evidence_pack_sha256": "0" * 64}
    with pytest.raises(ValueError, match="human_session_result_changed"):
        verified_revision_parent(output, child["run_id"], changed)
    changed = {**item["revision_context"], "teacher_evidence": item["revision_context"]["teacher_evidence"] + "\nInvented source."}
    with pytest.raises(ValueError, match="human_session_result_changed"):
        verified_revision_parent(output, child["run_id"], changed)


def test_only_verified_pack_snapshots_can_exceed_legacy_evidence_limit(tmp_path):
    _, _, _, _, _, item = prepared(tmp_path)
    context = deepcopy(item["revision_context"])
    context["teacher_evidence"] = "Accepted source text. " * 15_000
    assert len(context["teacher_evidence"]) > 200_000
    assert validate_revision_context(context, review=True)["teacher_evidence"] == context["teacher_evidence"].strip()
    context.pop("evidence_pack_sha256")
    with pytest.raises(ValueError, match="invalid_human_augmentation_text"):
        validate_revision_context(context, review=True)
