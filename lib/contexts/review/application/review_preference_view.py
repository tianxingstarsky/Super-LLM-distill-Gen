"""One canonical pair review, projected into each requested training format."""
from copy import deepcopy
from ..domain.preference_candidates import rlaif_view
from lib.contexts.workflows.contracts import PREFERENCE_TARGETS


class ReviewPreferenceView:
    def __init__(self, reviewer, recipe):
        self._reviewer, self._recipe = reviewer, recipe

    def execute(self, item):
        target = item["target"]
        if (target not in PREFERENCE_TARGETS or self._recipe.get("preference_design_version") not in {1, 2, 3}
                or self._recipe["review_repair"]["mode"] != "auto"):
            return self._reviewer.execute(item)
        neutral = {**deepcopy(item), "target": "dpo"}
        rows = self._reviewer.execute(neutral)
        for row in rows:
            row["_review_target"] = target
            if target == "rlaif" and row.get("preference"):
                row.update(rlaif_view(row, judge_role=("jev" if self._recipe.get("package_review", {}).get("enabled")
                                                     else "generation")))
        return rows
