"""Public access to dataset versions, reusable files and training exports.

DatasetApplication reads verified samples, statistics and delivery archives.
AssetCatalogApplication locates reusable sources and existing deliverables.
prepare_trl_export projects training records into the trainer-facing format.

These services consume existing artifacts; they do not start generation.
Concrete readers and archive stores are selected in lib.bootstrap.
"""
from .application.service import DatasetApplication
from .application.ports import DatasetPort
from .application.trainer_export import prepare_trl_export
from .application.asset_catalog import AssetCatalogApplication
