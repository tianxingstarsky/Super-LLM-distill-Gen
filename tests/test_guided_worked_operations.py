import json

from lib.contexts.workflows.application.review_guided_disclosure import review_or_recommend_disclosure


def test_a_withheld_operation_can_be_applied_to_the_next_students_actual_position():
    pair = "第3位与第4位必须不同"
    fixed = "现在第2位固定为0"
    question = f"一个15位二进制串不能含有111。{pair}。{fixed}。一的个数除以5余1，有多少个有效串？"
    steps = []
    for ident, quote, operation in (("pair", pair, "检查这两位的关系"), ("fixed", fixed, "更新固定位置")):
        offset = question.index(quote)
        steps.append(dict(id=ident, kind="operation", quote=quote, span=[offset, offset+len(quote)], operation=operation))
    source = dict(question=question, gold={"kind": "number", "value": "988"},
                  verified_teaching_steps={"receipt_id": "bound-catalogue", "steps": steps})
    published, tried, reviewed = [], set(), []

    def request(role, index, messages, schema):
        packet = json.loads(messages[-1]["content"])
        previous = packet["latest_disclosure_id"]
        if role in {"disclosure_draft", "disclosure_rewrite"}:
            available = [s["id"] for s in packet["available_operations"]]
            assert available == (["pair", "fixed"] if not previous else ["pair"])
            if role == "disclosure_rewrite":
                decision, quote, bridge, hint = "fixed", fixed, "原设置已被替换。", "从左往右的第二格现在只有零这一种取值。"
            elif previous:
                decision, quote, bridge, hint = "pair", pair, "保留刚固定的零，再看相邻两格。", "它们只能是01或10，所以这三格合计只有一个一。"
            else:
                decision, quote, bridge, hint = "pair", pair, "现在就开始分类。", "先记录相邻位置。"
            result = dict(decision="apply:" + decision, reason="局部约束", bridge=bridge, hint=hint, question_quote=quote)
        else:
            proposal = packet["proposed_next_teacher_disclosure"]
            reviewed.append(proposal["public_text"])
            approved = len(reviewed) > 1
            citations = [{"evidence_id": "question", "quote": proposal["question_quote"]}]
            if previous:
                citations.append({"evidence_id": previous, "quote": published[-1]["hint"]})
            result = dict(decision="release" if approved else "withhold",
                          bridge=proposal["bridge"] if approved else "", citations=citations if approved else [],
                          reason="已连接具体前提" if approved else "没有指出哪两位或推得什么",
                          pairing=dict(evidence_support=True, student_fit=approved, history_fit="continuous" if previous else "first"))
        return dict(content=json.dumps(result, ensure_ascii=False), reasoning_content="", finish_reason="stop")

    for index in (1, 2):
        attempt = dict(attempt=index, student={"content": "862"}, check={"format_ok": True, "answer_matches": False},
                       derivation_available=False)
        reference = dict(ordinal=index, kind="operation", text="", replaces_from="final_answer")
        hint, issue = review_or_recommend_disclosure(request, source, attempt, [], {}, published, reference,
                                                    teacher_experience="", tried=tried, quoted_premises=True,
                                                    contextual_operations=True)
        assert issue is None, issue
        assert hint["text"] == reviewed[-1]
        published.append({**hint, "hint": hint["candidate_text"]})
    assert [h["source_step_id"] for h in published] == ["fixed", "pair"]
    assert reviewed[0] not in [h["text"] for h in published]
    assert "01或10" in published[1]["text"] and "相邻" in published[1]["bridge"]
