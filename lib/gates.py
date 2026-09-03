"""HITL 闸门状态机（纯状态机，无智能）：propose → awaiting → approve/reject。

调用点约定：
  df-import → require("G1")（数据源确认）
  df-run / df-distill → require("G0")（预算与模型确认）
  df-export --bulk → require("G3")（小批量放量确认）
未过门操作抛 GateBlocked（任务挂起），提示用户先 approve。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class GateBlocked(RuntimeError):
    """操作被闸门拦截（任务挂起，等待用户确认）。"""


@dataclass
class GateDef:
    id: str
    title: str
    trigger: str
    prompt: str
    requires: List[str] = field(default_factory=list)
    auto_approve: bool = False  # 用户显式声明自动通过（批量/cron 场景的审计式豁免）


@dataclass
class GateRecord:
    gate_id: str
    status: str  # pending | awaiting | approved | rejected
    proposed_at: str = ""
    decided_at: str = ""
    note: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


class GateKeeper:
    """闸门状态机：状态持久化到 state 文件，跨进程/跨运行有效。"""

    def __init__(self, gates_yaml: str | Path, state_path: str | Path):
        raw = yaml.safe_load(Path(gates_yaml).read_text(encoding="utf-8"))
        self.defs: Dict[str, GateDef] = {
            g["id"]: GateDef(
                id=g["id"], title=g.get("title", ""), trigger=g.get("trigger", ""),
                prompt=g.get("prompt", ""), requires=g.get("requires", []),
                auto_approve=bool(g.get("auto_approve", False)),
            )
            for g in raw.get("gates", [])
        }
        self.state_path = Path(state_path)
        self.records: Dict[str, GateRecord] = {}
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            for gid, r in data.get("gates", {}).items():
                self.records[gid] = GateRecord(**r)

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "gates": {gid: r.__dict__ for gid, r in self.records.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def status(self, gate_id: str) -> str:
        gate = self.defs.get(gate_id)
        if gate and gate.auto_approve:
            return "approved"  # 显式声明豁免（可审计：gates.yaml 中可见）
        return self.records.get(gate_id, GateRecord(gate_id, "pending")).status

    def propose(self, gate_id: str, context: Optional[Dict[str, Any]] = None, note: str = "") -> None:
        """置为 awaiting，等待用户决定。"""
        self.records[gate_id] = GateRecord(
            gate_id=gate_id, status="awaiting",
            proposed_at=datetime.now(timezone.utc).isoformat(),
            context=context or {}, note=note,
        )
        self._save()

    def decide(self, gate_id: str, approve: bool, note: str = "") -> None:
        self.records[gate_id] = GateRecord(
            gate_id=gate_id, status="approved" if approve else "rejected",
            proposed_at=self.records.get(gate_id, GateRecord(gate_id, "pending")).proposed_at,
            decided_at=datetime.now(timezone.utc).isoformat(),
            context=self.records.get(gate_id, GateRecord(gate_id, "pending")).context,
            note=note,
        )
        self._save()

    def require(self, gate_id: str) -> None:
        """通过才继续；未通过抛 GateBlocked。"""
        st = self.status(gate_id)
        if st != "approved":
            gate = self.defs.get(gate_id)
            raise GateBlocked(
                f"闸门 {gate_id}（{gate.title if gate else ''}）未通过（状态={st}）。"
                f"请确认：{gate.prompt if gate else ''}"
            )

    def blocked_ids(self) -> List[str]:
        return [gid for gid in self.defs if self.status(gid) != "approved"]
