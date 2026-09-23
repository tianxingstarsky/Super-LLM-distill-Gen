"""Training previews keep conversational structure without executing sample HTML."""
from lib.presentation.streamlit.artifact_preview import render_training_sample


def test_agent_preview_keeps_tool_sequence_and_escapes_sample_content():
    sample = {
        "messages": [
            {"role": "user", "content": "计算 6 + 7"},
            {"role": "assistant", "content": "", "toolCalls": [
                {"id": "call-1", "name": "calculator", "arguments": {"expression": "6+7"}},
            ]},
            {"role": "tool", "name": "calculator", "toolCallId": "call-1", "content": "13"},
            {"role": "assistant", "content": "结果是 13 <script>alert(1)</script>"},
        ],
        "tools": [{"name": "calculator"}],
    }

    html = render_training_sample("agent", sample)

    assert "Agent 工具轨迹" in html
    assert "4 条消息" in html and "1 轮用户交互" in html and "1 次工具调用" in html
    assert "calculator" in html and "call-1" in html
    assert "Agent 执行轨迹" in html and "3 个步骤" in html
    assert html.index("计算 6 + 7") < html.index("calculator") < html.index("结果是 13")
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_multiturn_preview_shows_all_turns_and_escaped_answers():
    preview = render_training_sample("multiturn", {"messages": [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "继续说第二步"},
        {"role": "assistant", "content": "第二答 <script>unsafe</script>"},
    ]})
    assert "多轮对话与上下文" in preview
    assert "4 条消息" in preview and "2 轮用户交互" in preview
    assert "第 01 轮对话" in preview and "第 02 轮对话" in preview
    assert preview.count('<div class="df-artifact-turn-panel" data-role="input">') == 2
    assert preview.count('<div class="df-artifact-turn-panel" data-role="output">') == 2
    assert "第一问" in preview and "第二答" in preview
    assert "&lt;script&gt;" in preview and "<script>" not in preview


def test_preference_preview_compares_complete_answers_and_escapes_feedback():
    pair = {
        "prompt": [{"role": "user", "content": "如何维护设备？"}],
        "chosen": [{"role": "assistant", "content": "先断电，再检查。"}],
        "rejected": [{"role": "assistant", "content": "直接拆机。"}],
    }
    html = render_training_sample("orpo", pair)
    assert "共同提示上下文" in html
    assert "更优回答" in html and "对照回答" in html
    assert "先断电" in html and "直接拆机" in html

    feedback = render_training_sample("rlaif", {
        "prompt": pair["prompt"],
        "responses": [{"response": pair["chosen"], "preference_rank": 1,
                       "score": 5, "feedback": "保留 <img src=x onerror=alert(1)>"}],
    })
    assert "AI 评分 5" in feedback
    assert "&lt;img" in feedback and "<img" not in feedback


def test_negative_agent_preview_keeps_failure_evidence_separate():
    html = render_training_sample("agent_negative", {
        "messages": [
            {"role": "user", "content": "计算"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {
                "name": "calculator", "arguments": '{"expression":"6+7"}'}}]},
            {"role": "tool", "tool_call_id": "x", "content": "12"},
        ],
        "failure": "calculator_result_mismatch",
        "failure_step": 2,
        "source_location": "logs/episode-1.jsonl",
        "evidence": {"observed": "<bad>", "expected": "13"},
    })
    assert "Agent 失败轨迹" in html
    assert "calculator_result_mismatch" in html and "第 3 条消息（索引 2）" in html
    assert 'data-failed="true"' in html and "失败截断点" in html
    assert "&lt;bad&gt;" in html and "<bad>" not in html


def test_multiturn_preview_keeps_context_and_tool_result_with_its_turn():
    messages = [{"role": "system", "content": "只用记录事实"}]
    for index in range(1, 5):
        messages.extend(({"role": "user", "content": f"问题{index}"},
                         {"role": "assistant", "content": f"回答{index}"}))
    html = render_training_sample("multiturn", {"messages": messages, "turn_reviews": [
        {"turn": index, "judge": {"keep": True}} for index in range(1, 5)
    ]})
    assert "上下文指令" in html and "第 04 轮对话" in html
    assert "展开后续 1 轮对话" in html and "4 轮模型评估记录" in html
    assert html.count("问题4") == 1 and html.count("回答4") == 1

    anthro = render_training_sample("agent", {"messages": [
        {"role": "user", "content": "查版本"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1",
                                             "name": "search_docs", "input": {"q": "version"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1",
                                      "content": "v2"}]},
        {"role": "assistant", "content": "版本为 v2"},
    ]})
    assert "3 个步骤" in anthro and "1 轮用户交互" in anthro
    assert anthro.count("search_docs") == 3  # step title, call, and matched result

    verified = render_training_sample("agent", {"messages": [
        {"role": "user", "content": "2+2"},
        {"role": "assistant", "content": "", "toolCalls": [{"id": "x", "name": "calculator",
                                                   "input": {"expression": "2+2"}}]},
        {"role": "tool", "toolCallId": "x", "content": "4"},
        {"role": "assistant", "content": "4"},
    ], "verification": {"method": "bounded_arithmetic_replay",
                       "verified_call_ids": ["x"], "pruned_call_ids": []}})
    assert "本地算术重放" in verified and "1 次调用已核对" in verified


def test_agent_preview_names_snapshot_replay_without_claiming_arithmetic():
    html = render_training_sample("agent", {
        "messages": [
            {"role": "user", "content": "读取工单状态"},
            {"role": "assistant", "content": "", "toolCalls": [
                {"id": "read-1", "name": "json_pointer", "arguments": {
                    "snapshot_id": "ticket-1", "pointer": "/status"}},
            ]},
            {"role": "tool", "toolCallId": "read-1", "content": "已完成"},
            {"role": "assistant", "content": "工单已完成"},
        ],
        "verification": {"method": "snapshot_json_pointer_replay",
                         "verified_call_ids": ["read-1"],
                         "pruned_call_ids": [], "verified_turns": [1]},
    })
    assert "JSON 快照重放" in html and "本地算术重放" not in html
    assert "1 次调用已核对" in html and "1 轮回答已核对" in html


def test_agent_preview_distinguishes_verified_and_unverified_tool_steps():
    html = render_training_sample("agent", {
        "messages": [
            {"role": "user", "content": "查余额后记账"},
            {"role": "assistant", "toolCalls": [{"id": "read-1", "name": "ledger_read",
                                                   "arguments": {"account": "demo"}}]},
            {"role": "tool", "toolCallId": "read-1", "content": "100"},
            {"role": "assistant", "toolCalls": [{"id": "write-2", "name": "ledger_write",
                                                   "arguments": {"amount": 20}}]},
            {"role": "tool", "toolCallId": "write-2", "content": "120"},
        ],
        "verification": {"method": "isolated_docker_ledger_replay",
                         "verified_call_ids": ["read-1"], "pruned_call_ids": []},
    })
    assert "隔离容器重放" in html
    assert html.count('data-kind="tool" data-failed="false" data-verified="true"') == 1
    assert html.count('data-kind="tool" data-failed="false" data-verified="false"') == 1
    assert html.count("本地重放已核对") == 1
    assert html.index("ledger_read") < html.index("ledger_write")


def test_agent_preview_flags_a_missing_result_without_inventing_one():
    html = render_training_sample("agent", {"messages": [
        {"role": "user", "content": "查询"},
        {"role": "assistant", "toolCalls": [{"id": "lost-1", "name": "lookup"}]},
    ]})
    assert "未记录工具返回" in html
    assert "调用与返回" not in html
    assert html.count("lookup") == 2  # step heading and call body
