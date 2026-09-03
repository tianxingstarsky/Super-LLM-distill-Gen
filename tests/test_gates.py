"""G0/G1/G3 闸门状态机离线测试。"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _keeper(tmp_path):
    from lib.gates import GateKeeper

    return GateKeeper(ROOT / "configs" / "gates.yaml", tmp_path / "gates_state.json")


def test_initial_state_pending():
    k = _keeper(__import__("pathlib").Path("."))
    assert k.status("G0") == "pending"
    assert k.status("G1") == "pending"
    assert k.status("G3") == "pending"


def test_propose_decide_require(tmp_path):
    from lib.gates import GateBlocked, GateKeeper

    k = GateKeeper(ROOT / "configs" / "gates.yaml", tmp_path / "state.json")
    # 未过门 → require 拦截
    with pytest.raises(GateBlocked):
        k.require("G1")
    # propose → awaiting，仍拦截
    k.propose("G1", {"rollout_dir": "X"})
    assert k.status("G1") == "awaiting"
    with pytest.raises(GateBlocked):
        k.require("G1")
    # approve → 放行
    k.decide("G1", True, note="确认")
    assert k.status("G1") == "approved"
    k.require("G1")  # 不抛异常
    # reject 后仍拦截
    k.decide("G1", False)
    with pytest.raises(GateBlocked):
        k.require("G1")


def test_state_persists_across_instances(tmp_path):
    from lib.gates import GateKeeper

    state = tmp_path / "state.json"
    k1 = GateKeeper(ROOT / "configs" / "gates.yaml", state)
    k1.propose("G0")
    k1.decide("G0", True)
    k2 = GateKeeper(ROOT / "configs" / "gates.yaml", state)
    assert k2.status("G0") == "approved"
    k2.require("G0")  # 不抛异常
