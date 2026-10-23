"""Wire execution policy to the existing checkpointed generation runtime."""
from lib.contexts.workflows.application.execute_run import ExecuteWorkflowRun
from lib.contexts.workflows.application.generate_batch import GenerateDatasetBatch
from lib.contexts.workflows.infrastructure.execution_runtime import (
    CheckpointedBatchRuntime, FilesystemRunSession,
)


def workflow_execution(runtime) -> ExecuteWorkflowRun:
    from lib.infrastructure.training_workflow import SUPPORTED_RECIPE_VERSIONS

    return ExecuteWorkflowRun(
        FilesystemRunSession(runtime, supported_recipe_versions=SUPPORTED_RECIPE_VERSIONS),
        GenerateDatasetBatch(CheckpointedBatchRuntime(runtime)),
    )
