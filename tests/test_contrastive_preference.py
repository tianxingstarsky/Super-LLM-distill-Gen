"""Explicit opposite/short negatives, blind grades and shared format review."""
from copy import deepcopy
from unittest.mock import Mock
import pytest

from lib.contexts.workflows.contracts import validate_preference_design, preference_score_issue, rlaif_feedback_issue
from lib.contexts.workflows.application.generate_preference import GeneratePreferencePair
from lib.contexts.review.application.assess_preference_pair import AssessPreferencePair
from lib.contexts.review.application.review_preference_view import ReviewPreferenceView
from lib.contexts.review.application.review_candidate import ReviewCandidate
from lib.contexts.review.domain.preference_candidates import (
    response_projection, contrastive_reasoning_issue, validate_contrast_assessment, rlaif_view)
from lib.contexts.review.infrastructure.automatic_runtime import WorkflowReviewRepair
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.prompts import get, render


def grade(score=5):
    return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
        "correctness": score, "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
        "reason": "independent content judgment " + str(score)}


def pair():
    return {"id": "task1", "source_id": "source1", "status": "eligible", "evidence_level": "synthetic",
        "prompt": [{"role": "user", "content": "重复记录要先判断还是先写出？"}],
        "chosen": [{"role": "assistant", "content": "先判断，再写出。",
                    "reasoning_content": "先写后判重会使重复行已经进入输出，跳过后续操作不能撤销它。先查 seen，命中就跳过，未命中再登记并写出。空文件、字段内换行与带前导零的 id 都须保留原 CSV 语义，不能用字符串拆行。"}],
        "rejected": [{"role": "assistant", "content": "先写出，再判断。", "reasoning_content": "写出后再跳过就行。"}],
        "revision_context": {"design_requirements": {"instruction": "Make the rejected response wrong"}}}


def config():
    return {"preference_design_version": 1, "preference_design": validate_preference_design({"mode": "opposite_short"}),
            "review_repair": {"mode": "auto", "max_rounds": 1, "score_threshold": .8}}


class BlindPort:
    def __init__(self, scores=(5, 1), contrast=True, pending=False):
        self.calls, self.scores, self.contrast, self.pending = [], iter(scores), contrast, pending

    def assess_response(self, projected, evidence, **options):
        self.calls.append((deepcopy(projected), options))
        return {"verdict": grade(next(self.scores)), "condition_pending": self.pending}

    def assess_contrast(self, row, evidence, **options):
        return {"same_task": True, "opposite": self.contrast, "off_topic": False, "chosen_claim": "先判断，再写出。",
                "rejected_claim": "先写出，再判断。", "reason": "判断与写出顺序相反。"}


def test_requested_negative_is_opposite_and_short_but_not_deleted():
    original = pair(); port = BlindPort()
    row, verdict, stop = AssessPreferencePair(port, config()).execute(original, {}, instruction="Tell the negative to fail")
    assert verdict["keep"] and stop is None
    assert row["chosen"] == original["chosen"] and row["rejected"] == original["rejected"]
    assert not preference_score_issue(row)
    assert row["preference_assessment"]["contrast"]["opposite"]
    for projected, options in port.calls:
        assert not {"chosen", "rejected", "preference", "revision_context"} & set(projected)
        assert "negative" not in options["instruction"]
        assert "Tell" not in options["instruction"]
        assert projected["messages"][-1]["reasoning_content"]
    assert original == pair()


def test_quality_grades_cannot_be_swapped_to_rehabilitate_a_wrong_positive():
    port=BlindPort(scores=(1, 5))
    row, verdict, _ = AssessPreferencePair(port, config()).execute(pair(), {})
    assert not verdict["keep"]
    assert row["chosen"] == pair()["chosen"]
    assert 'preference' not in row and 'rlaif' not in row
    assert row['preference_assessment']['responses'][0]['verdict']['correctness']==1
    assert len(port.calls)==1


def test_changed_response_cannot_inherit_an_old_learner_context_verdict():
    row=pair();row['learner_context_check']={'keep':True,'reason':'stale earlier body'}
    port=BlindPort()
    assessed=port.assess_response
    def score(projected,evidence,**options):
        entry=assessed(projected,evidence,**options)
        entry['learner_context_check']={'keep':False,'reason':'actual new response check'}
        return entry
    port.assess_response=score
    checked,_,_=AssessPreferencePair(port,config()).execute(row,{})
    assert checked['learner_context_check']['reason']=='actual new response check'


def test_shorter_does_not_make_same_direction_answers_a_valid_contrast():
    row, verdict, _ = AssessPreferencePair(BlindPort(contrast=False), config()).execute(pair(), {})
    assert not verdict["keep"]
    assert row["preference_assessment"]["design_issue"] == "preference_opposite_answer_required"


@pytest.mark.parametrize("field,value,expected", [
    ("chosen", "很短。", "preference_negative_reasoning_not_shorter"),
    ("rejected", "", "preference_reasoning_required"),
    ("rejected", "推" * 161, "preference_negative_reasoning_too_long"),
])
def test_nonempty_and_configured_length_contrast_are_enforced(field, value, expected):
    row = pair(); row[field][0]["reasoning_content"] = value
    assert contrastive_reasoning_issue(row, None) == expected


@pytest.mark.parametrize("value", [{"mode": "independent"}, {"negative_reasoning_max_chars": True},
    {"negative_reasoning_max_chars": 0}, {"negative_reasoning_max_ratio": float("nan")},
    {"negative_reasoning_max_ratio": .9}, {"unknown": 1}])
def test_bad_design_configuration_is_rejected(value):
    with pytest.raises(ValueError, match="invalid_"):
        validate_preference_design(value)


def test_unquoted_or_invented_core_claim_cannot_pass_relation_check():
    value = BlindPort().assess_contrast(pair(), {})
    value["chosen_claim"] = "A claim the answer never made"
    with pytest.raises(ValueError, match="quote_not_in_answer"):
        validate_contrast_assessment(value, pair())


def test_full_reasoning_is_reviewed_even_when_sft_export_hides_it():
    runtime = WorkflowReviewRepair()
    runtime.recipe = {**config(), "sft_output_style": "drop"}
    projected = response_projection(pair(), pair()["chosen"])
    assert runtime._review_payload("sft", projected)["messages"][-1]["reasoning_content"] == pair()["chosen"][0]["reasoning_content"]
    assert "_complete_preference_response" not in runtime._review_payload("sft", projected)


def test_negative_generation_uses_correct_reference_and_explicit_design():
    port = Mock()
    port.request.return_value = {"answer": "先写出，再判断。", "reasoning": "写出后再跳过就行。"}
    sample = {**pair(), "messages": [*pair()["prompt"], *pair()["chosen"]], "source_context": "CSV constraints"}
    generated = GeneratePreferencePair(port, config()).execute(sample)[0]
    args, kwargs = port.request.call_args
    assert args[3]["reference_response"] == sample["messages"][-1]
    assert args[3]["contrastive_design"]["mode"] == "opposite_short"
    assert kwargs["allow_reasoning_fallback"] is False
    assert generated["chosen"] == pair()["chosen"] and generated["rejected"][0]["reasoning_content"]
    assert "故意制造坏答案" not in str(kwargs)


class ReviewPort:
    run_id = "run"
    authenticated_revision = False

    def __init__(self):
        self.keys = []

    def evidence(self, row): return {"source_kind": "synthetic"}
    def payload(self, target, row): return {key: row[key] for key in ("prompt", "chosen", "rejected")}
    def score(self, target, row, evidence, key, instruction):
        self.keys.append(key)
        checked, verdict, _ = AssessPreferencePair(BlindPort(), config()).execute(row, evidence)
        row.update({key: checked[key] for key in ("preference", "preference_assessment")})
        return verdict


def test_dpo_orpo_rlaif_use_same_pair_and_request_identity():
    port = ReviewPort(); recipe = config()
    reviewer = ReviewPreferenceView(ReviewCandidate(port, recipe), recipe)
    outputs = [reviewer.execute({"target": target, "record": pair()})[0] for target in ("dpo", "orpo", "rlaif")]
    assert len({str(key) for key in port.keys}) == 1
    assert all(row["review_repair"]["status"] == "accepted" for row in outputs)
    assert [row["_review_target"] for row in outputs] == ["dpo", "orpo", "rlaif"]
    assert all(row["chosen"] == outputs[0]["chosen"] and row["rejected"] == outputs[0]["rejected"] for row in outputs)
    assert not rlaif_feedback_issue(outputs[-1])


def test_changed_question_or_repair_instruction_cannot_reuse_the_same_pair_scope():
    port = ReviewPort(); reviewer = ReviewCandidate(port, config())
    reviewer.execute({"target": "dpo", "record": pair()})
    reviewer.execute({"target": "dpo", "record": pair(), "instruction": "Preserve a different requested condition"})
    assert port.keys[0] != port.keys[1]


def test_legacy_snapshots_stay_literal_and_new_templates_remain_editable():
    old = snapshot_node_prompts(None, recipe_version=21)
    new = snapshot_node_prompts(None, recipe_version=21, preference_design_version=1)
    assert old["preference"]["workflow.alternative"] == render(get("workflow.alternative", "1.1.0"))
    assert new["preference"]["workflow.alternative"] == render(get("workflow.alternative", "1.3.0"))
    assert new["preference"]["workflow.preference_directed_check"] == render(get("workflow.preference_directed_check", "2.1.0"))
    custom = snapshot_node_prompts({"preference": {"workflow.alternative": "my literal {rule}"}}, recipe_version=21,
                                  preference_design_version=1)
    assert custom["preference"]["workflow.alternative"] == "my literal {rule}"


def test_default_off_topic_is_distinct_from_an_opposite_answer():
    from lib.contexts.review.domain.preference_candidates import contrastive_relationship_issue
    assert validate_preference_design()["mode"] == "off_topic_short"
    row = pair()
    row["rejected"][0].update(content="薄荷盆栽要保持散射光。", reasoning_content="这样能减轻叶片晒伤。")
    assessment = {"same_task": False, "opposite": False, "off_topic": True,
        "chosen_claim": "先判断，再写出。", "rejected_claim": "薄荷盆栽要保持散射光。", "reason": "用户问 CSV，回答却讲园艺。"}
    checked = validate_contrast_assessment(assessment, row)
    assert contrastive_relationship_issue(checked, None) is None
    assert contrastive_relationship_issue(checked, {"mode": "opposite_short"}) == "preference_opposite_answer_required"
    opposite = BlindPort().assess_contrast(pair(), {})
    assert contrastive_relationship_issue(opposite, None) == "preference_off_topic_answer_required"


def test_off_topic_pair_passes_requested_design_without_changing_the_user_question():
    row = pair(); row["rejected"][0].update(content="薄荷盆栽要保持散射光。", reasoning_content="这样能减轻叶片晒伤。")
    port = BlindPort()
    port.assess_contrast = Mock(return_value={"same_task": False, "opposite": False, "off_topic": True,
        "chosen_claim": "先判断，再写出。", "rejected_claim": "薄荷盆栽要保持散射光。", "reason": "园艺答复不处理 CSV 任务。"})
    recipe = {**config(), "preference_design": validate_preference_design()}
    checked, verdict, _ = AssessPreferencePair(port, recipe).execute(row, {})
    assert verdict["keep"] and checked["prompt"] == pair()["prompt"]
    assert checked["preference"]["minimum_gap"] == 2
    assert checked["rejected"] == row["rejected"]


@pytest.mark.parametrize("flags", [dict(same_task=True, opposite=False, off_topic=True),
    dict(same_task=False, opposite=True, off_topic=False)])
def test_inconsistent_relation_flags_are_invalid(flags):
    value = {**BlindPort().assess_contrast(pair(), {}), **flags}
    with pytest.raises(ValueError, match="inconsistent_preference_contrast_assessment"):
        validate_contrast_assessment(value, pair())


def test_meta_explanation_is_not_accepted_as_a_natural_short_rationale():
    from lib.contexts.review.domain.preference_candidates import contrastive_relationship_issue
    row=pair()
    row["rejected"][0].update(content="阳台番茄要保证光照。", reasoning_content="未涉及 CSV 合并或脚本。")
    value={"same_task":False,"opposite":False,"off_topic":True,"rationale_supports_answer":False,
        "chosen_claim":"先判断，再写出。","rejected_claim":"阳台番茄要保证光照。",
        "rationale_quote":"未涉及 CSV 合并或脚本。","reason":"推理是制作旁白，没有解释番茄的养护。"}
    checked=validate_contrast_assessment(value,row,version=2)
    assert contrastive_relationship_issue(checked,None)=="preference_negative_rationale_meta"
    del value['rationale_quote']
    with pytest.raises(ValueError,match='invalid_preference_contrast_assessment'):
        validate_contrast_assessment(value,row,version=2)


def test_v2_design_requires_real_rationale_quotes_without_reinterpreting_v1_audits():
    row=pair(); value=BlindPort().assess_contrast(row,{})
    assert validate_contrast_assessment(value,row,version=1)==value
    with pytest.raises(ValueError,match='invalid_preference_contrast_assessment'):
        validate_contrast_assessment(value,row,version=2)
    value.update(rationale_supports_answer=True,rationale_quote='an invented phrase')
    with pytest.raises(ValueError,match='quote_not_in_rationale'):
        validate_contrast_assessment(value,row,version=2)


def test_each_new_protocol_template_is_frozen_independently():
    v1=snapshot_node_prompts(None,recipe_version=21,preference_design_version=1)
    v2=snapshot_node_prompts(None,recipe_version=21,preference_design_version=2)
    assert v1['preference']['workflow.alternative']==render(get('workflow.alternative','1.3.0'))
    assert v2['preference']['workflow.alternative']==render(get('workflow.alternative','1.4.0'))
    assert v2['preference']['workflow.preference_directed_check']==render(get('workflow.preference_directed_check','2.2.0'))


def test_new_design_prompt_keeps_positive_qa_adherence_as_a_distinct_contract():
    from lib.domain.workflow_node_prompts import validate_node_prompt_snapshot
    snapshot=snapshot_node_prompts(None,recipe_version=21,preference_design_version=2,preference_design_prompt_version=1)
    assert snapshot['preference']['workflow.preference_directed_check']==render(get('workflow.preference_directed_check','1.1.0'))
    assert snapshot['preference']['workflow.preference_design_check']==render(get('workflow.preference_design_check','1.0.0'))
    validate_node_prompt_snapshot(snapshot,'system',recipe_version=21,preference_design_prompt_version=1)


def test_v2_review_rejects_a_short_meta_rationale_and_keeps_its_field_for_repair():
    row=pair(); row['rejected'][0].update(content='阳台番茄要保证光照。',reasoning_content='未涉及 CSV 合并或脚本。')
    port=BlindPort()
    port.assess_contrast=Mock(return_value={"same_task":False,"opposite":False,"off_topic":True,
        "rationale_supports_answer":False,"chosen_claim":"先判断，再写出。","rejected_claim":"阳台番茄要保证光照。",
        "rationale_quote":"未涉及 CSV 合并或脚本。","reason":"这是制作旁白。"})
    recipe={**config(),'preference_design_version':2,'preference_design':validate_preference_design()}
    checked,verdict,stop=AssessPreferencePair(port,recipe).execute(row,{})
    assert not verdict['keep'] and stop is None
    assert checked['preference_assessment']['design_issue']=='preference_negative_rationale_meta'
    assert checked['rejected'][0]['reasoning_content']==row['rejected'][0]['reasoning_content']


def test_repaired_native_response_is_archived_and_never_mislabeled_as_original_native_thinking():
    from lib.contexts.review.domain.preference_candidates import preserve_preference_response_history
    original=pair();original['preference_response_origins']={'chosen':'provider_reasoning','rejected':'prompt_contrastive_generation'}
    updated=deepcopy(original);updated['chosen'][0]['reasoning_content']+='补充了实际边界核对。'
    preserve_preference_response_history(original,updated)
    assert updated['preference_response_origins']['chosen']=='prompt_revision'
    assert updated['preference_response_origins']['rejected']=='prompt_contrastive_generation'
    entry,=updated['preference_response_history']
    assert entry['side']=='chosen' and entry['response']==original['chosen']
    assert entry['reasoning_origin']=='provider_reasoning'
    assert original['preference_response_origins']['chosen']=='provider_reasoning'
