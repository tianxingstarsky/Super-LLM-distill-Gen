"""Opt-in Docker executor for the bounded Agent ledger tool.

The Python program is trusted application code passed as a fixed argv item.
Recorded tool arguments and a pinned JSON state travel only over stdin; they
are never interpolated into a shell command or mounted from the host.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid

from lib.domain.agent_sandbox_contract import (LEDGER_CONTRACT, LEDGER_TOOL,
                                                apply_ledger_action, ledger_action, ledger_task)
from lib.domain.agent_trajectory import ReplayOutcome, ReplayUnavailable
from lib.domain.workflow_quality import canonical


IMAGE_ENV = "DATAFORGE_AGENT_REPLAY_IMAGE"
_IMAGE = re.compile(r"docker\.io/library/python@sha256:[0-9a-f]{64}\Z")
_TIMEOUT_SECONDS = 8
_MAX_OUTPUT_BYTES = 4_096

# No filesystem, network, shell, user code, arbitrary Python expression, or
# import from the uploaded record is available to this program. Keep this in
# sync with the independent host contract/comparator in agent_sandbox_contract.
LEDGER_RUNNER = r'''
import json
import re
import sys

def check_balances(value):
    if not isinstance(value, dict) or not 2 <= len(value) <= 16:
        raise ValueError()
    for key, amount in value.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", key)
                or type(amount) is not int or not 0 <= amount <= 1000000000):
            raise ValueError()
    if sum(value.values()) > 1000000000:
        raise ValueError()

try:
    raw = sys.stdin.buffer.read(32769)
    if len(raw) > 32768:
        raise ValueError()
    request = json.loads(raw)
    if set(request) != {"contract", "balances", "goal_balances", "action"}:
        raise ValueError()
    if request["contract"] != "bounded_ledger_v1":
        raise ValueError()
    balances, goal, action = request["balances"], request["goal_balances"], request["action"]
    check_balances(balances)
    check_balances(goal)
    if (set(balances) != set(goal) or sum(balances.values()) != sum(goal.values())
            or balances == goal):
        raise ValueError()
    if not isinstance(action, dict):
        raise ValueError()
    if action.get("action") == "balance" and set(action) == {"action", "account"}:
        account = action["account"]
        if account not in balances:
            raise ValueError()
        result = balances[account]
    elif action.get("action") == "transfer" and set(action) == {"action", "from", "to", "amount"}:
        source, destination, amount = action["from"], action["to"], action["amount"]
        if (source not in balances or destination not in balances or source == destination
                or type(amount) is not int or not 1 <= amount <= 1000000000
                or balances[source] < amount or balances[destination] + amount > 1000000000):
            raise ValueError()
        balances[source] -= amount
        balances[destination] += amount
        result = balances[destination]
    else:
        raise ValueError()
    sys.stdout.write(json.dumps({"result": result, "balances": balances,
                                 "goal_met": balances == goal}, sort_keys=True, separators=(",", ":")))
except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
    sys.exit(2)
'''.strip()
RUNNER_SHA256 = hashlib.sha256(LEDGER_RUNNER.encode("utf-8")).hexdigest()


def validate_sandbox_image(image: str | None) -> str | None:
    """Only an administrator-selected, immutable official Python image runs."""
    if image is None or image == "":
        return None
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        raise ValueError("agent_sandbox_image_must_be_pinned_official_python_digest")
    return image


def _command(image: str, name: str, docker_cli: str) -> list[str]:
    return [docker_cli, "run", "--rm", "--interactive", "--pull=never",
            "--platform=linux/amd64", "--name", name,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--security-opt=seccomp=builtin",
            "--user=65534:65534", "--pids-limit=32", "--memory=128m",
            "--memory-swap=128m", "--cpus=0.5", "--ulimit=nofile=64:64",
            "--no-healthcheck", "--workdir=/",
            "--entrypoint=/usr/local/bin/python3", image,
            "-I", "-B", "-S", "-c", LEDGER_RUNNER]


def _remove_timed_out_container(docker_cli: str, name: str) -> None:
    try:
        subprocess.run([docker_cli, "rm", "-f", name], capture_output=True,
                       timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass


class DockerLedgerReplay:
    """One stateful, source-pinned ledger task; each action gets a fresh container."""

    def __init__(self, image: str, *, docker_cli: str = "docker"):
        self.image = validate_sandbox_image(image)
        if self.image is None:
            raise ValueError("agent_sandbox_image_required")
        self.docker_cli = docker_cli
        self.snapshot_id: str | None = None
        self.balances: dict[str, int] | None = None
        self.goal: dict[str, int] | None = None
        self.initial_sha256: str | None = None

    def replay(self, name: str, arguments: dict, snapshots: object) -> ReplayOutcome:
        if name != LEDGER_TOOL:
            raise ReplayUnavailable("tool_replay_unavailable")
        snapshot_id, action = ledger_action(arguments)
        if self.snapshot_id is None:
            self.balances, self.goal = ledger_task(snapshots, snapshot_id)
            self.snapshot_id = snapshot_id
            self.initial_sha256 = hashlib.sha256(canonical(self.balances).encode("utf-8")).hexdigest()
        elif snapshot_id != self.snapshot_id:
            raise ReplayUnavailable("multiple_ledger_snapshots_in_trajectory")
        assert self.balances is not None and self.goal is not None
        expected_result, expected_state = apply_ledger_action(self.balances, action)
        before = hashlib.sha256(canonical(self.balances).encode("utf-8")).hexdigest()
        request = {"contract": LEDGER_CONTRACT, "balances": self.balances,
                   "goal_balances": self.goal, "action": action}
        payload = canonical(request).encode("utf-8")
        if len(payload) > 32_768:
            raise ReplayUnavailable("tool_snapshot_limit_exceeded")
        container_name = "dataforge-agent-" + uuid.uuid4().hex[:16]
        try:
            completed = subprocess.run(_command(self.image, container_name, self.docker_cli),
                                       input=payload, capture_output=True, timeout=_TIMEOUT_SECONDS,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            _remove_timed_out_container(self.docker_cli, container_name)
            raise ReplayUnavailable("container_replay_timeout") from None
        except OSError:
            raise ReplayUnavailable("container_replay_unavailable") from None
        if completed.returncode != 0:
            raise ReplayUnavailable("container_replay_unavailable")
        if len(completed.stdout) > _MAX_OUTPUT_BYTES:
            raise ReplayUnavailable("invalid_container_replay_output")
        try:
            response = json.loads(completed.stdout)
        except (ValueError, UnicodeError):
            raise ReplayUnavailable("invalid_container_replay_output") from None
        if (not isinstance(response, dict) or set(response) != {"result", "balances", "goal_met"}
                or type(response["result"]) is not int or type(response["goal_met"]) is not bool
                or type(response["balances"]) is not dict
                or response["result"] != expected_result
                or canonical(response["balances"]) != canonical(expected_state)
                or response["goal_met"] != (expected_state == self.goal)):
            raise ReplayUnavailable("container_replay_result_mismatch")
        self.balances = expected_state
        return ReplayOutcome(expected_result, "isolated_docker_ledger_replay",
                             {"image": self.image, "runner_sha256": RUNNER_SHA256,
                              "snapshot_id": snapshot_id, "initial_state_sha256": self.initial_sha256,
                              "before_state_sha256": before,
                              "after_state_sha256": hashlib.sha256(canonical(expected_state).encode("utf-8")).hexdigest(),
                              "terminal_goal_met": expected_state == self.goal})

    def terminal(self) -> tuple[bool, dict]:
        if self.balances is None or self.goal is None or self.snapshot_id is None:
            raise ReplayUnavailable("no_container_replay")
        return self.balances == self.goal, {
            "image": self.image, "runner_sha256": RUNNER_SHA256,
            "snapshot_id": self.snapshot_id,
            "final_state_sha256": hashlib.sha256(canonical(self.balances).encode("utf-8")).hexdigest(),
            "goal_state_sha256": hashlib.sha256(canonical(self.goal).encode("utf-8")).hexdigest(),
            "basis": "bounded_ledger_terminal_equality",
        }
