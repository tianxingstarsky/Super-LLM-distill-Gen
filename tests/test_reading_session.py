"""One saved reading context, private actor state and literal native exports."""
from copy import deepcopy
import json
import pytest
from lib.infrastructure import training_workflow as engine
from lib.contexts.workflows.contracts import training_record, has_public_reading_context
from lib.contexts.workflows.domain.reading_session import reading_source
from lib.contexts.workflows.domain.dialogue import DialogueSource
from lib.domain.training_surface import training_record_surface_issue
from lib.domain.workflow_node_prompts import active_node_prompt_ids, snapshot_node_prompts


def config():
    return {"multiturn": {"enabled": True, "style": "default", "instruction": "", "reading_session": True}}


def test_public_context_native_history_resume_and_export(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("实验只显示这一组条件下的观察差异，不能由此推出唯一原因。", encoding="utf8")
    output = tmp_path / "out"
    rid = engine.create_run(output, sources=[source], targets=["multiturn"], node_generation=config(), conversation_turns=3,
        agent_experience={"reasoning": {"enabled": True, "text": "PROFILE_LITERAL_{unchanged}"}})
    class Author:
        reasoning = {"mode": "enabled"}
        calls = []
        interrupt = True
        def chat(self, messages, **options):
            system = messages[0]["content"]
            if "私有背景" in system:
                kind, turn = "scene", 0
                answer = "PRIVATE_STORY：我在准备组会，尚未跑实验，想讨论论文里的方法。"
                native = "私有场景设计思考。"
            elif "扮演场景中的用户" in system:
                kind = "user"
                turn = sum(m["role"] == "assistant" for m in messages)
                assert not any("public native" in m["content"] for m in messages)
                assert source.read_text(encoding="utf8") not in system
                assert "PRIVATE_STORY" in system
                answer = ["这能说明什么？", "那因果呢？", "<END_DIALOGUE>"][turn]
                native = "私有用户模拟思考。"
            else:
                kind = "answer"
                turn = sum(m["role"] == "user" for m in messages) - 1
                assert "PRIVATE_STORY" not in json.dumps(messages, ensure_ascii=False)
                assert not any("reasoning_content" in m for m in messages)
                answer = ["当前只能说明该条件下观察到了差异。", "还不能认定唯一原因，需要适合该问题的对照或其它识别依据。"][turn]
                native = "  public native：系统提示词要求区分观察与原因。根据所给资料，用户问的是结论边界。\n"
            self.calls.append((kind, turn, deepcopy(messages)))
            if kind == "answer" and turn == 1 and self.interrupt:
                self.interrupt = False
                raise RuntimeError("test interrupted once")
            options["response_details"].update(api_format="chat", reasoning_content=native)
            return answer
    author = Author()
    unit = {"id": "reading", "source_id": "paper", "kind": "document", "text": source.read_text(encoding="utf8")}
    workflow = engine.Workflow(output, rid, tmp_path, generator=author)
    workflow.stage = "multiturn"
    with pytest.raises(RuntimeError, match="test interrupted"):
        workflow.multiturn(unit)
    resumed = engine.Workflow(output, rid, tmp_path, generator=author)
    resumed.stage = "multiturn"
    row, = resumed.multiturn(unit)
    assert resumed.recipe["version"] == 41
    assert row["status"] == "eligible" and row["turn_count"] == 2
    assert row["reading_session"]["end_reason"] == "user_goal_complete"
    assert row["reading_session"]["semantic_status"] == "not_assessed" and "judge" not in row
    assert sum(kind == "scene" for kind, _, _ in author.calls) == 1
    assert sum(kind == "answer" and turn == 0 for kind, turn, _ in author.calls) == 1
    assert has_public_reading_context(row)
    assert training_record_surface_issue("multiturn", row, protocol_version=2) is None
    exported = training_record("multiturn", row)
    assert "PRIVATE_STORY" not in json.dumps(exported, ensure_ascii=False)
    assert exported["messages"][0] == next(ms[0] for kind, _, ms in author.calls if kind == "answer")
    assert "PROFILE_LITERAL_{unchanged}" in exported["messages"][0]["content"]
    assert exported["messages"][2]["reasoning_content"].startswith("  public native")
    assert source.read_text(encoding="utf8") in exported["messages"][0]["content"]
    assert [m["content"] for m in exported["messages"] if m["role"] == "user"] == ["这能说明什么？", "那因果呢？"]
    tampered = deepcopy(row)
    tampered["messages"][0]["content"] = "资料已经删除"
    assert training_record_surface_issue("multiturn", tampered, protocol_version=2) == "reading_session_context_mismatch"
    leaked = deepcopy(row)
    leaked["messages"][2]["reasoning_content"] += "我按照 qa_contract 配置回复。"
    assert training_record_surface_issue("multiturn", leaked, protocol_version=2) == "training_surface_internal_process_reference"
    assert resumed.multiturn(unit) == [row]
    assert len(author.calls) == 7  # one scene, three users, two answers + interrupted call


def test_reading_material_scope_and_old_prompt_catalog():
    from lib.contexts.workflows.domain.graph import execution_graph
    nodes, edges = execution_graph(["multiturn"], qa_director=True, recipe_version=41, node_generation=config())
    assert "director" not in nodes and ("cpt", "multiturn") in edges
    nodes, edges = execution_graph(["multiturn", "sft"], qa_director=True, recipe_version=41, node_generation=config())
    assert ("director", "sft") in edges and ("director", "multiturn") not in edges
    prepared = DialogueSource({}, "whole private paper")
    unit = {"qa_contract": {"qa_type": "partial", "visible_context": "visible excerpt"}}
    assert reading_source(unit, prepared) == "visible excerpt"
    unit["qa_contract"]["qa_type"] = "closed_book"
    with pytest.raises(ValueError, match="requires_visible"):
        reading_source(unit, prepared)
    assert "workflow.reading_answer" not in snapshot_node_prompts(None, recipe_version=40)["multiturn"]
    assert active_node_prompt_ids("multiturn", "文档", node_generation=config()) == (
        "workflow.reading_scene", "workflow.reading_user", "workflow.reading_answer")
