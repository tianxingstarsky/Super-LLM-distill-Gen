"""分布式评审客户端（协作者主机端）：拉取中心机待审批次 → 本地审（人工或自有 AGENT）→ 提交回中心。

部署模型：中心机跑完整协作栈（Argilla 服务端），协作者在自己的主机上：
  1. 配置 configs/review_remote.yaml（server/api_key 用中心管理员发放的账号）
  2. df review-remote pull    —— 拉取我的待审记录（身份=我的账号）
  3. df review-remote auto    —— 用【自己的模型】自动判 keep/reject（judge 槽位，
                                LLM_MODEL/--model 即可换模型：谁开谁是自己的 agent）
     或 df review-remote human —— 本地人工逐条过目
  4. df review-remote submit  —— 以我的身份提交标注回中心（含理由，可审计）
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import argilla as rg

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "output"
INBOX_PATH = OUT_DIR / "remote_inbox.jsonl"
DEFAULT_CONFIG = ROOT / "configs" / "review_remote.yaml"


def load_config(path: str | pathlib.Path | None = None) -> Dict[str, Any]:
    path = pathlib.Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"缺少评审配置 {path}（模板见 configs/review_remote.example.yaml）")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def get_client(cfg: Dict[str, Any]):
    import argilla as rg

    return rg.Argilla(api_url=cfg["server"], api_key=cfg["api_key"])


def pull(cfg: Dict[str, Any], batch: int = 10, client: Any = None) -> List[Dict[str, Any]]:
    """拉取我的待审记录（本人账号有权限的记录），缓存到本地 remote_inbox。"""
    client = client or get_client(cfg)
    ds = client.datasets(name=cfg.get("dataset", "rollout_review"))
    out = []
    for rec in ds.records:
        if len(out) >= batch:
            break
        submitted = any(getattr(r, "status", None) == "submitted" for r in (rec.responses or {}).values()) if hasattr(rec.responses, "values") else False
        if submitted:
            continue
        out.append({
            "record_id": rec.id,
            "sample_id": rec.fields["sample_id"],
            "instruction": rec.fields.get("instruction", ""),
            "conversation": rec.fields.get("conversation", ""),
            "meta": rec.fields.get("meta", ""),
            "suggestion": str(next(iter(rec.suggestions.values()), "") if hasattr(rec.suggestions, "values") else ""),
        })
    INBOX_PATH.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8"
    )
    return out


def _judge_answers(inbox: List[Dict[str, Any]], judge: Any, model: str) -> List[Dict[str, Any]]:
    """agent 模式：用【我的模型】走 judge.score 自动判定（谁开 agent 谁就是评审 agent）。"""
    from lib.llm_client import chat_json
    from lib.prompts import get, render

    judged = []
    for rec in inbox:
        try:
            score = chat_json(judge, [{"role": "user", "content": render(
                get("judge.score"), goal=rec["instruction"][:500],
                thinking="", final_answer=rec["conversation"][:2000])}], temperature=0.2)
            keep = bool(score.get("keep", False))
        except Exception as e:  # noqa: BLE001
            judged.append({**rec, "decision": "reject", "reason": f"本地 agent 判定异常: {str(e)[:120]}", "model": model})
            continue
        judged.append({
            **rec, "decision": "keep" if keep else "reject",
            "reason": f"本地 agent 判定: correctness={score.get('correctness')}",
            "model": model,
        })
    return judged


def submit(decisions: List[Dict[str, Any]], cfg: Dict[str, Any], client: Any = None) -> int:
    """以我的身份提交标注（keep/reject + 理由 → Response，可直接进中心审核统计）。"""
    client = client or get_client(cfg)
    me = client.me
    ds = client.datasets(name=cfg.get("dataset", "rollout_review"))
    # 保证理由题存在（旧数据集幂等补加；已存在则忽略）
    try:
        ds.questions.add(rg.TextQuestion(name="reason", title="判定理由/模型", required=False,
                                         client=client))
    except Exception:  # noqa: BLE001 —— 已存在/Server 兼容，不阻断提交
        pass
    updates = []
    for d in decisions:
        fields = {
            "sample_id": d["sample_id"],
            "instruction": d["instruction"],
            "conversation": d["conversation"],
            "meta": d.get("meta", ""),
        }
        updates.append(rg.Record(
            id=d["record_id"],
            fields=fields,
            responses=[
                rg.Response(question_name="keep_label", value=d["decision"],
                            user_id=me.id),
                rg.Response(question_name="reason", value=str(d.get("reason", ""))[:500],
                            user_id=me.id),
            ],
        ))
    ds.records.log(records=updates)
    return len(updates)


def human_loop(decisions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """human 模式：本地逐条过目，输入 keep/reject（理由必填）。"""
    for d in decisions:
        print(f"\n=== {d['sample_id']} ===")
        print(f"指令: {d['instruction'][:120]}")
        print(f"对话: {d['conversation'][:300]}")
        while True:
            ans = input("决策 (keep/reject): ").strip().lower()
            if ans in ("keep", "reject"):
                break
            print("请输入 keep 或 reject")
        reason = input(f"理由 (enter=默认): ").strip()
        d["decision"] = ans if ans in ("keep", "reject") else "reject"
        d["reason"] = reason or "本地人工评审"
        d["model"] = "human"
    return decisions
