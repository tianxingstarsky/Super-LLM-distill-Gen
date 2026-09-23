"""Contract, command-line containment and workflow integration for Docker replay.

These tests deliberately fake Docker. They do not claim that a container ran on
the test host; a separate Docker-daemon smoke test is required for deployment.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from lib.domain.agent_sandbox_contract import apply_ledger_action
from lib.domain.agent_trajectory import assess_recorded_trajectory
from lib.domain.workflow_quality import canonical
from lib.infrastructure import agent_docker_replay
from lib.infrastructure.agent_docker_replay import (DockerLedgerReplay, IMAGE_ENV, LEDGER_RUNNER,
                                                   validate_sandbox_image)
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts


IMAGE = "docker.io/library/python@sha256:" + "a" * 64
SNAPSHOTS = {"allocation": {"kind": "bounded_ledger_v1",
                            "balances": {"alpha": 3, "beta": 0},
                            "goal_balances": {"alpha": 1, "beta": 2}}}


def _call(call_id: str, action: dict) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": "sandbox_ledger", "arguments": json.dumps(
            {"snapshot_id": "allocation", **action})}}]}


def _messages(*, result: str = "2", final: str = "2", amount: int = 2) -> list[dict]:
    return [{"role": "user", "content": "把两单位从 alpha 转至 beta，然后回答 beta 的余额"},
            _call("move", {"action": "transfer", "from": "alpha", "to": "beta", "amount": amount}),
            {"role": "tool", "tool_call_id": "move", "content": result},
            {"role": "assistant", "content": final}]


def _fake_docker(calls: list[list[str]]):
    def invoke(command, **kwargs):
        calls.append(command)
        assert kwargs.get("input") and kwargs.get("capture_output") is True
        assert "shell" not in kwargs
        request = json.loads(kwargs["input"])
        result, balances = apply_ledger_action(request["balances"], request["action"])
        output = {"result": result, "balances": balances,
                  "goal_met": balances == request["goal_balances"]}
        return subprocess.CompletedProcess(command, 0, canonical(output).encode(), b"")
    return invoke


def test_runner_program_executes_only_bounded_state_action():
    request = {"contract": "bounded_ledger_v1", "balances": {"alpha": 3, "beta": 0},
               "goal_balances": {"alpha": 1, "beta": 2},
               "action": {"action": "transfer", "from": "alpha", "to": "beta", "amount": 2}}
    done = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", LEDGER_RUNNER],
                          input=canonical(request).encode(), capture_output=True, timeout=3)
    assert done.returncode == 0
    assert json.loads(done.stdout) == {"result": 2, "balances": {"alpha": 1, "beta": 2},
                                      "goal_met": True}
    request["action"] = {"action": "shell", "command": "whoami"}
    denied = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", LEDGER_RUNNER],
                            input=canonical(request).encode(), capture_output=True, timeout=3)
    assert denied.returncode == 2 and denied.stdout == b""


def test_image_must_be_official_python_with_exact_digest():
    assert validate_sandbox_image(None) is None
    assert validate_sandbox_image(IMAGE) == IMAGE
    for invalid in ("python:latest", "docker.io/library/python:3.12", "evil/python@sha256:" + "a" * 64,
                    "docker.io/library/python@sha256:" + "z" * 64):
        with pytest.raises(ValueError, match="pinned_official_python_digest"):
            validate_sandbox_image(invalid)


def test_opt_in_docker_command_has_no_host_mount_or_network(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", _fake_docker(calls))
    result = assess_recorded_trajectory(_messages(), tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result["status"] == "eligible"
    assert result["verification"]["method"] == "isolated_docker_ledger_replay"
    assert result["verification"]["terminal"]["basis"] == "bounded_ledger_terminal_equality"
    command = calls[0]
    assert command[:2] == ["docker", "run"]
    for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges:true", "--security-opt=seccomp=builtin",
                 "--user=65534:65534", "--pids-limit=32", "--memory=128m",
                 "--memory-swap=128m", "--cpus=0.5", "--entrypoint=/usr/local/bin/python3"):
        assert flag in command
    assert not any(flag.startswith(("-v", "--volume", "--mount", "--env", "--privileged"))
                   for flag in command)
    assert command[-5:] == ["-I", "-B", "-S", "-c", LEDGER_RUNNER]


def test_terminal_goal_and_recorded_result_are_separate_failure_evidence(monkeypatch):
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", _fake_docker([]))
    unfinished = assess_recorded_trajectory(_messages(result="1", final="1", amount=1),
                                            tool_snapshots=SNAPSHOTS,
                                            sandbox_replay=DockerLedgerReplay(IMAGE))
    assert unfinished["reason"] == "terminal_state_mismatch"
    assert unfinished["negative"]["failure_step"] == 3
    assert unfinished["negative"]["evidence"]["final_state_sha256"]
    wrong_observation = assess_recorded_trajectory(_messages(result="3", final="3"),
                                                   tool_snapshots=SNAPSHOTS,
                                                   sandbox_replay=DockerLedgerReplay(IMAGE))
    assert wrong_observation["reason"] == "tool_observation_mismatch"
    assert wrong_observation["negative"]["evidence"]["basis"] == "isolated_docker_ledger_replay"


def test_daemon_failure_quarantines_without_negative_and_local_adapter_survives(monkeypatch):
    def unavailable(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"daemon unavailable")
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", unavailable)
    result = assess_recorded_trajectory(_messages(), tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result == {"status": "quarantined", "reason": "container_replay_unavailable"}
    local = [{"role": "user", "content": "2+2"},
             {"role": "assistant", "content": "", "tool_calls": [
                 {"id": "calc", "name": "calculator", "input": {"expression": "2+2"}}]},
             {"role": "tool", "tool_call_id": "calc", "content": "4"},
             {"role": "assistant", "content": "4"}]
    assert assess_recorded_trajectory(local, sandbox_replay=DockerLedgerReplay(IMAGE))["status"] == "eligible"


def test_timeout_force_removes_named_container_and_does_not_create_negative(monkeypatch):
    commands = []
    def timed_out(command, **kwargs):
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, timeout=8)
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", timed_out)
    result = assess_recorded_trajectory(_messages(), tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result == {"status": "quarantined", "reason": "container_replay_timeout"}
    assert commands[1] == ["docker", "rm", "-f", commands[0][commands[0].index("--name") + 1]]


def test_invalid_container_response_cannot_override_host_comparator(monkeypatch):
    def forged(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, canonical({
            "result": 2, "balances": {"alpha": True, "beta": 2}, "goal_met": True}).encode(), b"")
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", forged)
    result = assess_recorded_trajectory(_messages(), tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result == {"status": "quarantined", "reason": "container_replay_result_mismatch"}


def test_workflow_pins_opt_in_image_and_keeps_unavailable_trace_out_of_training(tmp_path, monkeypatch):
    monkeypatch.setenv(IMAGE_ENV, IMAGE)
    source = tmp_path / "trajectory.jsonl"
    source.write_text(json.dumps({"messages": _messages(), "tool_snapshots": SNAPSHOTS}) + "\n",
                      encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["agent"])
    path = run_path(output, run_id)
    assert read_json(path / "recipe.json")["agent_sandbox_image"] == IMAGE
    def unavailable(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"daemon unavailable")
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", unavailable)
    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"agent": 0}
    record = read_json(path / "artifacts" / "agent.records.json")[0]
    assert record["reason"] == "container_replay_unavailable"
    assert "negative" not in record


def test_unsupported_tool_and_unbounded_action_never_reach_docker(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_docker_replay.subprocess, "run", _fake_docker(calls))
    unknown = _messages()
    unknown[1]["tool_calls"][0]["function"]["name"] = "shell"
    result = assess_recorded_trajectory(unknown, tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result["reason"] == "tool_replay_unavailable"
    assert result["unverified_tool"] == "shell"
    assert calls == []
    unsafe = _messages()
    unsafe[1]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "snapshot_id": "allocation", "action": "shell", "command": "whoami"})
    result = assess_recorded_trajectory(unsafe, tool_snapshots=SNAPSHOTS,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result["reason"] == "invalid_ledger_arguments"
    assert calls == []
    already_complete = {"allocation": {**SNAPSHOTS["allocation"],
                                       "goal_balances": SNAPSHOTS["allocation"]["balances"]}}
    result = assess_recorded_trajectory(_messages(), tool_snapshots=already_complete,
                                        sandbox_replay=DockerLedgerReplay(IMAGE))
    assert result["reason"] == "invalid_ledger_goal"
    assert calls == []
