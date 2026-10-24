"""Run explicit rework of immutable selected parents without re-running generation."""
from __future__ import annotations
from .automatic_ports import RepairBranchPort
from ..domain.repair import validate_repair_inputs


class ExecuteRepairBranch:
    def __init__(self, port: RepairBranchPort, recipe: dict):
        self._port, self.recipe = port, recipe

    def execute(self):
        inputs = validate_repair_inputs(self.recipe["repair_inputs"], self.recipe["review_repair"])
        self._port.authenticate(inputs)
        if self.recipe.get("training_format_lineage_version") == 1:
            from ..domain.training_lineage import validate_format_view_selections
            validate_format_view_selections(inputs, self._port.run_id)
        self._port.prepare(inputs)
        reviewed = self._port.review(inputs)
        counts = self._port.publish(reviewed)
        status = "completed" if all(value["eligible"] == value["total"] for value in counts.values()) else "needs_attention"
        return self._port.complete(status)
