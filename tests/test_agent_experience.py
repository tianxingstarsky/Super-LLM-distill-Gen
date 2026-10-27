"""Full procedural experience is bounded, role-local, literal and reproducible."""
from copy import deepcopy
import hashlib

import pytest

from lib.domain.agent_experience import (
    AGENT_EXPERIENCE_VERSION, EXPERIENCE_KIND, EXPERIENCE_SCOPES, MAX_EXPERIENCE_BYTES,
    default_experience_text, experience_prompt_payload, experience_scope,
    snapshot_agent_experience, validate_agent_experience, validate_agent_experience_snapshot,
)
from lib.domain.workflow_quality import canonical


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Only deterministic domain functions are exercised; no model service."""
    yield


def test_builtin_experience_keeps_rich_conditional_examples_and_truthful_provenance():
    for scope in EXPERIENCE_SCOPES:
        text = default_experience_text(scope, version=3)
        assert 4096 < len(text.encode("utf-8")) < MAX_EXPERIENCE_BYTES
        assert "好例：" in text and "坏例：" in text
        assert "条件经验：" in text and ("失败回顾" in text or "失败反馈" in text)
        assert "Codex 编码助手" in text and "不是专家签署" in text
        assert "不是教师知识" in text and "不能逐字复制" in text
    assert "不规定训练答案或推理的标题、步骤、顺序" in default_experience_text("reasoning", version=3)
    assert "模糊输入用于练习澄清" in default_experience_text("question")
    assert "不设固定比例" in default_experience_text("director")
    assert "实际可见消息明确断言" in default_experience_text("answer")
    assert "评分应按照实际证据" in default_experience_text("review")
    assert "LaTeX" in default_experience_text("cpt")


def test_all_roles_defer_schema_and_unknown_domain_to_the_actual_stage_and_user():
    for scope in EXPERIENCE_SCOPES:
        text = default_experience_text(scope)
        assert "要求纯文本时交付纯文本，要求 JSON 时仅返回指定字段" in text
        assert "长期经验不新增 answer 字段" in text
        assert "资料主题、参考答案所属领域和经验例子不能替模糊用户确定" in text
        assert "普通开放概念说明可以使用可靠通用知识" in text
        assert "资料限定的闭域任务" in text and "必须核对对应证据" in text
    assert "原生答案阶段按该阶段的纯文本契约交付" in default_experience_text("answer")
    assert "纯文本阶段核对实际答案，不因为没有 JSON 包装而拒绝" in default_experience_text("review")


def test_every_registered_node_prompt_maps_to_the_processing_role():
    from lib.domain.workflow_node_prompts import NODE_PROMPT_IDS
    for prompt_ids in NODE_PROMPT_IDS.values():
        assert all(experience_scope(prompt) in EXPERIENCE_SCOPES for prompt in prompt_ids)
    assert experience_scope("workflow.qa_director") == "director"
    assert experience_scope("workflow.question_user") == "question"
    assert experience_scope("workflow.sft_answer") == "answer"
    assert experience_scope("workflow.cot_rationale") == "reasoning"
    assert experience_scope("workflow.cot_native_answer") == "answer"
    assert experience_scope("workflow.cot_prefix_check") == "review"
    assert experience_scope("workflow.cpt_clean") == "cpt"
    assert experience_scope("workflow.review_reference") == "review"
    assert experience_scope("workflow.learner_context_check") == "review"
    assert experience_scope("workflow.preference_design_check") == "review"
    for unknown in (None, 1, [], "workflow.unknown", "cot", ""):
        assert experience_scope(unknown) is None


def test_missing_configuration_resolves_all_scopes_without_shared_mutable_state():
    first = validate_agent_experience()
    second = validate_agent_experience(None)
    assert first == second == validate_agent_experience({})
    assert set(first) == set(EXPERIENCE_SCOPES)
    assert all(config["enabled"] is True for config in first.values())
    first["director"]["text"] = "changed locally"
    first["question"]["enabled"] = False
    assert second["director"]["text"] == default_experience_text("director")
    assert validate_agent_experience()["question"]["enabled"] is True


def test_partial_custom_edit_is_literal_and_cannot_change_another_role():
    text = '  Keep {question}, {{literal}}, ${name}, and "quotes".\r\n  第二行  \n'
    original = {"answer": {"text": text}, "review": {"enabled": False}}
    unchanged = deepcopy(original)
    resolved = validate_agent_experience(original)
    assert resolved["answer"] == {"enabled": True, "text": text}
    assert resolved["review"] == {"enabled": False, "text": default_experience_text("review")}
    assert resolved["question"]["text"] == default_experience_text("question")
    resolved["answer"]["text"] = "after validation"
    assert original == unchanged
    assert experience_prompt_payload("answer", {"text": text}) == {"enabled": True, "text": text}


def test_disabled_scope_can_retain_empty_incomplete_edit_but_enabled_scope_cannot():
    assert validate_agent_experience({"reasoning": {"enabled": False, "text": ""}})["reasoning"] == {
        "enabled": False, "text": ""}
    with pytest.raises(ValueError, match="invalid_agent_experience"):
        validate_agent_experience({"reasoning": {"enabled": True, "text": " "}})


@pytest.mark.parametrize("value", [
    True, [], "configuration", {"unknown": {}}, {"question": []}, {"question": {"quota": 20}},
    {"question": {"enabled": 1}}, {"question": {"enabled": "true"}}, {"answer": {"text": 123}},
    {"answer": {"text": "x\x00y"}}, {"answer": {"text": "x\ufffdy"}}, {"answer": {"text": "\ud800"}},
])
def test_invalid_configuration_is_rejected_before_snapshot_or_model_call(value):
    with pytest.raises(ValueError, match="invalid_agent_experience"):
        validate_agent_experience(value)
    with pytest.raises(ValueError, match="invalid_agent_experience"):
        snapshot_agent_experience(value)


@pytest.mark.parametrize("scope", ["unknown", "", None, 1, []])
def test_invalid_scope_cannot_restore_or_export_a_different_roles_text(scope):
    with pytest.raises(ValueError, match="invalid_agent_experience_scope"):
        default_experience_text(scope)
    with pytest.raises(ValueError, match="invalid_agent_experience_scope"):
        experience_prompt_payload(scope)


@pytest.mark.parametrize("version", [True, "1", 1.0, 0, 5, None])
def test_default_version_must_be_a_known_immutable_integer(version):
    with pytest.raises(ValueError, match="invalid_agent_experience_version"):
        default_experience_text("director", version=version)
    with pytest.raises(ValueError, match="invalid_agent_experience_version"):
        snapshot_agent_experience(version=version)


@pytest.mark.parametrize("text", ["a" * MAX_EXPERIENCE_BYTES, "中" * 10922 + "\n\n"], ids=["ascii-limit", "utf8-limit"])
def test_text_limit_counts_exact_utf8_bytes_instead_of_character_count(text):
    assert len(text.encode("utf-8")) == MAX_EXPERIENCE_BYTES
    assert validate_agent_experience({"answer": {"text": text}})["answer"]["text"] == text
    with pytest.raises(ValueError, match="invalid_agent_experience"):
        validate_agent_experience({"answer": {"text": text + "a"}})
    with pytest.raises(ValueError, match="invalid_agent_experience"):
        validate_agent_experience({"answer": {"text": "中" * 10923}})


def test_complete_snapshot_pins_version_raw_text_and_enabled_flags():
    pack = snapshot_agent_experience()
    assert pack["version"] == AGENT_EXPERIENCE_VERSION == 4
    assert pack["kind"] == EXPERIENCE_KIND
    assert set(pack["scopes"]) == set(EXPERIENCE_SCOPES)
    for scope, config in pack["scopes"].items():
        assert config["text"] == default_experience_text(scope)
        assert config["text_sha256"] == hashlib.sha256(config["text"].encode("utf-8")).hexdigest()
        assert config["origin"] == "builtin"
    assert pack["sha256"] == hashlib.sha256(canonical({key: value for key, value in pack.items()
                                                     if key != "sha256"}).encode("utf-8")).hexdigest()
    assert validate_agent_experience_snapshot(pack) == pack
    restored = validate_agent_experience_snapshot(pack)
    restored["scopes"]["question"]["text"] = "caller change"
    assert restored != pack == snapshot_agent_experience()


def test_published_version_one_default_hash_is_frozen():
    # Changes to published experience must add a new version, never move v1.
    assert snapshot_agent_experience(version=1)["sha256"] == \
        "ad620281d7f40cc04d66823fb0ae1897c99b33692ba28afc105d9bc4eb4d2197"


def test_version_two_is_frozen_and_keeps_version_one_replay_valid():
    prior = snapshot_agent_experience(version=1)
    current = snapshot_agent_experience(version=2)
    assert current["sha256"] == "bb92e1b0d4284a88921c7965f1ab82e020825610a46d85b9ccda831e0071e8ef"
    assert validate_agent_experience_snapshot(prior) == prior
    assert validate_agent_experience_snapshot(current) == current
    assert current["sha256"] != prior["sha256"]
    for scope in ("director", "answer", "reasoning", "review"):
        assert current["scopes"][scope]["text_sha256"] != prior["scopes"][scope]["text_sha256"]
    for scope in ("question", "cpt"):
        assert current["scopes"][scope]["text"] == prior["scopes"][scope]["text"]
    # Reusing a user's previous body is literal; defaults never rewrite it.
    reused = snapshot_agent_experience({scope: {name: config[name] for name in ("enabled", "text")}
                                       for scope, config in prior["scopes"].items()})
    assert reused["version"] == AGENT_EXPERIENCE_VERSION
    assert all(reused["scopes"][scope]["text"] == prior["scopes"][scope]["text"] for scope in EXPERIENCE_SCOPES)


def test_v2_conditions_do_not_force_unknown_inputs_into_diagnosis_or_examples_into_definitions():
    for scope in ("director", "answer", "reasoning", "review"):
        text = default_experience_text(scope, version=2)
        assert "条件经验：" in text and any(word in text for word in ("首次", "首轮", "没有上下文"))
        assert "技术" in text and "对象" in text and "可见" in text
        assert "必要条件" in text and "监督信号" in text
        assert "人工" in text and "程序" in text
        assert "不是教师知识" in text and "本次" in text and "核验" in text
    assert "不能断言它“只能说明某个操作失败”" in default_experience_text("answer")
    assert "对象还未知时，不进入后续诊断阶段" in default_experience_text("answer")
    assert "不能因首句短就惩罚有用长答" in default_experience_text("review")
    assert "把实例属性升级为方法的必要条件" in default_experience_text("reasoning", version=3)


def test_version_three_is_frozen_and_prior_versions_keep_literal_snapshots():
    hashes = {
        1: "ad620281d7f40cc04d66823fb0ae1897c99b33692ba28afc105d9bc4eb4d2197",
        2: "bb92e1b0d4284a88921c7965f1ab82e020825610a46d85b9ccda831e0071e8ef",
        3: "dc50cb269a9c78f124b2254e952487cbbf35a4bb689ada0c22d15d3ade421f00",
    }
    packs = {version: snapshot_agent_experience(version=version) for version in hashes}
    assert snapshot_agent_experience()["version"] == 4
    for version, expected_sha in hashes.items():
        assert packs[version]["sha256"] == expected_sha
        assert validate_agent_experience_snapshot(packs[version]) == packs[version]
        for scope in EXPERIENCE_SCOPES:
            assert len(packs[version]["scopes"][scope]["text"].encode("utf-8")) < MAX_EXPERIENCE_BYTES
    for scope in ("director", "answer", "reasoning", "review"):
        assert packs[3]["scopes"][scope]["text_sha256"] != packs[2]["scopes"][scope]["text_sha256"]
    for scope in ("question", "cpt"):
        assert packs[3]["scopes"][scope]["text"] == packs[2]["scopes"][scope]["text"]
    # Custom or reused v2 bodies remain literal under the new version marker.
    reused = snapshot_agent_experience({scope: {key: value[key] for key in ("enabled", "text")}
                                       for scope, value in packs[2]["scopes"].items()})
    assert reused["version"] == AGENT_EXPERIENCE_VERSION
    for scope in EXPERIENCE_SCOPES:
        assert reused["scopes"][scope]["text"] == packs[2]["scopes"][scope]["text"]


def test_version_four_is_compact_and_preserves_other_roles_and_old_bodies():
    prior = snapshot_agent_experience(version=3)
    current = snapshot_agent_experience(version=4)
    assert current == snapshot_agent_experience()
    assert validate_agent_experience_snapshot(current) == current
    text = current["scopes"]["reasoning"]["text"]
    assert len(text.encode("utf8")) < len(prior["scopes"]["reasoning"]["text"].encode("utf8"))
    for requirement in ("最少但足够", "支持与反证", "发现冲突或疏漏就修正判断", "修正带进最终答案",
                        "不编造负例、失败和反转", "没有固定步骤", "一次好恶不升级为普遍规则"):
        assert requirement in text
    for scope in set(EXPERIENCE_SCOPES) - {"reasoning"}:
        assert current["scopes"][scope] == prior["scopes"][scope]
    reused = snapshot_agent_experience({"reasoning": {"text": prior["scopes"]["reasoning"]["text"]}})
    assert reused["scopes"]["reasoning"]["text"] == prior["scopes"]["reasoning"]["text"]
    assert reused["scopes"]["reasoning"]["origin"] == "custom"


@pytest.mark.parametrize("scope", ["answer", "reasoning", "review"])
def test_v3_checks_visible_quantities_without_forcing_new_counts_or_token_substitution(scope):
    text = default_experience_text(scope, version=3)
    assert "CPT" in text and "三个拉丁字母" in text and "两个字母" in text
    assert "实际" in text and "token 数" in text and "字母" in text
    assert any(phrase in text for phrase in ("没有用途的数量", "没有用途的计数", "没有任务需要"))
    assert "全部" in text and "只有" in text and "必须" in text
    assert "用户问题" in text or "原输入" in text or "真实输入" in text
    assert "不是教师知识" in text and "不能把经验中的例句" in text


@pytest.mark.parametrize("scope", ["director", "answer", "reasoning", "review"])
def test_v3_multiturn_is_activity_driven_without_hidden_fault_requirements(scope):
    text = default_experience_text(scope, version=3)
    for activity in ("保养", "安全", "流程", "取舍", "真实结果", "解释", "改写"):
        assert activity in text
    assert ("信息差" in text or "关键未知项" in text) and "完整隐藏故障状态" in text and "必要条件" in text
    assert "资料" in text and "操作" in text and ("依赖" in text or "顺序" in text)
    assert "已知" in text or "已经提供" in text or "已经明确" in text
    assert "配额" in text or "行为清单" in text or "只说明可行设计方向" in text
    assert "好例：" in text and "坏例：" in text
    assert any(phrase in text for phrase in ("不是所有多轮", "普遍定义", "通用的多轮生成要求"))
    assert "短" in text and "长" in text


def test_snapshot_changes_for_literal_edits_and_disabled_scope_without_nondeterminism():
    left = {"answer": {"text": "User answer guidance."}, "review": {"enabled": False}}
    right = {"review": {"enabled": False}, "answer": {"text": "User answer guidance."}}
    pack = snapshot_agent_experience(left)
    assert pack == snapshot_agent_experience(right)
    assert pack["scopes"]["answer"]["origin"] == "custom"
    assert pack["scopes"]["review"]["enabled"] is False
    assert pack["sha256"] != snapshot_agent_experience()["sha256"]
    assert pack["sha256"] != snapshot_agent_experience({"answer": {"text": "User answer guidance. "},
                                                       "review": {"enabled": False}})["sha256"]


@pytest.mark.parametrize("change", ["text", "text_hash", "enabled", "origin", "version", "missing_scope", "kind", "sha", "extra"])
def test_changed_or_incomplete_snapshots_cannot_replay_as_original_experience(change):
    pack = snapshot_agent_experience()
    if change == "text":
        pack["scopes"]["director"]["text"] += "not pinned"
    elif change == "text_hash":
        pack["scopes"]["director"]["text_sha256"] = "0" * 64
    elif change == "enabled":
        pack["scopes"]["director"]["enabled"] = False
    elif change == "origin":
        pack["scopes"]["director"]["origin"] = "custom"
    elif change == "version":
        pack["version"] = True
    elif change == "missing_scope":
        pack["scopes"].pop("director")
    elif change == "kind":
        pack["kind"] = "teacher_evidence"
    elif change == "sha":
        pack["sha256"] = "0" * 64
    else:
        pack["source_facts"] = "untrusted body"
    with pytest.raises(ValueError, match="invalid_agent_experience_snapshot"):
        validate_agent_experience_snapshot(pack)
