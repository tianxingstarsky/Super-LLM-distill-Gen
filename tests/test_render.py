"""渲染层测试：Markdown 渲染、气泡结构、不可信内容转义。"""
from __future__ import annotations


def test_markdown_rendered_in_bubbles():
    from lib.render import _render_message

    html = _render_message({"role": "assistant", "content": "**粗体**\n\n- 一\n- 二\n\n```py\nprint(1)\n```"})
    assert "bub-assistant" in html and 'class="md"' in html
    assert "<strong>粗体</strong>" in html          # Markdown 生效
    assert "<li>一</li>" in html and "<li>二</li>" in html
    assert "print(1)" in html and "<pre>" in html


def test_untrusted_html_is_escaped():
    from lib.render import _render_message

    html = _render_message({"role": "user", "content": "<script>alert(1)</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_bubble_roles_and_thinking():
    from lib.render import _render_message

    user = _render_message({"role": "user", "content": "hi"})
    assistant = _render_message({"role": "assistant", "content": "ok", "reasoning_content": "想一下"})
    tool_err = _render_message({"role": "tool", "content": "boom", "isError": True})
    system = _render_message({"role": "system", "content": "sys"})
    assert "bub-user" in user
    assert "bub-assistant" in assistant and "bub-think" in assistant and "🧠 思考" in assistant
    assert "bub-tool" in tool_err and "err" in tool_err and "⚠ 执行失败" in tool_err
    assert "bub-system" in system


def test_agent_preview_reads_openai_tool_calls_without_leaking_markup():
    from lib.render import _render_message

    html = _render_message({"role": "assistant", "content": "", "tool_calls": [{
        "id": "call-1", "type": "function", "function": {
            "name": "search_manual", "arguments": '{"query":"设备维护","note":"<script>bad</script>"}',
        },
    }]})
    assert "工具调用" in html and "search_manual" in html and "设备维护" in html
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_multimodal_messages_render_as_readable_safe_turns():
    from lib.render import _render_message

    user = _render_message({"role": "user", "content": [
        {"type": "text", "text": "请解释这张图"},
        {"type": "image_url", "image_url": {"url": "https://example.invalid/private.png"}},
    ]})
    assistant = _render_message({"role": "assistant", "content": [
        {"type": "reasoning", "text": "先识别图中的设备"},
        {"type": "text", "text": "图片展示的是**控制面板**。"},
    ]})
    assert "请解释这张图" in user and "［图像内容］" in user
    assert "example.invalid" not in user
    assert "先识别图中的设备" in assistant and "控制面板" in assistant


def test_preview_html_contains_bubbles(tmp_path):
    from lib.render import render_preview_html

    out = render_preview_html([{"id": "s1", "model": "m", "finish_reason": "stop",
                                "messages": [{"role": "user", "content": "**q**"},
                                             {"role": "assistant", "content": "a"}]}],
                              out_path=tmp_path / "p.html")
    text = out.read_text(encoding="utf-8")
    assert "bubbles" in text and "<strong>q</strong>" in text


def test_anthropic_content_blocks_keep_tool_sequence_and_hide_media_sources():
    from lib.render import _render_message

    assistant = _render_message({"role": "assistant", "content": [
        {"type": "thinking", "thinking": "先确认目录"},
        {"type": "text", "text": "准备查询"},
        {"type": "tool_use", "id": "call-42", "name": "list_files", "input": {"path": "."}},
        {"type": "text", "text": "等待结果"},
    ]})
    tool_result = _render_message({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call-42", "is_error": True,
         "content": [{"type": "text", "text": "权限不足"}]},
    ]})
    media = _render_message({"role": "user", "content": [
        {"type": "input_image", "image_url": "https://example.invalid/private.png"},
        {"type": "input_audio", "audio_url": "data:audio/wav;base64,SECRET"},
        {"type": "input_file", "file_data": "data:application/pdf;base64,SECRET"},
    ]})

    assert assistant.index("准备查询") < assistant.index("list_files") < assistant.index("等待结果")
    assert "先确认目录" in assistant and '<details class="think">' in assistant
    assert "call-42" in assistant and "call-42" in tool_result
    assert "权限不足" in tool_result and "执行失败" in tool_result
    assert "［图像内容］" in media and "［音频内容］" in media and "［文件内容］" in media
    assert "example.invalid" not in media and "SECRET" not in media


def test_json_encoded_tool_calls_and_long_result_are_readable_and_safe():
    from lib.render import _render_message

    assistant = _render_message({"role": "assistant", "tool_calls": '[{"id":"call-7","function":'
                                 '{"name":"calc","arguments":"{\\"expression\\":\\"2+2\\"}"}}]'})
    result = _render_message({"role": "tool", "tool_call_id": "call-7", "name": "calc",
                              "content": "<script>bad</script>" + "x" * 1300})
    assert "calc" in assistant and "expression" in assistant and "2+2" in assistant
    assert "call-7" in assistant and "call-7" in result
    assert "展开工具输出" in result and "<details" in result
    assert "<script>" not in result and "&lt;script&gt;" in result


def test_developer_role_and_long_trajectory_preview_keep_turns_once(tmp_path):
    from lib.render import _render_message, render_preview_html

    instruction = _render_message({"role": "developer", "content": "只回答问题"})
    assert "开发者指令" in instruction and "bub-system" in instruction

    messages = [{"role": "user", "content": f"turn-{index}"} for index in range(65)]
    out = render_preview_html([{"id": "long", "messages": messages}], out_path=tmp_path / "long.html")
    text = out.read_text(encoding="utf-8")
    assert "<p>turn-0</p>" in text and "<p>turn-64</p>" in text and "<p>turn-2</p>" not in text
    assert text.count("turn-64") == 1
    assert "已省略中间 5 条消息" in text


def test_preview_escapes_report_metadata(tmp_path):
    from lib.render import render_preview_html

    out = render_preview_html([], report={"counts": {"<script>alert(1)</script>": 1}},
                              out_path=tmp_path / "metadata.html")
    text = out.read_text(encoding="utf-8")
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_trajectory_labels_tool_result_from_matching_call_without_changing_source():
    from lib.render import render_message_sequence

    messages = [
        {"role": "assistant", "content": "", "toolCalls": [
            {"id": "t1", "name": "search_docs", "input": {"q": "版本"}},
        ]},
        {"role": "tool", "toolCallId": "t1", "content": "找到版本信息"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "calculator",
                                           "input": {"expression": "2+2"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2",
                                      "content": "4"}]},
    ]
    rendered = render_message_sequence(messages)
    assert rendered.count("search_docs") == 2
    assert rendered.count("calculator") == 2
    assert "toolName" not in messages[1] and "name" not in messages[3]["content"][0]


def test_nested_parts_and_tagged_thinking_render_without_embedding_payload():
    from lib.render import _render_message

    user = _render_message({"role": "user", "content": {"parts": [
        {"text": "请看文件"},
        {"inline_data": {"mime_type": "image/png", "data": "SECRET_BASE64"}},
    ]}})
    assistant = _render_message({"role": "assistant", "content": "<think>先核算 2+2</think>结果是 **4**。"})
    assert "请看文件" in user and "［图像内容］" in user
    assert "SECRET_BASE64" not in user
    assert "先核算 2+2" in assistant and '<details class="think">' in assistant
    assert "<strong>4</strong>" in assistant and "&lt;think&gt;" not in assistant
