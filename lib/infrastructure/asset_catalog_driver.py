"""Bounded, link-aware filesystem adapter for the data library."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

from lib import workspace
from lib.domain.dataset_assets import (
    Asset, AssetInventory, DIRECT_DOWNLOAD_LIMIT_BYTES, OUTPUT_SUFFIXES, output_category,
)


MAX_SOURCE_ASSETS = 5000
MAX_OUTPUT_ASSETS = 5000
MAX_SCAN_ENTRIES = 15000


def _linked_ancestor(path: Path) -> bool:
    return any(workspace.is_linked(parent) for parent in (path, *path.parents))


def _asset(origin: str, base: Path, path: Path) -> Asset:
    relative = "/".join(path.relative_to(base).parts)
    stat = path.stat(follow_symlinks=False)
    return Asset(
        id=f"{origin}/{relative}", origin=origin, relative_path=relative,
        category="来源文件" if origin == "source" else output_category(relative),
        size=stat.st_size, mtime_ns=stat.st_mtime_ns,
    )


class FilesystemAssetCatalogDriver:
    def inventory(self, workspace_id: str) -> AssetInventory:
        source_root = workspace.folder(workspace_id)
        output_root = workspace.out(workspace_id)
        assets: list[Asset] = []
        sources = workspace.source_files(workspace_id, limit=MAX_SOURCE_ASSETS + 1)
        source_truncated = len(sources) > MAX_SOURCE_ASSETS
        for path in sources[:MAX_SOURCE_ASSETS]:
            try:
                if path.is_file() and not _linked_ancestor(path):
                    assets.append(_asset("source", source_root, path))
            except OSError:
                continue

        output_truncated = False
        if output_root.is_dir() and not _linked_ancestor(output_root):
            seen = 0
            output_count = 0
            for current, directories, files in os.walk(output_root, followlinks=False):
                parent = Path(current)
                directories[:] = sorted(directory for directory in directories
                                        if not workspace.is_linked(parent / directory))
                if "review_batches" in parent.parts:
                    directories.clear()
                    continue
                for name in sorted(files):
                    seen += 1
                    if seen > MAX_SCAN_ENTRIES:
                        output_truncated = True
                        break
                    path = parent / name
                    if path.suffix.lower() not in OUTPUT_SUFFIXES or workspace.is_linked(path):
                        continue
                    try:
                        assets.append(_asset("output", output_root, path))
                    except OSError:
                        continue
                    output_count += 1
                    if output_count >= MAX_OUTPUT_ASSETS:
                        output_truncated = True
                        break
                if output_truncated:
                    break
        return AssetInventory(tuple(assets), source_truncated or output_truncated)

    def _resolve(self, workspace_id: str, asset: Asset) -> Path:
        if asset.origin not in {"source", "output"}:
            raise ValueError("invalid_asset_origin")
        relative = asset.relative_path
        parts = PurePosixPath(relative).parts
        if (not parts or relative.startswith("/") or "\\" in relative or ":" in relative
                or any(part in {"", ".", ".."} for part in parts)
                or asset.id != f"{asset.origin}/{relative}"):
            raise ValueError("invalid_asset_id")
        base = workspace.folder(workspace_id) if asset.origin == "source" else workspace.out(workspace_id)
        if _linked_ancestor(base):
            raise ValueError("linked_asset_root")
        path = base
        for part in parts:
            path = path / part
            if workspace.is_linked(path):
                raise ValueError("linked_asset_path")
        if not path.is_file() or not path.resolve(strict=True).is_relative_to(base.resolve(strict=True)):
            raise ValueError("asset_outside_workspace")
        if asset.origin == "source" and path.is_relative_to(workspace.out(workspace_id)):
            raise ValueError("output_not_source")
        info = path.stat(follow_symlinks=False)
        if (info.st_size, info.st_mtime_ns) != (asset.size, asset.mtime_ns):
            raise ValueError("asset_changed_since_listing")
        return path

    def excerpt(self, workspace_id: str, asset: Asset) -> tuple[str, str]:
        path = self._resolve(workspace_id, asset)
        suffix = asset.suffix
        if suffix not in {".jsonl", ".json", ".txt", ".md", ".csv", ".html"}:
            return "", "当前文件类型不提供正文预览，可使用下载按钮查看。"
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            excerpt = handle.read(3000)
        if not excerpt:
            return "", "文件为空。"
        if suffix == ".jsonl":
            first = excerpt.splitlines()[0]
            try:
                excerpt = json.dumps(json.loads(first), ensure_ascii=False, indent=2)[:2000]
            except ValueError:
                excerpt = first[:2000]
            return excerpt, "仅展示第一条记录的摘录，未在这里校验全文件。"
        if suffix == ".json":
            try:
                excerpt = json.dumps(json.loads(excerpt), ensure_ascii=False, indent=2)[:2000]
            except ValueError:
                pass
        return excerpt[:2000], "仅展示文件开头的摘录，未在这里校验全文件。"

    def download(self, workspace_id: str, asset: Asset) -> bytes:
        path = self._resolve(workspace_id, asset)
        if asset.size > DIRECT_DOWNLOAD_LIMIT_BYTES:
            raise ValueError("asset_too_large_for_direct_download")
        with path.open("rb") as handle:
            data = handle.read(DIRECT_DOWNLOAD_LIMIT_BYTES + 1)
        if len(data) > DIRECT_DOWNLOAD_LIMIT_BYTES:
            raise ValueError("asset_too_large_for_direct_download")
        return data
