"""Composition root for the data-library catalog."""
from lib.application.asset_catalog_service import AssetCatalogApplication
from lib.infrastructure.asset_catalog_driver import FilesystemAssetCatalogDriver


def asset_catalog_application() -> AssetCatalogApplication:
    return AssetCatalogApplication(FilesystemAssetCatalogDriver())
