"""Model inventory and draft preparation without framework or filesystem access."""
from typing import Protocol

from lib.domain.workflow_node_models import binding_snapshot, initialize_draft


class ModelInventoryPort(Protocol):
    def list_backends(self) -> dict: ...


class WorkflowNodeModelsApplication:
    def __init__(self, inventory: ModelInventoryPort):
        self._inventory = inventory

    def prepare_draft(self, nodes, source_mode, draft, initialized, *, node_generation=None,
                      package_review=None, cpt_processing=None):
        return initialize_draft(nodes, source_mode, draft, initialized, self._inventory.list_backends(),
                                node_generation=node_generation, package_review=package_review,
                                cpt_processing=cpt_processing)

    def snapshot(self, nodes, source_mode, bindings, *, node_generation=None, package_review=None,
                 cpt_processing=None):
        # Recheck service existence at submission, rather than trust an earlier UI render.
        endpoints = {row["name"]: row for row in self._inventory.list_backends().get("backends", [])}
        return binding_snapshot(nodes, source_mode, bindings, endpoints, node_generation=node_generation,
                                package_review=package_review, cpt_processing=cpt_processing)
