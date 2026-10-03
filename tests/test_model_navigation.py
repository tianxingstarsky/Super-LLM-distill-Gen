"""Model administration must not divert a workflow into a separate main page."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

WEBAPP = Path(__file__).resolve().parents[1] / "lib" / "webapp.py"


def test_model_service_is_not_a_primary_navigation_item():
    ui = AppTest.from_file(WEBAPP, default_timeout=30).run()
    assert not ui.exception
    assert not [button for button in ui.sidebar.button
                if button.key == "nav-button:模型与密钥"]
    ui.sidebar.button(key="nav-button:系统设置").click().run()
    assert not ui.exception
    assert any(item.label == "服务连接与预算（高级）" for item in ui.expander)


def test_legacy_model_service_route_opens_settings():
    ui = AppTest.from_file(WEBAPP, default_timeout=30).run()
    ui.session_state["nav"] = "模型与密钥"
    ui.run()
    assert not ui.exception
    assert ui.session_state["nav"] == "系统设置"
