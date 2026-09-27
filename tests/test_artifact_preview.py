"""Training previews keep conversational structure without executing sample HTML."""
from lib.presentation.streamlit.artifact_preview import render_training_sample


def test_math_preview_distinguishes_consistent_conflicting_and_missing_evidence():
    from copy import deepcopy
    from lib.domain.math_tasks import build_gsm8k
    from lib.presentation.streamlit.i18n import translate_markup
    sample = build_gsm8k("工作流", 0, version=2)
    row = {"question": sample["question"], "answer": sample["answer"],
           "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
           "calculation_steps": sample["_steps"], "generator_version": 2}
    preview = render_training_sample("gsm8k", row)
    assert 'data-status="consistent"' in preview and '算术与步骤一致' in preview
    assert '题意与文字解释未独立核验' in preview
    assert 'Arithmetic and steps match' in translate_markup(preview)
    assert '工作流' in translate_markup(preview)
    damaged = deepcopy(row)
    damaged["calculation_steps"][0]["result"] += 1
    preview = render_training_sample("gsm8k", damaged)
    assert 'data-status="conflict"' in preview and '算术证据存在冲突' in preview
    assert 'Arithmetic evidence conflicts' in translate_markup(preview)
    preview = render_training_sample("gsm8k", {"question": row["question"], "answer": row["answer"]})
    assert 'data-status="missing"' in preview and '未附算术校验证据' in preview
    assert 'Arithmetic evidence not included' in translate_markup(preview)


def test_multistep_arithmetic_has_numbered_calculations_and_final_answer():
    preview = render_training_sample("gsm8k", {"question": "How many items?", "answer":
        "First count: <<2 * 3=6>>.\nThen add: <<2 * 3 + 4=10>>.\n#### 10"})
    assert '<b>01</b>' in preview and '<b>02</b>' in preview
    assert '<code>2 * 3=6</code>' in preview
    assert "最终答案" in preview and "计算步骤" in preview
    unsafe = render_training_sample("gsm8k", {"question": "<script>x</script>", "answer":
        "<script>x</script> <<2 + 2=4>>\n#### 4"})
    assert '<script>' not in unsafe and '&lt;script&gt;' in unsafe


def test_nonstandard_math_answer_keeps_full_original_content():
    preview = render_training_sample("gsm8k", {"question": "Question", "answer": "Legacy answer END"})
    assert "Legacy answer END" in preview and "计算与答案" in preview


def test_english_ui_preserves_recorded_math_and_reasoning_step_text():
    from lib.presentation.streamlit.i18n import translate_markup
    math = render_training_sample("gsm8k", {"question": "题目", "answer": "工作流 <<2 + 2=4>>\n#### 4"})
    rationale = render_training_sample("cot", {"reasoning": ["工作流", "执行中"], "answer": "答案"})
    assert "Calculation steps" in translate_markup(math)
    assert "工作流" in translate_markup(math)
    assert "工作流" in translate_markup(rationale) and "执行中" in translate_markup(rationale)


def test_long_messages_collapse_without_losing_or_executing_content():
    content = "Long answer line.\n" * 150 + "END <script>alert(1)</script>"
    preview = render_training_sample("multiturn", {"messages": [
        {"role": "user", "content": "Explain."}, {"role": "assistant", "content": content},
    ]})
    assert '<details class="df-message-long"><summary>' in preview
    assert "展开完整内容" in preview
    assert "END &lt;script&gt;alert(1)&lt;/script&gt;" in preview
    assert "<script>" not in preview
    assert 'class="df-message-full" tabindex="0" role="region"' in preview


def test_long_agent_result_preserves_tool_pair_and_failure_status():
    preview = render_training_sample("agent_negative", {"messages": [
        {"role": "user", "content": "Look up the record."},
        {"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": "lookup", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "observed data " * 200 + "RESULT_END"},
    ], "failure": "tool_replay_mismatch", "failure_step": 2})
    assert 'data-failed="true"' in preview and "失败截断点" in preview
    assert "RESULT_END" in preview
    assert '<details class="df-message-long">' in preview
    assert "lookup" in preview


def test_long_message_controls_translate_without_translating_recorded_text():
    from lib.presentation.streamlit.i18n import translate_markup
    text = "完整消息中的中文训练正文。" * 180
    preview = translate_markup(render_training_sample("sft", {"messages": [
        {"role": "user", "content": "Explain."}, {"role": "assistant", "content": text},
    ]}), "en")
    assert "Show full content" in preview and 'aria-label="Full message"' in preview
    assert text in preview and text[:180] in preview
    assert "展开完整内容" not in preview


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


def test_agent_preview_does_not_pair_unrelated_or_duplicate_results():
    messages = [
        {"role": "assistant", "toolCalls": [{"id": "x", "name": "calculator"}]},
        {"role": "tool", "toolCallId": "other", "content": "UNRELATED"},
    ]
    preview = render_training_sample("agent", {"messages": messages,
                "verification": {"verified_call_ids": ["x"]}})
    assert "未记录工具返回" in preview and "未配对的结果" in preview
    assert 'data-failed="false" data-verified="true"' not in preview
    messages[1] = {"role": "tool", "toolCallId": "x", "content": "FIRST"}
    messages.append({"role": "tool", "toolCallId": "x", "content": "DUPLICATE"})
    preview = render_training_sample("agent", {"messages": messages})
    assert "2 个步骤" in preview and "未配对的结果" in preview
    assert preview.count("FIRST") == 1 and preview.count("DUPLICATE") == 1


def test_parallel_calls_report_missing_results_and_match_anthropic_ids():
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "read"},
            {"type": "tool_use", "id": "b", "name": "write"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "B"}]},
    ]
    preview = render_training_sample("agent", {"messages": messages,
                    "verification": {"verified_call_ids": ["a", "b"]}})
    assert "部分调用缺少返回" in preview and 'data-failed="false" data-verified="true"' not in preview
    messages[1]["content"].append({"type": "tool_result", "tool_use_id": "a", "content": "A"})
    preview = render_training_sample("agent", {"messages": messages,
                    "verification": {"verified_call_ids": ["a", "b"]}})
    assert "1 个步骤" in preview and 'data-failed="false" data-verified="true"' in preview


def test_mixed_tool_result_and_new_user_text_starts_a_dialogue_turn():
    preview = render_training_sample("multiturn", {"messages": [
        {"role": "user", "content": "First task"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "read"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "Result"},
            {"type": "text", "text": "NEW_USER_REQUEST"}]},
        {"role": "assistant", "content": "Second response"},
    ]})
    assert "2 轮" in preview and "第 02 轮对话" in preview
    assert preview.count("NEW_USER_REQUEST") == 1


def test_long_tool_result_keeps_tail_and_localizes_only_fallback_label():
    from lib.presentation.streamlit.i18n import translate_markup
    preview = render_training_sample("agent", {"messages": [
        {"role": "tool", "tool_call_id": "工作流", "content": "x" * 21000 + "TOOL_END"},
        {"role": "tool", "name": "工作流", "content": "Content"},
    ]})
    assert "TOOL_END" in preview
    english = translate_markup(preview, "en")
    assert '<span>Tool result</span>' in english
    assert '<span data-user-content>工作流</span>' in english
    assert '<span data-user-content style="overflow-wrap:anywhere">工作流</span>' in english


def test_long_corpus_and_evidence_are_collapsible_complete_and_not_translated():
    from lib.presentation.streamlit.i18n import translate_markup
    text = "工作流 <script>alert(1)</script>\n" * 300 + "CORPUS_END"
    preview = render_training_sample("cpt", {"text": text})
    assert '<details class="df-message-long">' in preview
    assert 'tabindex="0" role="region" aria-label="完整内容"' in preview
    assert "CORPUS_END" in preview
    assert "<script>" not in preview and "&lt;script&gt;" in preview
    english = translate_markup(preview, "en")
    assert "Show full content" in english and 'aria-label="Full content"' in english
    assert "工作流" in english and "CORPUS_END" in english
    short = render_training_sample("cpt", {"text": "short corpus"})
    assert '<details class="df-message-long">' not in short
    evidence = render_training_sample("agent_negative", {
        "messages": [], "evidence": {"record": "x" * 7000 + "EVIDENCE_END"}})
    assert "EVIDENCE_END" in evidence
    structured = render_training_sample("custom", {"record": "x" * 11000 + "JSON_END"})
    assert "JSON_END" in structured


def test_agent_preview_flags_a_missing_result_without_inventing_one():
    html = render_training_sample("agent", {"messages": [
        {"role": "user", "content": "查询"},
        {"role": "assistant", "toolCalls": [{"id": "lost-1", "name": "lookup"}]},
    ]})
    assert "未记录工具返回" in html
    assert "调用与返回" not in html
    assert html.count("lookup") == 2  # step heading and call body


def _long_trace():
    messages = [{"role": "user", "content": "TASK_START"}]
    for index in range(8):
        messages.extend([
            {"role": "assistant", "tool_calls": [{"id": f"call-{index}",
             "function": {"name": "工作流", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": f"OBSERVATION_{index}"},
        ])
    messages.append({"role": "assistant", "content": "FINAL_ANSWER"})
    return messages


def test_long_trace_collapses_middle_but_keeps_failure_and_final_open():
    import re
    from copy import deepcopy
    messages = _long_trace()
    original = deepcopy(messages)
    preview = render_training_sample('agent_negative', {
        'messages': messages, 'failure_step': 10, 'failure': 'recorded_tool_error'})
    states = re.findall(r'<details class="df-artifact-trace-card"( open)?>', preview)
    assert len(states) == 10
    assert [i for i, state in enumerate(states) if state] == [0, 5, 9]
    assert preview.count(' data-failed="true" data-verified=') == 1
    for index in range(8):
        assert preview.count(f'OBSERVATION_{index}') == 1
    assert 'FINAL_ANSWER' in preview and messages == original


def test_long_trace_controls_translate_but_tool_names_do_not():
    from lib.presentation.streamlit.i18n import translate_markup
    preview = translate_markup(render_training_sample('agent', {'messages': _long_trace()}), 'en')
    assert 'Middle steps are collapsed in long traces.' in preview
    assert '长轨迹默认' not in preview
    assert '<strong data-user-content>工作流</strong>' in preview
    assert '<summary class="df-artifact-trace-head">' in preview


def test_boolean_failure_index_is_not_a_message_location():
    preview = render_training_sample('agent_negative', {
        'messages': _long_trace(), 'failure_step': True, 'failure': 'recorded_tool_error'})
    assert ' data-failed="true" data-verified=' not in preview
    assert '第 2 条消息（索引 True）' not in preview


def test_all_known_failure_titles_translate_in_english():
    from lib.presentation.streamlit.artifact_preview import _FAILURE_LABELS
    from lib.presentation.streamlit.i18n import translate_markup
    for reason, title in _FAILURE_LABELS.items():
        preview = translate_markup(render_training_sample('agent_negative', {
            'messages': [], 'failure': reason}), 'en')
        assert title not in preview
        assert reason in preview
