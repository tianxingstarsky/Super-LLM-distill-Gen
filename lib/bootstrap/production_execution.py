"""Wire production scheduling to file receipts and the on-disk corpus index."""
from lib.contexts.workflows.application.execute_production import ExecuteProductionRounds
from lib.contexts.workflows.infrastructure.production_adapter import FilesystemProductionAdapter
from lib.contexts.datasets.infrastructure.corpus_index import CorpusNearDuplicateIndex


def production_execution(runtime):
    return ExecuteProductionRounds(FilesystemProductionAdapter(runtime, CorpusNearDuplicateIndex))


def production_batch(runtime):
    from lib.contexts.workflows.application.generate_production_batch import GenerateProductionBatch
    from lib.contexts.workflows.infrastructure.production_batch import CheckpointedProductionBatch
    return GenerateProductionBatch(CheckpointedProductionBatch(runtime), runtime.recipe)
