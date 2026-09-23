"""The backend overview must display real counts without leaking key fragments."""
from __future__ import annotations

from lib.presentation.streamlit import backend_page


def test_backend_overview_uses_inventory_but_never_renders_masked_key(monkeypatch):
    fragments: list[str] = []
    monkeypatch.setattr(backend_page.st, "html", fragments.append)
    rows = [
        {"name": "<private>", "base_url": "http://127.0.0.1:11434/v1",
         "models": ["qwen:7b"], "roles": ["generation"], "is_default": True,
         "api_key": {"source": "configs/backends.local.yaml", "status": "sk-***3456"}},
        {"name": "empty", "base_url": "http://127.0.0.1:1234/v1",
         "models": [], "roles": [], "is_default": False,
         "api_key": {"source": "未配置", "status": "缺失"}},
    ]
    info = {"backends": rows, "roles": {"generation": {"backend": "<private>", "model": "qwen:7b"}},
            "spent": 1.25, "budget": {"max_total_usd": 5}}
    backend_page._summary(info)
    backend_page._endpoints(rows)
    markup = "\n".join(fragments)
    assert "已登记端点</small><strong>2" in markup
    assert "密钥已配置</small><strong>1" in markup
    assert "$1.25" in markup
    assert "密钥未配置" in markup
    assert "&lt;private&gt;" in markup
    assert "sk-" not in markup and "3456" not in markup and "<private>" not in markup
