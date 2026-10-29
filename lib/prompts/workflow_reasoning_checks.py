"""Checklist, claim and tool-assisted reasoning-check prompt versions."""
from lib.prompts.workflow_reasoning_checklist import SELF_CHECK, INDEPENDENT_CHECK
from lib.prompts.workflow_spec import spec
from lib.prompts.workflow_reasoning_claims import (
    CLAIM_SELF_CHECK, CLAIM_REVIEW, CLAIM_SCHEMA, claim_protocol_v12,
)
from dataclasses import replace as _reasoning_spec_replace
from lib.prompts.workflow_reasoning_agent import REASONING_AGENT
from lib.prompts.workflow_reasoning_agent_recovery import (
    REASONING_AGENT_RECOVERY_V1_1, REASONING_AGENT_RECOVERY,
)
from lib.prompts.workflow_reasoning_environment import REASONING_ENVIRONMENT
from lib.prompts.workflow_semantic_review import SEMANTIC_REVIEW
from lib.prompts.workflow_evidence_review import EVIDENCE_REVIEW
from lib.prompts.workflow_obligation_review import OBLIGATION_REVIEW


COT_SELF_CHECK = spec("cot_self_check", "以正文证据逐项自检", SELF_CHECK)

COT_SELF_CHECK_REVIEW = spec("cot_self_check_review", "独立核验规则覆盖和实质证据", INDEPENDENT_CHECK)

COT_CLAIM_CHECK = spec("cot_claim_check", "逐个核对数值单位与结论前提", CLAIM_SELF_CHECK)

COT_CLAIM_REVIEW = spec("cot_claim_review", "独立复算并反驳过强主张", CLAIM_REVIEW)

COT_CLAIM_CHECK_V1_0 = COT_CLAIM_CHECK

COT_CLAIM_REVIEW_V1_0 = COT_CLAIM_REVIEW

COT_CLAIM_CHECK = _reasoning_spec_replace(COT_CLAIM_CHECK, version="1.1.0",
    template=spec("cot_claim_check", COT_CLAIM_CHECK.purpose, CLAIM_SELF_CHECK + CLAIM_SCHEMA).template)

COT_CLAIM_REVIEW = _reasoning_spec_replace(COT_CLAIM_REVIEW, version="1.1.0",
    template=spec("cot_claim_review", COT_CLAIM_REVIEW.purpose, CLAIM_REVIEW + CLAIM_SCHEMA).template)

COT_CLAIM_CHECK_V1_1 = COT_CLAIM_CHECK

COT_CLAIM_REVIEW_V1_1 = COT_CLAIM_REVIEW

COT_CLAIM_CHECK = _reasoning_spec_replace(COT_CLAIM_CHECK, version="1.2.0",
    template=spec("cot_claim_check", COT_CLAIM_CHECK.purpose, claim_protocol_v12(CLAIM_SELF_CHECK)).template)

COT_CLAIM_REVIEW = _reasoning_spec_replace(COT_CLAIM_REVIEW, version="1.2.0",
    template=spec("cot_claim_review", COT_CLAIM_REVIEW.purpose, claim_protocol_v12(CLAIM_REVIEW)).template)

COT_CHECK_AGENT = spec("cot_check_agent", "按疑点选择工具并逐项保存核查证据", REASONING_AGENT)

COT_CHECK_AGENT_V1_0 = COT_CHECK_AGENT

COT_CHECK_AGENT_V1_1 = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.1.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, REASONING_AGENT_RECOVERY_V1_1).template)

COT_CHECK_AGENT = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.2.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, REASONING_AGENT_RECOVERY).template)

COT_CHECK_AGENT_V1_2 = COT_CHECK_AGENT

COT_CHECK_AGENT = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.3.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, REASONING_ENVIRONMENT).template)

COT_CHECK_AGENT_V1_3 = COT_CHECK_AGENT

COT_CHECK_AGENT = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.4.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, SEMANTIC_REVIEW).template)

COT_CHECK_AGENT_V1_4 = COT_CHECK_AGENT

COT_CHECK_AGENT = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.5.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, EVIDENCE_REVIEW).template)

COT_CHECK_AGENT_V1_5 = COT_CHECK_AGENT

COT_CHECK_AGENT = _reasoning_spec_replace(COT_CHECK_AGENT, version="1.6.0",
    template=spec("cot_check_agent", COT_CHECK_AGENT.purpose, OBLIGATION_REVIEW).template)
