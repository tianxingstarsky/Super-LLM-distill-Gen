"""提示词真机评测：每个用例 = 提示词 + 输入变量 + 结构检查（离线可判定的契约）。

结构检查只做"可离线判定"的部分（JSON 键、正则、长度、语言占比等）；
语义质量由人工抽检（报告中的输出样例）。
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from lib.prompts import get, render


def _json_keys(*keys: str) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        try:
            obj = json.loads(output.strip())
        except json.JSONDecodeError as e:
            return False, f"非法 JSON: {e}"
        missing = [k for k in keys if k not in obj]
        if missing:
            return False, f"缺少键: {missing}"
        return True, f"JSON 键齐全: {keys}"
    return check


def _contains(text: str, name: str) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        return (text in output, f"含『{name}』" if text in output else f"缺『{name}』")
    return check


def _cjk_ratio_lt(threshold: float) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", output))
        ratio = cjk / max(len(output), 1)
        ok = ratio < threshold
        return ok, f"中文占比 {ratio:.2f}{'<' if ok else '≥'}{threshold}"
    return check


def _cjk_ratio_gt(threshold: float) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", output))
        ratio = cjk / max(len(output), 1)
        ok = ratio > threshold
        return ok, f"中文占比 {ratio:.2f}{'>' if ok else '≤'}{threshold}"
    return check


def _json_field_cjk(field: str, threshold: float, greater: bool) -> Callable[[str], Tuple[bool, str]]:
    """对 JSON 输出的指定字段做中文占比检查（避免整段 JSON 键名干扰）。"""

    def check(output: str) -> Tuple[bool, str]:
        try:
            obj = json.loads(output.strip())
            text = str(obj.get(field, ""))
        except json.JSONDecodeError as e:
            return False, f"非法 JSON: {e}"
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        ratio = cjk / max(len(text), 1)
        ok = ratio > threshold if greater else ratio < threshold
        return ok, f"{field} 中文占比 {ratio:.2f}"
    return check


def _json_list_len(field: str, expected: int) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        try:
            obj = json.loads(output.strip())
            n = len(obj.get(field, []))
        except (json.JSONDecodeError, TypeError) as e:
            return False, f"解析失败: {e}"
        return (n == expected, f"{field} 数量 {n}")
    return check


def _len_between(lo: int, hi: int) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        ok = lo <= len(output.strip()) <= hi
        return ok, f"长度 {len(output.strip())}"
    return check


def _not_matching(pattern: str, name: str) -> Callable[[str], Tuple[bool, str]]:
    def check(output: str) -> Tuple[bool, str]:
        ok = re.search(pattern, output, re.IGNORECASE) is None
        return ok, f"无『{name}』模式" if ok else f"命中『{name}』模式"
    return check


@dataclass
class EvalCase:
    name: str
    prompt_id: str
    variables: Dict[str, Any]
    checks: List[Tuple[str, Callable[[str], Tuple[bool, str]]]] = field(default_factory=list)


DEFAULT_CASES: List[EvalCase] = [
    EvalCase("magpie.query 生成", "magpie.query", {}, [
        ("非空", _len_between(8, 400)),
        ("无问候模板", _not_matching(r"^(hello|hi|你好|您好)[\s!,.]", "问候语模板")),
    ]),
    EvalCase("distill.reflector 质检", "distill.reflector", {
        "goal": "帮我写一个判断回文的函数",
        "history_steps": "- assistant_answer(\"用切片反转实现\")",
        "last_step": "- run_shell(ls) [错误]",
    }, [("JSON 键", _json_keys("redundant", "incorrect", "error_type", "lesson"))]),
    EvalCase("distill.generator 反思", "distill.generator", {
        "goal": "写一个不额外分配空间的回文判断函数",
        "annotated_steps": "- assistant_answer(切片反转) [错误]\n- assistant_answer(双指针实现) [正确]",
        "style_guide": "默认风格",
    }, [
        ("JSON 键", _json_keys("thinking", "final_answer")),
        ("反思结构", _contains("改用", "改用")),
    ]),
    EvalCase("judge.score 打分", "judge.score", {
        "goal": "判断回文",
        "thinking": "原计划切片反转，发现多占空间，改用双指针。",
        "final_answer": "双指针实现",
    }, [("JSON 键", _json_keys("correctness", "alignment", "efficiency", "lesson_quality", "keep"))]),
    EvalCase("translation.zh2en 中译英", "translation.zh2en", {
        "text": "机器学习模型在训练数据上拟合规律，并在未见数据上做预测。",
    }, [("JSON 键", _json_keys("translation", "terms")), ("译文英文占比", _json_field_cjk("translation", 0.1, greater=False))]),
    EvalCase("translation.en2zh 英译中", "translation.en2zh", {
        "text": "Machine learning models learn patterns from data and make predictions.",
    }, [("JSON 键", _json_keys("translation", "terms")), ("译文中文占比", _json_field_cjk("translation", 0.3, greater=True))]),
    EvalCase("translation.bridge_zh2en 桥接", "translation.bridge_zh2en", {
        "question": "什么是过拟合？",
    }, [("JSON 键", _json_keys("english", "zh_summary"))]),
    EvalCase("translation.backcheck 回译校验", "translation.backcheck", {
        "original": "模型在训练集上表现很好但在测试集上很差。",
        "back_translation": "The model performs well on training but poorly on test.",
    }, [("JSON 键", _json_keys("faithful", "score", "issues"))]),
    EvalCase("identity.question_variants 问题变体", "identity.question_variants", {
        "identity_brief": "AI 助手『示例-1』由示例科技独立研发",
        "count": 5,
        "seen_questions": "（无）",
    }, [("JSON 键", _json_keys("questions")), ("恰好 5 条", _json_list_len("questions", 5))]),
    EvalCase("identity.answer 身份回答", "identity.answer", {
        "question": "你是谁？谁开发的？",
        "facts": "- 我是由示例科技独立研发的大语言模型示例-1。\n- 未基于任何第三方开源模型改造。",
        "required_facts": "- 由示例科技独立研发\n- 模型名为示例-1\n- 未基于第三方开源模型",
        "style": "专业、简洁、真诚",
    }, [("JSON 键", _json_keys("answer"))]),
    EvalCase("identity.fact_check 事实校验", "identity.fact_check", {
        "question": "你是谁？",
        "answer": "我是由示例科技独立研发的大语言模型示例-1，未基于任何第三方开源模型改造。",
        "required_facts": "- 由示例科技独立研发\n- 模型名为示例-1\n- 未基于第三方开源模型",
    }, [("JSON 键", _json_keys("complete", "contradictions", "natural", "keep"))]),
    EvalCase("document.qa_gen 文档问答", "document.qa_gen", {
        "chunk": "本系统支持 Markdown、PDF 与 Word 文档导入。清洗阶段去除纯页码行。分块按段落边界合并，Markdown 标题作为硬边界。",
        "n": 3,
    }, [("JSON 键", _json_keys("qa")), ("恰好 3 条", _json_list_len("qa", 3))]),
    EvalCase("document.instruction_gen 任务指令", "document.instruction_gen", {
        "chunk": "本系统支持 Markdown、PDF 与 Word 文档导入。清洗阶段去除纯页码行。",
    }, [("JSON 键", _json_keys("instructions"))]),
    EvalCase("document.ground_check 依据校验", "document.ground_check", {
        "chunk": "本系统支持 Markdown、PDF 与 Word 文档导入。",
        "question": "系统支持哪些文档格式？",
        "answer": "系统支持 Markdown、PDF、Word，以及 Epub 格式。",
    }, [("JSON 键", _json_keys("grounded", "unsupported", "keep"))]),
]


def run_case(client: Any, case: EvalCase) -> Dict[str, Any]:
    prompt = render(get(case.prompt_id), **case.variables)
    # 与生产路径一致：json_mode 解码层强制 JSON + 容错解析
    from lib.llm_client import parse_json_robust

    output = client.chat([{"role": "user", "content": prompt}],
                         max_tokens=None, temperature=0.2, thinking=False, json_mode=True)
    try:
        output = json.dumps(parse_json_robust(output), ensure_ascii=False)
    except Exception:  # noqa: BLE001 —— 保留原始输出供检查报告诊断
        pass
    results = []
    for name, check in case.checks:
        try:
            ok, detail = check(output)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"检查异常: {e}"
        results.append({"name": name, "ok": ok, "detail": detail})
    return {
        "case": case.name,
        "prompt_id": case.prompt_id,
        "output": output[:400],
        "checks": results,
        "passed": all(r["ok"] for r in results),
    }


def run_all(client: Any, cases: List[EvalCase] | None = None, ids: List[str] | None = None) -> List[Dict[str, Any]]:
    cases = cases or DEFAULT_CASES
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c.prompt_id in wanted or c.name in wanted]
    return [run_case(client, c) for c in cases]


CHECK_TYPES = {
    "json_keys": lambda *keys: _json_keys(*keys),
    "contains": lambda text, name=None: _contains(text, name or text),
    "list_len": lambda field, n: _json_list_len(field, int(n)),
    "cjk_field": lambda field, threshold, greater: _json_field_cjk(field, float(threshold), str(greater).lower() == "true"),
}


def load_cases(path: str) -> List[EvalCase]:
    """从 YAML/JSON 用例文件加载自定义评测用例。

    用例格式: [{name, prompt_id, variables: {...},
                 checks: [{type: json_keys, args: [a, b]} | {type: contains, args: [text]}]}]
    """
    import yaml

    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):  # 单用例文件也接受（dict → [dict]）
        raw = [raw]
    cases = []
    for item in raw:
        checks = []
        for ch in item.get("checks", []):
            fn = CHECK_TYPES.get(ch["type"])
            if fn is None:
                raise ValueError(f"未知检查类型: {ch['type']}（可用: {sorted(CHECK_TYPES)}）")
            checks.append((ch.get("name", ch["type"]), fn(*ch.get("args", []))))
        cases.append(EvalCase(
            name=item["name"], prompt_id=item["prompt_id"],
            variables=item.get("variables", {}), checks=checks,
        ))
    return cases
