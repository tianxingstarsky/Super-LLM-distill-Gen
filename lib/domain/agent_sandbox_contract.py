"""Small, explicit state-machine contract for isolated Agent replay.

The uploaded snapshot defines a task *inside* a bounded ledger. It does not
establish that the balances describe an external system. Both the host and the
container validate this contract before a recorded observation is accepted.
"""
from __future__ import annotations

from copy import deepcopy
import re

from lib.domain.agent_trajectory import ReplayUnavailable


LEDGER_CONTRACT = "bounded_ledger_v1"
LEDGER_TOOL = "sandbox_ledger"
MAX_ACCOUNTS = 16
MAX_BALANCE = 1_000_000_000
_ACCOUNT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")


def _balances(value: object) -> dict[str, int]:
    if (not isinstance(value, dict) or not 2 <= len(value) <= MAX_ACCOUNTS
            or any(not isinstance(key, str) or not _ACCOUNT.fullmatch(key)
                   or type(amount) is not int or not 0 <= amount <= MAX_BALANCE
                   for key, amount in value.items())):
        raise ReplayUnavailable("invalid_ledger_snapshot")
    if sum(value.values()) > MAX_BALANCE:
        raise ReplayUnavailable("invalid_ledger_snapshot")
    return deepcopy(value)


def ledger_task(snapshots: object, snapshot_id: str) -> tuple[dict[str, int], dict[str, int]]:
    if not isinstance(snapshots, dict) or snapshot_id not in snapshots:
        raise ReplayUnavailable("missing_tool_snapshot")
    item = snapshots[snapshot_id]
    if not isinstance(item, dict) or set(item) != {"kind", "balances", "goal_balances"}:
        raise ReplayUnavailable("invalid_ledger_snapshot")
    if item["kind"] != LEDGER_CONTRACT:
        raise ReplayUnavailable("invalid_ledger_snapshot")
    balances, goal = _balances(item["balances"]), _balances(item["goal_balances"])
    if (set(balances) != set(goal) or sum(balances.values()) != sum(goal.values())
            or balances == goal):
        raise ReplayUnavailable("invalid_ledger_goal")
    return balances, goal


def ledger_action(arguments: object) -> tuple[str, dict]:
    if not isinstance(arguments, dict):
        raise ReplayUnavailable("invalid_ledger_arguments")
    snapshot_id = arguments.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id or len(snapshot_id) > 64:
        raise ReplayUnavailable("invalid_ledger_arguments")
    action = arguments.get("action")
    if action == "balance":
        if set(arguments) != {"snapshot_id", "action", "account"}:
            raise ReplayUnavailable("invalid_ledger_arguments")
        account = arguments["account"]
        if not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
            raise ReplayUnavailable("invalid_ledger_arguments")
        return snapshot_id, {"action": "balance", "account": account}
    if action == "transfer":
        if set(arguments) != {"snapshot_id", "action", "from", "to", "amount"}:
            raise ReplayUnavailable("invalid_ledger_arguments")
        source, destination, amount = arguments["from"], arguments["to"], arguments["amount"]
        if (not isinstance(source, str) or not _ACCOUNT.fullmatch(source)
                or not isinstance(destination, str) or not _ACCOUNT.fullmatch(destination)
                or source == destination or type(amount) is not int or not 1 <= amount <= MAX_BALANCE):
            raise ReplayUnavailable("invalid_ledger_arguments")
        return snapshot_id, {"action": "transfer", "from": source, "to": destination,
                             "amount": amount}
    raise ReplayUnavailable("invalid_ledger_arguments")


def apply_ledger_action(balances: dict[str, int], action: dict) -> tuple[int, dict[str, int]]:
    """Independent host-side result comparator for the container response."""
    current = deepcopy(balances)
    if action["action"] == "balance":
        account = action["account"]
        if account not in current:
            raise ReplayUnavailable("ledger_account_missing")
        return current[account], current
    source, destination, amount = action["from"], action["to"], action["amount"]
    if source not in current or destination not in current:
        raise ReplayUnavailable("ledger_account_missing")
    if current[source] < amount or current[destination] + amount > MAX_BALANCE:
        raise ReplayUnavailable("ledger_transfer_out_of_bounds")
    current[source] -= amount
    current[destination] += amount
    return current[destination], current
