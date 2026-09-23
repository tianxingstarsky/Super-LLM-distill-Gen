"""The data library must stay within a workspace and avoid unbounded reads."""
from __future__ import annotations

from dataclasses import replace

import pytest

from lib.application.asset_catalog_service import AssetCatalogApplication
from lib.domain.dataset_assets import output_category
from lib.infrastructure.asset_catalog_driver import FilesystemAssetCatalogDriver


def _workspace(tmp_path, monkeypatch):
    from lib import workspace

    monkeypatch.setattr(workspace, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(workspace, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(workspace, "CURRENT_PATH", tmp_path / "current.json")
    source = tmp_path / "source"
    source.mkdir()
    name = workspace.add_folder(source)
    return workspace, source, name


def test_catalog_prioritizes_sources_and_hides_audit_files_from_common_view(tmp_path, monkeypatch):
    workspace, source, name = _workspace(tmp_path, monkeypatch)
    (source / "guide.txt").write_text("先断电，再检查。", encoding="utf-8")
    artifacts = workspace.out(name) / "workflows" / ("a" * 32) / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "cpt.jsonl").write_text('{"text":"已核验语料"}\n', encoding="utf-8")
    (artifacts / "cpt.records.json").write_text("{}", encoding="utf-8")

    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    inventory = app.inventory(name)
    assert not inventory.truncated
    common = app.select(inventory, "常用文件")
    assert [asset.name for asset in common] == ["guide.txt", "cpt.jsonl"]
    assert any(asset.name == "cpt.records.json" for asset in app.select(inventory, "全部文件"))
    assert app.categories(inventory)["语料"] == 1
    assert "先断电" in app.excerpt(name, common[0])[0]
    assert app.download(name, common[0]).decode("utf-8") == "先断电，再检查。"


def test_catalog_rejects_changed_and_forged_paths(tmp_path, monkeypatch):
    _, source, name = _workspace(tmp_path, monkeypatch)
    path = source / "guide.txt"
    path.write_text("original", encoding="utf-8")
    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    asset = app.inventory(name).assets[0]
    path.write_text("modified content", encoding="utf-8")
    with pytest.raises(ValueError, match="asset_changed_since_listing"):
        app.download(name, asset)
    forged = replace(asset, id="source/../outside.txt", relative_path="../outside.txt")
    with pytest.raises(ValueError, match="invalid_asset_id"):
        app.excerpt(name, forged)


def test_catalog_ignores_linked_output(tmp_path, monkeypatch):
    workspace, _, name = _workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sft.jsonl").write_text('{"messages":[]}\n', encoding="utf-8")
    output = workspace.out(name)
    output.mkdir(parents=True)
    linked = output / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create a directory link")
    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    inventory = app.inventory(name)
    assert "sft.jsonl" not in [asset.name for asset in inventory.assets]


def test_catalog_limits_direct_download(tmp_path, monkeypatch):
    workspace, _, name = _workspace(tmp_path, monkeypatch)
    output = workspace.out(name)
    output.mkdir(parents=True)
    path = output / "manual.txt"
    path.write_text("longer than limit", encoding="utf-8")
    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    inventory = app.inventory(name)
    asset = next(asset for asset in inventory.assets if asset.name == "manual.txt")
    monkeypatch.setattr("lib.infrastructure.asset_catalog_driver.DIRECT_DOWNLOAD_LIMIT_BYTES", 5)
    with pytest.raises(ValueError, match="asset_too_large_for_direct_download"):
        app.download(name, asset)


@pytest.mark.parametrize("filename,expected", [
    ("workflows/x/artifacts/cpt.records.json", "报告与状态"),
    ("workflows/x/artifacts/cpt.jsonl", "语料"),
    ("workflows/x/artifacts/orpo.jsonl", "其他偏好数据"),
    ("workflows/x/inputs/0000.txt", "输入快照"),
])
def test_output_classification(filename: str, expected: str):
    assert output_category(filename) == expected
