import pytest
"""服务托管测试：模式注册表、活跃服务、模式一致性。"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_modes_and_active_services():
    from lib import services as S

    # 单进程模式：只有 console（全景承诺）
    assert S.MODES["light"] == ["console"]
    # share = console + 预览；collab = 全栈（协作审核才需要 ES/Redis/Argilla）
    assert set(S.MODES["share"]) - {"console", "preview"} == set()
    assert set(S.MODES["collab"]) == {"console", "preview", "redis", "elasticsearch", "argilla"}
    # 内存提示存在且 collab 显著高于 light
    assert "300MB" in S.MEMORY_HINT["light"]
    assert "2.5GB" in S.MEMORY_HINT["collab"]


def test_mode_persistence(tmp_path, monkeypatch):
    from lib import services as S

    monkeypatch.setattr(S, "MODE_PATH", tmp_path / "m.json")
    assert S.load_mode() == "light"  # 默认单进程
    S.set_mode("collab")
    assert S.load_mode() == "collab"
    assert {s["name"] for s in S.active_services()} == {"console", "preview", "redis", "elasticsearch", "argilla"}
    with pytest.raises(ValueError):
        S.set_mode("nope")


def test_service_registry_complete():
    from lib import services as S

    names = {s["name"] for s in S.SERVICES}
    assert names == {"redis", "elasticsearch", "argilla", "preview", "console"}
    for s in S.SERVICES:
        assert s["port"] and s["exe"] and s["cwd"]
