"""分布式评审客户端（协作者主机端）：HTTP 直连中心机审核中心 → 本地审（人工或自有 AGENT）→ 提交回中心。

部署模型：中心机一个进程（控制台或 df review-server）同时提供 UI 与审核中心 API；
协作者在自己的主机上：
  1. 配置 configs/review_remote.yaml（server/api_key 用中心管理员发放的账号）
  2. df review-remote pull    —— 拉取我的待审记录（身份=我的账号）
  3. df review-remote auto    —— 用【自己的模型】自动判 keep/reject（judge 槽位，
                                LLM_MODEL/--model 即可换模型：谁开谁是自己的 agent）
     或 df review-remote human —— 本地人工逐条过目
  4. df review-remote submit  —— 以我的身份提交标注回中心（含理由，可审计）
纯标准库（urllib），无 SDK 依赖。
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request
from typing import Any, Dict, List

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


class AuthClient:
    """中心机审核中心 HTTP 客户端（Bearer agent.<key> 身份认证）。"""

    def __init__(self, server: str, api_key: str, timeout: float = 30.0):
        self.server = server.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.me: Dict[str, str] = self._get("/api/me")

    def _req(self, method: str, path: str, body: Any = None) -> Any:
        url = f"{self.server}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise ConnectionError(f"审核中心返回 {e.code}: {detail}") from e

    def _get(self, path: str) -> Any:
        return self._req("GET", path)

    def pending(self, dataset: str, batch: int = 10) -> List[Dict[str, Any]]:
        from urllib.parse import urlencode

        out = self._get(f"/api/pending?{urlencode({'dataset': dataset, 'batch': batch})}")
        return list(out.get("records", []))

    def submit(self, dataset: str, decisions: List[Dict[str, Any]]) -> int:
        out = self._req("POST", "/api/submit", {"dataset": dataset, "records": decisions})
        return int(out.get("submitted", 0))


def get_client(cfg: Dict[str, Any]):
    return AuthClient(cfg["server"], cfg["api_key"])


def pull(cfg: Dict[str, Any], batch: int = 10, client: Any = None) -> List[Dict[str, Any]]:
    """拉取我的待审记录（身份=我的账号，已提交者被中心过滤），缓存到本地 remote_inbox。"""
    client = client or get_client(cfg)
    rows = client.pending(cfg.get("dataset", "rollout_review"), batch)
    out = [{
        "record_id": r["record_id"],
        "sample_id": r["sample_id"],
        "instruction": r.get("instruction", ""),
        "conversation": r.get("conversation", ""),
        "meta": r.get("meta", ""),
        "suggestion": r.get("suggestion", ""),
    } for r in rows]
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
    """以我的身份提交标注（keep/reject + 理由 → 中心可审计）。"""
    client = client or get_client(cfg)
    payload = [{
        "record_id": d["record_id"],
        "decision": d["decision"],
        "reason": str(d.get("reason", ""))[:500],
        "model": d.get("model", ""),
    } for d in decisions]
    return client.submit(cfg.get("dataset", "rollout_review"), payload)


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
