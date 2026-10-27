"""User-owned editions preserve failed traces, evidence and append-only lineage."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository


EPISODES = [
    {"id": "failed", "outcome": "failed", "messages": [
        {"role": "user", "content": "希望页面紧凑，重点容易找。"},
        {"role": "assistant", "content": "把所有内容藏进折叠区。", "reasoning_content": "越少内容越紧凑。"},
        {"role": "user", "content": "关键入口也找不到了。"}]},
    {"id": "repaired", "outcome": "unknown", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "read_preview", "arguments": "{}"}}], "content": ""},
        {"role": "tool", "tool_call_id": "call-1", "is_error": True, "content": "预览服务尚未启动"},
        {"role": "assistant", "content": "启动预览服务后再检查入口。"}]}]


def proposal():
    return {"summary": "保留紧凑布局中的关键入口，视觉判断以实际预览为准。", "rules": [
        {"principle": "保留任务所需的主要操作，减少重复说明。", "when": "用户要求紧凑且入口易找时。",
         "avoid": "把隐藏关键功能误当作精简界面。", "evidence": [
             {"episode_id": "failed", "quote": "关键入口也找不到了。"}]},
        {"principle": "预览可用后再作视觉判断。", "when": "需要检查真实页面而预览尚不可用时。",
         "avoid": "把工具错误当成已完成的页面验收。", "evidence": [
             {"episode_id": "repaired", "quote": "预览服务尚未启动"}]}]}


class Port:
    def __init__(self, root):
        self.repo = ExperienceEvolutionRepository(root)
        self.calls = []
        self.response = proposal()

    def propose(self, payload):
        self.calls.append(deepcopy(payload))
        return deepcopy(self.response)

    def save(self, snapshot): return self.repo.save(snapshot)
    def list_versions(self, scope): return self.repo.list_versions(scope)
    def get_version(self, identifier): return self.repo.get_version(identifier)


def test_full_lineage_raw_failures_and_personal_versions_survive_restart(tmp_path):
    port = Port(tmp_path / "user-a")
    app = EvolveExperience(port)
    initial = app.save_version("reasoning", "简洁、全面，关注支持与反例。", label="初始版", status="initial")
    original = deepcopy(EPISODES)
    candidate = app.propose("reasoning", initial["text"], EPISODES, parent_id=initial["id"],
        instruction="重视审美和可发现性。", source_receipt={"import_id": "dsh-import", "source_sha256": "abc"})
    assert len(port.calls) == 1 and port.calls[0]["episodes"] == EPISODES
    assert candidate["episodes"] == EPISODES == original
    assert candidate["quality"] == "model_proposed_not_verified" and candidate["status"] == "candidate"
    assert candidate["source_receipt"]["import_id"] == "dsh-import"
    assert candidate["base_text"] == initial["text"] and "隐藏关键功能" in candidate["text"]
    assert candidate["text"].startswith(candidate["summary"])
    assert "预览服务尚未启动" not in candidate["text"]  # raw failures are lineage, not next-run instructions
    assert "outcome" not in candidate["text"] and "grade" not in candidate
    personal = app.save_version("reasoning", candidate["text"] + "\n使用真实页面核对。",
        parent_id=candidate["id"], label="我的紧凑风格")
    reloaded = EvolveExperience(Port(tmp_path / "user-a"))
    assert reloaded.get_version(candidate["id"]) == candidate
    assert reloaded.get_version(initial["id"]) == initial
    assert reloaded.get_version(personal["id"])["base_text"] == candidate["text"]
    assert [item["id"] for item in reloaded.list_versions("reasoning")] == [personal["id"], candidate["id"], initial["id"]]
    assert reloaded.list_versions("answer") == []
    other = EvolveExperience(Port(tmp_path / "user-b"))
    assert other.list_versions("reasoning") == []
    with pytest.raises(ValueError, match="not_found"):
        other.get_version(candidate["id"])


def test_unknown_or_cross_scope_parent_fails_before_model_call(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    with pytest.raises(ValueError, match="not_found"):
        app.propose("reasoning", "原经验", EPISODES, parent_id="a" * 32)
    initial = app.save_version("answer", "回答经验")
    with pytest.raises(ValueError, match="parent_scope_mismatch"):
        app.propose("reasoning", "原经验", EPISODES, parent_id=initial["id"])
    assert port.calls == []


def test_next_proposal_observes_candidate_and_user_edit_without_repeating_raw_attempts(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    first = app.propose("reasoning", "保持简洁，保留反例。", EPISODES, instruction="重视入口发现。")
    edited = app.save_version("reasoning", first["text"] + "\n不采用固定清单格式。", parent_id=first["id"])
    next_edition = app.propose("reasoning", edited["text"], EPISODES, parent_id=edited["id"], instruction="再整理一次。")
    payload = port.calls[-1]
    history = payload["evolution_history"]
    assert [edition["id"] for edition in history["editions"]] == [first["id"], edited["id"]]
    assert history["editions"][0]["instruction"] == "重视入口发现。"
    assert "不采用固定清单格式。" in history["editions"][1]["text"]
    assert history["editions"][0]["quality"] == "model_proposed_not_verified"
    assert history["editions"][1]["quality"] == "user_saved_not_verified"
    assert all("episodes" not in edition and "model_request_receipt" not in edition for edition in history["editions"])
    assert "越少内容越紧凑。" not in json.dumps(history, ensure_ascii=False)
    assert history["omitted_ids"] == [] and "not_verified_quality_success" in history["meaning"]
    assert next_edition["evolution_history"] == history
    assert payload["current_text"] == edited["text"] and payload["instruction"] == "再整理一次。"
    assert app.get_version(first["id"])["episodes"] == EPISODES


def test_history_budget_omits_whole_edition_and_keeps_its_id(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    long_personal = app.save_version("reasoning", "a" * 17000)
    app.propose("reasoning", long_personal["text"], EPISODES, parent_id=long_personal["id"])
    history = port.calls[-1]["evolution_history"]
    assert history["editions"] == [] and history["omitted_ids"] == [long_personal["id"]]
    assert len(port.calls[-1]["current_text"]) == 17000


def test_unbound_evidence_does_not_create_a_saved_candidate(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    port.response["rules"][0]["evidence"][0]["quote"] = "所有界面都必须折叠。"
    with pytest.raises(ValueError, match="evidence_mismatch"):
        app.propose("reasoning", "原经验", EPISODES)
    assert len(port.calls) == 1 and app.list_versions("reasoning") == []


def test_no_new_lesson_can_preserve_current_style_without_inventing_rules(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    current = "简洁、全面；保留真正影响结论的依据与反例。"
    seed = app.save_version("reasoning", current, status="initial")
    port.response = {"summary": current, "rules": []}
    candidate = app.propose("reasoning", current, EPISODES, parent_id=seed["id"])
    assert candidate["rules"] == [] and candidate["text"] == current
    assert candidate["quality"] == "model_proposed_not_verified"
    assert app.get_version(seed["id"])["text"] == current
    assert app.get_version(candidate["id"])["episodes"] == EPISODES


def test_episode_limit_never_silently_slices_raw_history(tmp_path):
    port = Port(tmp_path)
    app = EvolveExperience(port)
    huge = deepcopy(EPISODES)
    huge[0]["messages"][0]["content"] = "长" * 60000
    with pytest.raises(ValueError, match="episodes_too_large"):
        app.propose("reasoning", "原经验", huge)
    assert len(huge[0]["messages"][0]["content"]) == 60000 and not port.calls


def test_store_detects_tampering_and_refuses_edition_overwrite(tmp_path, monkeypatch):
    import lib.contexts.workflows.infrastructure.experience_evolution as module
    port = Port(tmp_path)
    app = EvolveExperience(port)
    version = app.save_version("reasoning", "原版")
    class SameId:
        hex = version["id"]
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SameId())
    with pytest.raises(ValueError, match="id_conflict"):
        app.save_version("reasoning", "不允许覆盖旧版")
    assert app.get_version(version["id"]) == version
    path = tmp_path / (version["id"] + ".json")
    record = json.loads(path.read_text("utf8"))
    record["text"] = "被篡改"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="corrupt_version"):
        app.get_version(version["id"])
    with pytest.raises(ValueError, match="invalid_id"):
        app.get_version("../outside")
