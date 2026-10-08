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


@pytest.mark.parametrize("suffix,mode", [
    (".md", "文档资料"), (".txt", "文档资料"), (".pdf", "文档资料"), (".docx", "文档资料"),
    (".PNG", "文档资料"), (".jpg", "文档资料"), (".JPEG", "文档资料"), (".webp", "文档资料"),
    (".json", "Agent 上下文"), (".jsonl", "Agent 上下文"),
])
def test_generation_source_resolves_existing_version_and_maps_mode(tmp_path, monkeypatch, suffix, mode):
    _, source, name = _workspace(tmp_path, monkeypatch)
    path = source / ("saved" + suffix)
    path.write_bytes(b"original source")
    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    asset = next(asset for asset in app.inventory(name).assets if asset.name == path.name)
    assert app.generation_source(name, asset) == {"path": str(path.resolve()), "source_mode": mode}


@pytest.mark.parametrize("suffix", [".txt", ".PNG"])
def test_generation_handoff_rejects_stale_forged_output_and_unsupported_assets(tmp_path, monkeypatch, suffix):
    from lib.domain.dataset_assets import Asset
    workspace, source, name = _workspace(tmp_path, monkeypatch)
    path = source / ("saved" + suffix)
    path.write_text("source", encoding="utf-8")
    output = workspace.out(name)
    output.mkdir(parents=True)
    (output / "sft.jsonl").write_text('{}\n', encoding="utf-8")
    driver = FilesystemAssetCatalogDriver()
    app = AssetCatalogApplication(driver)
    assets = app.inventory(name).assets
    asset = next(row for row in assets if row.origin == "source")
    generated = next(row for row in assets if row.origin == "output")
    unsupported = Asset("source/saved.csv", "source", "saved.csv", "来源文件", 0, 0)
    for invalid in (generated, unsupported):
        with pytest.raises(ValueError, match="asset_not_generation_source"):
            app.generation_source(name, invalid)
        with pytest.raises(ValueError, match="asset_not_generation_source"):
            driver.source_path_for_generation(name, invalid)
    forged = replace(asset, id="source/../outside.txt", relative_path="../outside.txt")
    with pytest.raises(ValueError, match="invalid_asset_id"):
        app.generation_source(name, forged)
    path.write_text("changed source", encoding="utf-8")
    with pytest.raises(ValueError, match="asset_changed_since_listing"):
        app.generation_source(name, asset)


def test_generated_images_cannot_be_reused_as_library_sources():
    from lib.domain.dataset_assets import Asset, generation_source_mode

    generated = Asset("output/scan.png", "output", "scan.png", "报告与状态", 10, 1)
    with pytest.raises(ValueError, match="asset_not_generation_source"):
        generation_source_mode(generated)


def test_generation_source_rejects_a_file_replaced_with_link(tmp_path, monkeypatch):
    _, source, name = _workspace(tmp_path, monkeypatch)
    path = source / "saved.txt"
    path.write_text("source", encoding="utf-8")
    app = AssetCatalogApplication(FilesystemAssetCatalogDriver())
    asset = app.inventory(name).assets[0]
    outside = tmp_path / "private.txt"
    outside.write_text("outside", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create a file link")
    with pytest.raises(ValueError, match="linked_asset_path"):
        app.generation_source(name, asset)


def test_directory_selection_uses_current_rows_and_preserves_file_identity():
    from lib.domain.dataset_assets import Asset
    from lib.presentation.streamlit.asset_catalog_page import _selection_asset, _table_identity
    first = Asset("source/first.txt", "source", "first.txt", "来源文件", 10, 1)
    second = Asset("source/second.txt", "source", "second.txt", "来源文件", 11, 2)
    rows = [first, second]
    assert _selection_asset(rows, [1]) == second
    for expired in ([2], [-1], [True], [0, 1], [], None):
        assert _selection_asset(rows, expired, second.id) == second
    assert _selection_asset([first], [1], second.id) == first
    assert _selection_asset([], [0], first.id) is None
    context = ("default", "来源文件", "", 1)
    identity = _table_identity(rows, context)
    assert identity != _table_identity(list(reversed(rows)), context)
    assert identity != _table_identity([first, replace(second, mtime_ns=3)], context)
    assert identity != _table_identity(rows, ("default", "来源文件", "second", 1))
    assert identity != _table_identity(rows, ("default", "来源文件", "", 2))
