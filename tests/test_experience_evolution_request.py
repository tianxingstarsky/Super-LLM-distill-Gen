"""Proposal transmission uses the node model once and saves its raw receipt."""
import json
from types import SimpleNamespace

from lib.bootstrap.experience_evolution import experience_evolution_application


def test_strict_proposal_is_reused_and_isolated_by_user(tmp_path, monkeypatch):
    from lib import llm_client
    calls = []
    class Writer:
        base_url = "https://example.test/v1"
        api_format = "chat"
        client = SimpleNamespace(close=lambda: None)
        def tool_turn(self, messages, tools, **options):
            calls.append((messages, tools, options))
            assert options["strict"] is True and options["retries"] == 1
            assert tools[0]["function"]["strict"] is True
            assert tools[0]["function"]["parameters"]["properties"]["rules"]["minItems"] == 0
            options["response_details"]["usage"] = {"completion_tokens": 40}
            return {"role": "assistant", "content": "", "reasoning_content": "原生提炼思考保持原文。",
                "tool_calls": [{"function": {"name": "submit_experience_rules", "arguments": json.dumps({
                    "summary": "保留条件，以实际反馈修正，不重复完整草稿。",
                    "rules": [{"principle": "重新核对用户的约束", "when": "用户指出页面难以使用时",
                        "avoid": "不把偏好当成所有页面的普遍规则",
                        "evidence": [{"episode_id": "task-1", "quote": "这个入口找不到。"}]}]},ensure_ascii=False)}}]}
    monkeypatch.setattr(llm_client, "load_backend", lambda *a, **k: (Writer(), "configured-model"))
    app = experience_evolution_application({"backend": "configured", "model": "configured-model"},
        root=tmp_path, user_key="alice")
    seed = app.save_version("reasoning", "简洁、全面。", label="初始版", status="initial")
    episodes = [{"id": "task-1", "messages": [{"role": "user", "content": "这个入口找不到。"}]}]
    first = app.propose("reasoning", seed["text"], episodes, parent_id=seed["id"])
    second = app.propose("reasoning", seed["text"], episodes, parent_id=seed["id"])
    assert len(calls) == 1
    assert first["id"] != second["id"] and first["parent_id"] == seed["id"]
    assert first["quality"] == "model_proposed_not_verified"
    assert first["episodes"] == episodes
    assert first["model_request_receipt"]["response_details"]["usage"]["completion_tokens"] == 40
    saved = list((tmp_path/"experience-evolution").rglob("requests/*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text("utf8"))["assistant"]["reasoning_content"] == "原生提炼思考保持原文。"
    other = experience_evolution_application(root=tmp_path, user_key="bob")
    assert other.list_versions("reasoning") == []
