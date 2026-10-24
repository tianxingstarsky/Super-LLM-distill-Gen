"""Versioned model response shapes and safe format diagnostics."""
from __future__ import annotations


from ..domain.automatic_rules import _AUTHORED_REVIEW_RULES


_CLAIM_REVIEW_WIRE_PROTOCOL = (
    "本次调用使用固定的claim_review_protocol_version=1传输契约；节点自定义文本决定语义评审标准，"
    "本说明只规定返回shape，不添加评审步骤或内容规则。仅返回JSON对象，字段必须且仅为："
    "claim_review_protocol_version（整数1）、scope（完整原样复制request.scope）、"
    "coverage（complete或partial）、checked_fields（target_fields中的字段名数组）、"
    "claim_checks（非空数组）、verdict（既定keep、grounded、reasoning_valid布尔值，correctness整数1至5，"
    "scores含correctness/reasoning/grounding/instruction/safety整数1至5，reason非空字符串）。"
    "每个claim_checks项目必须且仅有field、quote、status（supported/refuted/insufficient）、"
    "severity（material/minor）、basis。basis必须且仅有kind（source/context/logic）、evidence数组、"
    "derivation非空字符串。source引用项目是quote加source_path路径数组，路径相对独立来源记忆中的"
    "source_evidence对象；context引用项目是quote加message_index和field；logic可用空evidence。"
    "quote与路径对应实际输入字段中的原文；不返回旧六字段顶层或turn_verdicts。"
)


_BLIND_REFERENCE_WIRE_PROTOCOL = (
    "本次使用claim_review_protocol_version=2、phase=blind_reference固定传输契约；"
    "自定义节点文本决定语义标准，本说明只规定shape。仅返回JSON且只有"
    "claim_review_protocol_version（整数2）、phase（blind_reference）、scope（原样复制request.scope）、"
    "coverage（complete/partial）、reference_points（非空数组）、limitations（字符串数组）。"
    "每个reference_points项目仅有id、point、severity（material/minor）、basis；"
    "basis仅有kind（source/context/logic）、evidence数组、derivation非空字符串。"
    "source引文项为quote和相对来源记忆source_evidence对象的source_path数组；"
    "context引文项为quote、message_index、field；logic可用空evidence。"
)


_CLAIM_REVIEW_V2_WIRE_PROTOCOL = (_CLAIM_REVIEW_WIRE_PROTOCOL
    .replace("claim_review_protocol_version=1", "claim_review_protocol_version=2")
    .replace("（整数1）", "（整数2）")
    .replace("claim_checks（非空数组）、verdict", "claim_checks（非空数组）、reference_checks（数组）、verdict")
    + "reference_checks对reference_review.reference_points的每个id恰好一次，"
      "每项只有id、relation（consistent/contradicted/not_claimed/uncertain/reference_error）、field、quote、reason。"
      "not_claimed时field=null且quote为空字符串；其它relation需field为当前target_fields真实字段且quote逐字匹配。")


_CLAIM_REVIEW_V2_WIRE_PROTOCOL += (
    "仅reference_error项另加counter_evidence，结构为kind（source/context）、evidence真实引文数组、"
    "derivation具体反证；同时claim_checks中同field和quote须有source/context真引文支持的supported项目。")

_CLAIM_REVIEW_V3_WIRE_PROTOCOL = (
    "本次使用claim_review_protocol_version=3固定传输契约；自定义节点文本决定语义标准，"
    "本说明只规定唯一返回shape。仅返回JSON且只有claim_review_protocol_version（整数3）、"
    "scope_sha256（复制request.scope.scope_sha256）、coverage（complete/partial）、checked_unit_ids、"
    "claim_checks、reference_checks、verdict。checked_unit_ids只使用本次target_units实际unit_id。"
    "claim_checks每项仅有unit_id、status（supported/refuted/insufficient）、severity（material/minor）、"
    "basis；不抄写候选quote、field或整个scope。basis仅有kind（source/context/logic）、evidence、"
    "derivation；source引文项为quote和source_path，context引文项为quote、message_index、field，"
    "logic的evidence可为空数组。reference_checks每项只有id、relation、unit_id、reason；"
    "relation为consistent/contradicted/not_claimed/uncertain/reference_error，not_claimed用unit_id=null，"
    "其它relation仅选本次target_units实际unit_id；仅reference_error另有counter_evidence，"
    "其结构为kind（source/context/logic）、evidence和derivation。verdict保留既定六字段与scores结构。")


_CLAIM_CHALLENGE_V4_WIRE_PROTOCOL = (
    "本次使用协议4、phase=claim_challenge的固定传输契约；语义要求以可编辑节点模板为准。"
    "仅返回JSON且只有claim_review_protocol_version（整数4）、phase（claim_challenge）、"
    "scope_sha256（复制request.scope.scope_sha256）、issues和limitations。issues每项仅有id、unit_id、"
    "severity（material/minor）、problem、counterexample、basis。id是唯一1至128位字母数字或_.-字符串；"
    "unit_id只能来自本次target_units。basis仅kind（source/context/logic）、evidence、derivation；"
    "source引文为quote和source_path，context引文为quote、message_index、field，logic的evidence可为空数组。"
    "limitations是字符串数组。不返回评分、scope、候选quote或其它阶段字段。")


_CLAIM_ADJUDICATION_V4_WIRE_PROTOCOL = (
    "本次使用协议4、phase=claim_adjudication固定传输契约；语义要求以可编辑节点模板为准。"
    "仅返回JSON且只有claim_review_protocol_version（整数4）、scope_sha256（复制request.scope.scope_sha256）、"
    "coverage、checked_unit_ids、claim_checks、challenge_checks、verdict。coverage为complete/partial。"
    "checked_unit_ids及claim_checks.unit_id只用本次target_units的id。claim_checks每项仅unit_id、"
    "status（supported/refuted/insufficient）、severity（material/minor）、basis。challenge_checks每项仅id、"
    "unit_id、decision（confirmed/dismissed/unresolved）、basis；id与unit_id绑定实际challenge_review的同一issue。"
    "basis仅kind（source/context/logic）、evidence、derivation；source引文为quote和source_path，context引文为"
    "quote、message_index、field，logic的evidence可为空数组。verdict保留既定六字段与scores结构。"
    "不要抄候选quote、field、完整scope或reference_checks，不将程序normalized表示冒称模型原始返回。")


_CLAIM_FORMAT_ERROR_CODES = frozenset({
    "invalid_blind_review_response", "stale_blind_review_response", "invalid_blind_review_scope",
    "invalid_blind_review_coverage", "missing_blind_review_reference_points", "invalid_blind_review_reference_point",
    "invalid_blind_review_reference_id", "missing_blind_review_reference_text", "invalid_blind_review_severity",
    "invalid_blind_review_basis", "missing_blind_review_derivation", "vacuous_blind_review_derivation",
    "missing_blind_review_evidence", "invalid_blind_review_limitations", "invalid_blind_review_limitation",
    "invalid_claim_review_v2_response", "stale_claim_review_v2_response", "invalid_claim_review_response",
    "invalid_claim_review_coverage", "invalid_claim_review_checked_fields", "missing_claim_review_claims",
    "invalid_claim_review_claim", "missing_claim_review_quote", "fabricated_claim_review_quote",
    "duplicate_claim_review_claim", "invalid_claim_review_claim_status", "invalid_claim_review_basis",
    "missing_claim_review_derivation", "vacuous_claim_review_derivation", "missing_claim_review_evidence",
    "invalid_claim_review_evidence_reference", "missing_claim_review_evidence_quote", "invalid_claim_review_source_path",
    "invalid_claim_review_context_reference", "fabricated_claim_review_evidence_quote", "claim_review_unchecked_field",
    "incomplete_claim_review_fields", "invalid_claim_review_verdict_fields", "invalid_judge_schema",
    "invalid_judge_boolean", "invalid_judge_score", "missing_judge_reason", "invalid_jev_dimensions",
    "incomplete_claim_review_reference_checks", "invalid_claim_review_reference_check", "invalid_claim_review_reference_check_id",
    "invalid_claim_review_reference_relation", "missing_claim_review_reference_reason", "invalid_claim_review_not_claimed_reference",
    "fabricated_claim_review_reference_quote", "unsubstantiated_claim_review_reference_error",
    "invalid_challenge_review_response", "stale_challenge_review_response", "invalid_challenge_review_issues",
    "invalid_challenge_review_issue", "invalid_challenge_review_issue_id", "invalid_challenge_review_unit_id",
    "invalid_challenge_review_severity", "missing_challenge_review_problem", "missing_challenge_review_counterexample",
    "invalid_challenge_review_limitations", "missing_challenge_review_limitation", "invalid_adversarial_review_response",
    "stale_adversarial_review_response", "invalid_adversarial_unit_coverage", "invalid_adversarial_claim",
    "invalid_adversarial_unit_id", "invalid_adversarial_challenge_checks", "invalid_adversarial_challenge_check",
    "invalid_adversarial_challenge_decision", "unbound_adversarial_challenge_check", "unconfirmed_adversarial_refutation",
    "invalid_adversarial_basis", "missing_adversarial_derivation", "vacuous_adversarial_derivation",
    "missing_adversarial_evidence",
})


_CONDITION_INVENTORY_WIRE_PROTOCOL = (
    "协议4 condition_contract_version=1。本阶段只登记原文条件，不作对错挑战。"
    "返回claim_review_protocol_version=4、phase=claim_challenge、scope_sha256、issues=[]、"
    "condition_ledger、limitations。condition_ledger对每个当前unit恰好一项unit_id/premises。"
    "premises及引用严格使用response_json_schema，来源只定位原文、不证明推断。")


_CONDITION_ADJUDICATION_WIRE_PROTOCOL = (
    "协议4 condition_contract_version=1。本阶段逐原文检查段评审，不补条件、不替作者修稿。"
    "保留既定coverage/checked_unit_ids/claim_checks/challenge_checks/verdict；"
    "每条claim_checks新增必填premises，逐项记录实际使用的条件来源。challenge_checks=[]。"
    "新增假设不能证明原句，无法核清用insufficient。严格使用response_json_schema。")


CONDITION_REVIEW_COMPLETE_WIRE_PROTOCOL = (
    "\n完整传输合同（不改变本节点评审标准）：返回一个JSON对象，不能省字段或把分数平铺。"
    "裁决verdict必须且仅有keep、grounded、reasoning_valid（布尔），correctness（1到5整数），"
    "scores（对象且仅含correctness、reasoning、grounding、instruction、safety五项1到5整数），reason（非空文字）。"
    "不要使用answer_correct、leakage_free、critical_error或平铺的instruction/reasoning等替代字段。"
    "它们属于不同协议；语义上都正确也不能替代完整格式。basis必须含kind、evidence数组、derivation。"
    "condition_ledger与claim_checks中的每项都必须有premises数组，无需额外条件时用空数组，不捏造条件。"
    "若存在review_window，完整messages、target_fields、target_units和source_evidence仍可通读，"
    "但本次只返回review_window.unit_ids中每项一次；scope_sha256复制review_window.sha256。"
    "其它unit可作为条件或指代的原文锚点，不能以本次未评审为理由忽略相关上下文。"
    "condition_ledger_view若存在，challenge_review仅是当前窗口的条件登记视图，完整登记另存审计；"
    "其余登记未重复发送不表示其余原文不可见。登记是可错的提示，不是来源事实；仍独立核对完整原文。"
    "登记阶段仍只返回phase、scope_sha256、issues=[]、condition_ledger、limitations和协议整数4，不能返回verdict。"
    "裁决阶段不返回phase或condition_ledger，challenge_checks=[]。格式反馈仅说明必填字段、类型、路径，"
    "没有提供事实条件或预设分数；仍须独立判断原文。不要重复抄写整段资料或候选，保留判断必要的实际依据。\n")


AGGREGATE_REVIEW_COMPLETE_WIRE_PROTOCOL = (
    "\n本次是普通候选/偏好评分，不是逐句协议4。仅返回评分对象：keep、grounded、reasoning_valid为布尔，"
    "correctness为1到5整数，scores对象必须且仅有correctness、reasoning、grounding、instruction、safety五项1到5整数，"
    "reason为具体非空理由。不返回verdict包装、claim_checks、condition_ledger或scope_sha256。"
    "偏好候选核验chosen的质量、prompt的真实条件、chosen/rejected的实际差异；拒绝回答是刻意负例，"
    "不能要求它本身正确才认可整对数据。单独rejected_score按实际质量打分，低分不算格式错误。"
    "输入未提供supervised_assistant_turns时不返回turn_verdicts；提供时按列出的索引与哈希逐项返回。\n")


def _claim_format_boundaries(request, phase):
    """Describe literal input paths/indices without echoing any text value."""
    messages = request["messages"]
    allowed_fields = {"role", "content", "name", "reasoning_content", "reasoning", "thinking", "tool_call_id", "toolCallId"}
    limit = len(messages) if phase == "blind_reference" else request["scope"]["message_index"]
    provenances = {"assistant": "prior_assistant_claim_not_verified", "user": "user_report_or_request_not_external_fact",
                   "tool": "tool_record", "system": "conversation_instruction"}
    table = []
    for index, message in enumerate(messages):
        role = message.get("role")
        role = role if isinstance(role, str) and role in provenances else "unknown"
        table.append({"message_index": index, "role": role, "provenance": provenances.get(role, "unknown"),
                      "text_fields": sorted(field for field in allowed_fields if isinstance(message.get(field), str)),
                      "context_reference_allowed": index < limit})
    response_fields = (["claim_review_protocol_version", "phase", "scope", "coverage", "reference_points", "limitations"]
        if phase == "blind_reference" else ["claim_review_protocol_version", "scope", "coverage", "checked_fields",
                                               "claim_checks", "reference_checks", "verdict"])
    result = {"messages_index_table": table, "valid_context_message_indices": list(range(min(limit, len(messages)))),
            "current_assistant_message_index": request["scope"]["message_index"],
            "scope_field_names": sorted(request["scope"]), "response_field_names": response_fields}
    if request.get("claim_review_protocol_version") == 3:
        result["scope_field_names"] = []
        result["response_field_names"] = ["claim_review_protocol_version", "scope_sha256", "coverage",
            "checked_unit_ids", "claim_checks", "reference_checks", "verdict"]
        result["current_target_unit_ids"] = [item["id"] for item in request["target_units"]]
    elif request.get("claim_review_protocol_version") == 4:
        result["scope_field_names"] = []
        result["response_field_names"] = (["claim_review_protocol_version", "phase", "scope_sha256", "issues", "limitations"]
            if phase == "claim_challenge" else ["claim_review_protocol_version", "scope_sha256", "coverage",
                "checked_unit_ids", "claim_checks", "challenge_checks", "verdict"])
        result["current_target_unit_ids"] = [item["id"] for item in request["target_units"]]
        if phase == "claim_adjudication":
            result["current_challenge_issue_ids"] = [item["id"] for item in request["challenge_review"]["issues"]]
        if request.get("condition_contract_version") == 1 and phase == "claim_challenge":
            result["response_field_names"].append("condition_ledger")
    return result


def _safe_claim_format_code(error, phase, protocol_version=None):
    code = str(error)
    return code if code in _CLAIM_FORMAT_ERROR_CODES else (
        "invalid_blind_review_response" if phase == "blind_reference" else
        "invalid_challenge_review_response" if protocol_version == 4 and phase == "claim_challenge" else
        "invalid_adversarial_review_response" if protocol_version == 4 else
        "invalid_claim_review_v3_response" if protocol_version == 3 else "invalid_claim_review_v2_response")


def _claim_format_quote_diagnostic(raw, request, phase, *, quote_matching_version=None, error_code=None):
    """Locate schema mistakes using only program-derived paths and indices.

    Neither the rejected quote nor an exception value is copied into feedback.
    A presentation-only match in the current target is not reported as a stale
    previous-turn quote. The next model reply must still fix and validate it.
    """
    from lib.domain.review_quote_binding import bind_candidate_quote
    result = {}
    if not isinstance(raw, dict):
        return result
    if phase == "claim_review":
        failures = []
        target_index = request["scope"]["message_index"]
        for collection in ("claim_checks", "reference_checks"):
            items = raw.get(collection)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items[:32]):
                if not isinstance(item, dict) or item.get("relation") == "not_claimed":
                    continue
                field, quote = item.get("field"), item.get("quote")
                if (not isinstance(field, str) or field not in request["target_fields"]
                        or not isinstance(quote, str) or not quote.strip() or len(quote) > 20_000):
                    continue
                path = [collection, index, "quote"]
                try:
                    bind_candidate_quote(request["target_fields"][field], quote, field=field,
                        response_path=path, version=quote_matching_version)
                except ValueError:
                    prior = []
                    for message_index, message in enumerate(request["messages"][:target_index]):
                        if message.get("role") != "assistant":
                            continue
                        prior_fields = {"content", "reasoning_content", "reasoning", "thinking", "analysis"}
                        for previous_field in prior_fields:
                            text = message.get(previous_field)
                            if not isinstance(text, str):
                                continue
                            try:
                                bind_candidate_quote(text, quote, field=previous_field,
                                    response_path=path, version=quote_matching_version)
                            except ValueError:
                                continue
                            prior.append(message_index)
                            break
                    failures.append({"response_path": path, "field": field,
                        "target_message_index": target_index, "actual_previous_message_indices": prior})
        if failures:
            result["unmatched_candidate_quotes"] = failures[:32]
    if error_code in {"invalid_claim_review_source_path", "fabricated_claim_review_evidence_quote"}:
        from lib.infrastructure.claim_review_wire_schema import source_leaf_paths
        result.update(source_leaf_paths(request.get("source_evidence")))
    return result
