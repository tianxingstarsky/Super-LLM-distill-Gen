"""Coordinate committed production rounds and final validation through narrow ports."""
from __future__ import annotations
from .production_ports import ProductionPort
from ..domain.production import quality_first_production, soft_expectation_production
from ..domain.production_rounds import (restore_stop_signals, plan_next_round,
    check_round_availability, advance_round)


class ExecuteProductionRounds:
    def __init__(self, port: ProductionPort):
        self._port = port

    def execute(self):
        while True:
            self._port.reset_replenishment()
            state = self.execute_once()
            if not self._port.needs_replenishment():
                return state

    def execute_once(self):
        with self._port.open_session() as progress:
            restore_stop_signals(progress)
            stop = self._run_rounds(progress)
            self._port.begin_finalization(progress)
            return self._finalize(progress, stop)

    def _run_rounds(self, progress):
        while True:
            self._port.check_cancel()
            plan = plan_next_round(progress)
            if isinstance(plan, str):
                return plan
            batch = self._port.prepare_round(progress, plan)
            stop = check_round_availability(progress, plan, ready_count=len(batch.ready),
                source_count=len(batch.sources), arithmetic_count=batch.arithmetic_count,
                arithmetic_stop=batch.arithmetic_stop)
            if stop:
                return stop
            receipt = self._port.execute_and_commit(progress, plan, batch)
            progress.receipts.append(receipt)
            previous = progress.production
            self._port.refresh_progress(progress)
            advance_round(progress, previous)
            self._port.record_progress(progress, receipt)

    def _finalize(self, progress, stop):
        config, production, receipts, recipe = progress.config, progress.production, progress.receipts, progress.recipe
        quality_first, soft = quality_first_production(config), soft_expectation_production(config)
        reached_reason = "expectation_reached" if soft else "goal_reached"
        low_reason = "diminishing_returns" if soft else "low_acceptance_rate"
        if stop == "source_exhausted" and any(item["stop_reason"] == "director_saturation"
                                              for item in production["goals"].values()):
            stop = "director_saturation"
        if stop == "source_exhausted" and any(item["stop_reason"] == low_reason
                                              for item in production["goals"].values()):
            stop = low_reason
        if stop == "source_exhausted" and any(item["stop_reason"] == "arithmetic_space_exhausted"
                                              for item in production["goals"].values()):
            stop = "arithmetic_space_exhausted"
        unavailable = any(production["counts"][target] == 0 for target in recipe["targets"] if target not in config["goals"])
        if unavailable and stop == reached_reason:
            stop = "source_exhausted"
        production.update(stop_reason=stop, status=self._port.completion_status(progress, production["counts"], stop))
        for goal in production["goals"].values():
            if not goal["remaining"]:
                goal["stop_reason"] = reached_reason
            elif not goal["stop_reason"]:
                goal["stop_reason"] = stop
        counts = self._port.publish_final(progress)
        final_loss = any(counts.get(target, 0) < production["counts"].get(target, 0)
                         for target in config["goals"])
        production["counts"] = counts
        for target, goal in production["goals"].items():
            goal.update(eligible=counts[target], remaining=max(0, goal["goal"] - counts[target]))
            if not goal["remaining"]:
                goal["stop_reason"] = reached_reason
            elif goal["stop_reason"] in {None, reached_reason}:
                goal["stop_reason"] = stop if stop != reached_reason else "final_validation_loss"
        unmet = any(goal["remaining"] for goal in production["goals"].values())
        if (not quality_first and not production.get("planning_exhaustion") and unmet and final_loss and
                production["attempted"] < config["max_attempts"] and len(receipts) < config["max_rounds"]):
            return self._port.reconcile_for_replenishment(progress)
        if unmet and stop == reached_reason:
            stop = "final_validation_loss"
        if unmet and stop == "source_exhausted" and any(goal["stop_reason"] == low_reason
                                                        for goal in production["goals"].values()):
            stop = low_reason
        production.pop("current_batch", None)
        unavailable = any(counts[target] == 0 for target in recipe["targets"] if target not in config["goals"])
        production.update(status=self._port.completion_status(progress, counts, stop), stop_reason=stop)
        if soft:
            production["below_expectation"] = {target: goal["remaining"] for target, goal in production["goals"].items()
                                              if goal["remaining"]}
        return self._port.complete(progress)
