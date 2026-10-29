"""Wire production scheduling to file receipts and the on-disk corpus index."""
from lib.contexts.workflows.application.execute_production import ExecuteProductionRounds
from lib.contexts.workflows.infrastructure.production_adapter import FilesystemProductionAdapter
from lib.contexts.datasets.infrastructure.corpus_index import CorpusNearDuplicateIndex


def production_execution(runtime) -> ExecuteProductionRounds:
    """Build resumable round scheduling backed by receipts and corpus deduplication."""
    return ExecuteProductionRounds(FilesystemProductionAdapter(runtime, CorpusNearDuplicateIndex))


def production_batch(runtime):
    """Build one production window; round advancement belongs to production_execution."""
    from lib.contexts.workflows.application.generate_production_batch import GenerateProductionBatch
    from lib.contexts.workflows.infrastructure.production_batch import CheckpointedProductionBatch
    return GenerateProductionBatch(CheckpointedProductionBatch(runtime), runtime.recipe)
