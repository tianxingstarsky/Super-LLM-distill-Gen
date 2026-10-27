"""Narrow observable regressions, never a substitute for semantic assessment."""
import re

from lib.domain.training_surface import _VISIBLE_ARTICLE_BLOCK
from lib.domain.knowledge_voice import _READING_REQUEST, _QUOTED_DISCUSSION


def observable_surface_defects(context, candidate, phase):
    messages = context.get("prompt", context.get("messages", []))
    users = [m.get("content", "") for m in messages if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)]
    if not users:
        return []
    visible, current = "\n".join(users), users[-1]
    article = bool(_VISIBLE_ARTICLE_BLOCK.search(current) or _READING_REQUEST.search(current))
    defects = []
    if not article:
        pattern = re.compile(r"(?:资料|材料|原文|文档)(?:也|还)?(?:明确|直接)(?:说|说明|支持|表明|规定)")
        for field, text in candidate.items():
            for match in pattern.finditer(text):
                begin = max(text.rfind("。", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
                finish = text.find("。", match.end())
                sentence = text[begin:finish + 1 if finish >= 0 else len(text)]
                if (_QUOTED_DISCUSSION.search(sentence) or re.search(r"如果|假如|例如|比如", sentence[:match.start()-begin])
                        or match[0] in visible):
                    continue
                defects.append({"kind": "provenance", "field": field, "quote": sentence,
                                "issue": "hidden_source_narration", "rule_id": "learner_surface"})
    if phase == "final":
        # Only a gross language reversal, excluding quoted passages and explicit
        # translation/multilingual requests. Ambiguous language stays semantic.
        question = _VISIBLE_ARTICLE_BLOCK.sub("", current)
        override = re.search(r"翻译|译成|中文回答|英文回答|用中文|用英文|translate|translation|in Chinese|in Mandarin|bilingual|中英", question, re.I)
        answer = re.sub(r"```[\s\S]*?```|“[^”]*”|\"[^\"]*\"", "", candidate.get("answer", ""))
        en_question = len(re.findall(r"\b[A-Za-z]+\b", question))
        zh_question = len(re.findall(r"[\u4e00-\u9fff]", question))
        en_answer = len(re.findall(r"\b[A-Za-z]+\b", answer))
        zh_answer = len(re.findall(r"[\u4e00-\u9fff]", answer))
        if not override and en_question >= 10 and zh_question <= 4 and zh_answer >= 40 and en_answer < zh_answer / 3:
            defects.append({"kind": "task", "field": "answer", "quote": candidate["answer"],
                            "issue": "answer_language_mismatch_en", "rule_id": "answer_language"})
    return defects
