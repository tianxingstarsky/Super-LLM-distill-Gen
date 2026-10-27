"""Content-bound evidence scope. Page coverage is not proof of extraction accuracy."""
from copy import deepcopy
import hashlib

from .reasoning_checklist import checklist_digest


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_inventory(context):
    """Validate optional import coverage; unknown/selected sources stay partial.

    The importer owns the original-file digest. This pure layer verifies the
    supplied text and page spans, never infers completeness from text length.
    """
    source = context.get("source", context.get("source_context"))
    inventory = {"version": 1, "coverage": "unknown", "original_file_verified": False,
                 "content_sha256": checklist_digest(source), "pages": [],
                 "limits": "Text coverage does not certify formulas, figures, tables or extraction order."}
    if not isinstance(source, dict):
        return inventory
    if context.get("source_line_scope") == "selected" or source.get("source_line_scope") == "selected":
        return {**inventory, "coverage": "selected_ranges"}
    manifest = source.get("coverage_manifest")
    if manifest is None:
        return inventory
    text = source.get("text")
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or not isinstance(text, str) or manifest.get("text_sha256") != text_sha256(text)
            or type(manifest.get("page_count")) is not int or manifest["page_count"] < 1
            or not isinstance(manifest.get("pages"), list)):
        raise ValueError("source_coverage_manifest_invalid")
    pages, end = [], 0
    for page in manifest["pages"]:
        if (not isinstance(page, dict) or type(page.get("page")) is not int
                or type(page.get("start")) is not int or type(page.get("end")) is not int
                or page["start"] != end or not page["start"] < page["end"] <= len(text)
                or page.get("text_sha256") != text_sha256(text[page["start"]:page["end"]])):
            raise ValueError("source_coverage_page_invalid")
        end = page["end"]
        pages.append(deepcopy(page))
    if (end != len(text) or [p["page"] for p in pages] != list(range(1, manifest["page_count"] + 1))):
        raise ValueError("source_coverage_pages_missing")
    return {**inventory, "coverage": "all_text_pages", "pages": pages,
            "original_sha256": manifest.get("original_sha256"),
            "text_sha256": manifest["text_sha256"], "page_count": manifest["page_count"],
            "original_file_verified": context.get("source_import_verified") is True}


EVIDENCE_REQUIREMENTS = [
    {"id": "evidence_scope", "allow_na": False,
     "requirement": "区分用户可见段落、教师知识、论文全文和当前检索片段。论文级事实须与可用原文相符；查无匹配或只见选段不证明全文不存在。用户限定给定段落时，不能把外部细节说成段内事实；额外建议要明确归属。已知来源缺失不能靠作者或评审补条件。"},
    {"id": "learner_surface", "allow_na": False,
     "requirement": "核对实际用户任务和每个被监督的回答。非阅读任务的答案和解释直接运用知识，不叙述后台资料、评审或生成规则；可以正确归属命名研究的事实。阅读例外仅来自用户当时可见的内容。前文助手不是事实依据，当前稿必须纠正影响任务的前文错误，不能继承错误单位或因果结论。"},
    {"id": "answer_language", "allow_na": False, "phases": ["final"],
     "requirement": "单独核对最终答案语言：服从用户明确指定的语言，否则跟随当前用户问题的语言。中英混合推理仅约束推理，不能豁免答案语言；引文或术语允许保留原文。引用用户问题和答案的实际语言作核对，不因内容正确跳过。"},
]


def evidence_rules(rules):
    if {r["id"] for r in rules}.intersection(r["id"] for r in EVIDENCE_REQUIREMENTS):
        raise ValueError("reserved_evidence_rule_id")
    return deepcopy(rules + EVIDENCE_REQUIREMENTS)


def review_context_identity(context):
    # Only production-owned receipt metadata is excluded. Questions, full
    # history, evidence, style and every instruction remain bound.
    return checklist_digest({k: v for k, v in context.items() if k != "verified_prefix_review"})


def reusable_prefix(receipt, context, candidate, rules):
    if not isinstance(receipt, dict) or receipt.get("passed") is not True or receipt.get("phase") != "prefix":
        return None
    if (receipt.get("candidate_sha256") != checklist_digest({"reasoning": candidate.get("reasoning")})
            or receipt.get("rules_sha256") != checklist_digest(rules)
            or receipt.get("review_context_sha256") != review_context_identity(context)
            or receipt.get("evidence_policy") != 1):
        return None
    return {"candidate_sha256": receipt["candidate_sha256"], "phase": "prefix",
            "verified": True, "reuse_scope": "Unchanged reasoning only; review the whole new answer, answer/reasoning consistency, task, language and source attribution. Reopen reasoning if the answer exposes a contradiction.",
            "checks": {role: deepcopy(receipt.get(role, {}).get("checks", []))
                       for role in ("self_check", "independent_check")},
            "resolutions": deepcopy(receipt.get("resolutions", []))}
