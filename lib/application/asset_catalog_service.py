"""Data-library use cases: inspect a bounded catalog and a selected file."""
from __future__ import annotations

from collections import Counter

from lib.application.asset_catalog_ports import AssetCatalogDriver
from lib.domain.dataset_assets import Asset, AssetInventory, asset_sort_key, common_asset, generation_source_mode


class AssetCatalogApplication:
    def __init__(self, driver: AssetCatalogDriver):
        self._driver = driver

    def inventory(self, workspace_id: str) -> AssetInventory:
        return self._driver.inventory(workspace_id)

    @staticmethod
    def categories(inventory: AssetInventory) -> dict[str, int]:
        return dict(Counter(asset.category for asset in inventory.assets))

    @staticmethod
    def select(inventory: AssetInventory, category: str, search: str = "") -> list[Asset]:
        term = search.casefold().strip()
        return sorted((asset for asset in inventory.assets
                       if (category == "全部文件" or
                           (common_asset(asset) if category == "常用文件" else asset.category == category))
                       and term in asset.label.casefold()), key=asset_sort_key)

    def excerpt(self, workspace_id: str, asset: Asset) -> tuple[str, str]:
        return self._driver.excerpt(workspace_id, asset)

    def download(self, workspace_id: str, asset: Asset) -> bytes:
        return self._driver.download(workspace_id, asset)

    def generation_source(self, workspace_id: str, asset: Asset) -> dict[str, str]:
        """Resolve the selected version before opening it in the workbench."""
        mode = generation_source_mode(asset)
        return {"path": self._driver.source_path_for_generation(workspace_id, asset),
                "source_mode": mode}
