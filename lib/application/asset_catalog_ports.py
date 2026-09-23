"""Storage boundary for the data-library catalog."""
from __future__ import annotations

from typing import Protocol

from lib.domain.dataset_assets import Asset, AssetInventory


class AssetCatalogDriver(Protocol):
    def inventory(self, workspace_id: str) -> AssetInventory: ...
    def excerpt(self, workspace_id: str, asset: Asset) -> tuple[str, str]: ...
    def download(self, workspace_id: str, asset: Asset) -> bytes: ...
