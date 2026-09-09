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


def test_preview_html_contains_bubbles(tmp_path):
    from lib.render import render_preview_html

    out = render_preview_html([{"id": "s1", "model": "m", "finish_reason": "stop",
                                "messages": [{"role": "user", "content": "**q**"},
                                             {"role": "assistant", "content": "a"}]}],
                              out_path=tmp_path / "p.html")
    text = out.read_text(encoding="utf-8")
    assert "bubbles" in text and "<strong>q</strong>" in text
