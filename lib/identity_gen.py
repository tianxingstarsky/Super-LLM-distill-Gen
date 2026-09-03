"""身份问答零参考训练集管线：多样化"你是谁"问题 + 固定事实回答 + 事实校验质量门。

防重复（对应防重复四层）：
  - 生成期：问题生成要求句式/语气/语言/场景互异 + seen_questions 显式排除；
  - 精排期：问题级 sha256 manifest（跨运行零重复）+ 开篇词多样性统计；
  - 质量门：identity.fact_check（关键事实完整性/虚构检测/自然度）判定 keep。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List, Tuple

import yaml

from lib.llm_client import chat_json
from lib.prompts import get, render


def load_config(path: str | pathlib.Path) -> Dict[str, Any]:
    cfg = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    identity = cfg["identity"]
    gen = cfg.get("generation", {})
    return {
        "company": identity["company"],
        "model_name": identity["model_name"],
        "facts": identity["facts"].strip(),
        "required_facts": identity["required_facts"].strip(),
        "style": identity.get("style", "专业、简洁、真诚"),
        "n_questions": gen.get("n_questions", 20),
        "batch_size": gen.get("batch_size", 10),
        "fact_check": gen.get("fact_check", True),
        "dedup_file": gen.get("dedup_file", "data/output/identity_questions_manifest.txt"),
    }


def _identity_brief(cfg: Dict[str, Any]) -> str:
    return f"AI 助手『{cfg['model_name']}』由 {cfg['company']} 独立研发，用户会询问它的身份与出处。"


def _parse_json(output: str) -> Dict[str, Any]:
    from lib.llm_client import parse_json_robust

    return parse_json_robust(output)


class QuestionManifest:
    """问题级全局查重（sha256，跨运行零重复）。"""

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.ids = set()
        if self.path.exists():
            self.ids = {line.strip() for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()}

    def hash(self, question: str) -> str:
        norm = "".join(question.split())  # 去空白规范化（换行/空格不影响查重）
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def add(self, question: str) -> None:
        self.ids.add(self.hash(question))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(sorted(self.ids)) + "\n", encoding="utf-8")


def gen_questions(client: Any, cfg: Dict[str, Any], manifest: QuestionManifest, target: int) -> List[str]:
    """批量生成多样化问题，去重后达 target 条为止。"""
    questions: List[str] = []
    seen_text: List[str] = []
    rounds = 0
    while len(questions) < target and rounds < 8:
        rounds += 1
        batch = chat_json(client, [{"role": "user", "content": render(get("identity.question_variants"),
            identity_brief=_identity_brief(cfg),
            count=cfg["batch_size"],
            seen_questions="\n".join(f"- {q}" for q in seen_text) or "（无）",
        )}], temperature=1.0, thinking=False)["questions"]
        for q in batch:
            q = q.strip()
            if not q or len(q) < 4 or len(q) > 200:
                continue
            if manifest.hash(q) in manifest.ids:
                continue
            manifest.add(q)
            questions.append(q)
            seen_text.append(q)
            if len(questions) >= target:
                break
    manifest.save()
    return questions[:target]


def gen_answer(client: Any, cfg: Dict[str, Any], question: str) -> str:
    out = chat_json(client, [{"role": "user", "content": render(get("identity.answer"),
        question=question, facts=cfg["facts"],
        required_facts=cfg["required_facts"], style=cfg["style"])}],
        temperature=0.9, thinking=False)
    return out["answer"]


def fact_check(client: Any, question: str, answer: str, required_facts: str) -> Dict[str, Any]:
    return chat_json(client, [{"role": "user", "content": render(get("identity.fact_check"),
        question=question, answer=answer, required_facts=required_facts)}],
        temperature=0.2, thinking=False)


def run(client: Any, cfg: Dict[str, Any], answer_cap: int = 0) -> Dict[str, Any]:
    """主流程：生成问题 → 回答 → 事实校验 → 样本 + 多样性统计。answer_cap 为保护性截断上限。"""
    from lib.length import truncate_to_max

    manifest = QuestionManifest(cfg["dedup_file"])
    questions = gen_questions(client, cfg, manifest, cfg["n_questions"])

    samples: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for q in questions:
        try:
            answer = gen_answer(client, cfg, q)
            if answer_cap > 0:
                answer = truncate_to_max(answer, answer_cap)
            check = fact_check(client, q, answer, cfg["required_facts"]) if cfg["fact_check"] else {"keep": True}
        except Exception as e:  # noqa: BLE001
            rejected.append({"question": q[:80], "error": str(e)[:200]})
            continue
        sample = {
            "id": f"identity-{manifest.hash(q)}",
            "source": "identity",
            "type": "sft",
            "company": cfg["company"],
            "model_name": cfg["model_name"],
            "messages": [
                {"role": "user", "content": q},
                {"role": "assistant", "content": answer},
            ],
            "fact_check": check,
        }
        if check.get("keep", True):
            samples.append(sample)
        else:
            rejected.append({"question": q[:80], "check": check})

    openings = [s["messages"][1]["content"][:6] for s in samples]
    stats = {
        "questions": len(questions),
        "kept": len(samples),
        "rejected": len(rejected),
        "manifest_total": len(manifest.ids),
        "unique_openings": len(set(openings)),
        "opening_diversity": round(len(set(openings)) / max(len(openings), 1), 3),
    }
    return {"samples": samples, "rejected": rejected, "stats": stats}
