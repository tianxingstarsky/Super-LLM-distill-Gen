"""Composition root for the data-library catalog."""
from lib.contexts.datasets.api import AssetCatalogApplication
from lib.contexts.datasets.infrastructure.asset_catalog import FilesystemAssetCatalogDriver


def asset_catalog_application() -> AssetCatalogApplication:
    return AssetCatalogApplication(FilesystemAssetCatalogDriver())
