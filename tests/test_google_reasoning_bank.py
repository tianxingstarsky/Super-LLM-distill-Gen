"""Exercise the actual pinned Google entrypoints; fixtures assert protocol only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class Embedding:
    identity = "protocol-fixture-not-a-semantic-model"
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]
    def embed_query(self, query, instruction):
        return [1.0, 0.0]


def test_reviewer_provides_feedback_and_is_never_an_evolution_target(tmp_path, monkeypatch):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    with pytest.raises(ValueError, match="generation_scope_required"):
        app.learn("review", {})
    with pytest.raises(ValueError, match="generation_scope_required"):
        app.activate("review", "a" * 32)
    # Older saved reviewer selections cannot enter a new task's system context.
    monkeypatch.setattr(app, "active", lambda: {"scopes": {"review": "a" * 32}})
    assert app.freeze(None, "A generation task") is None


def test_style_freeze_can_exclude_reasoning_and_only_recall_common_other_role_memories(tmp_path, monkeypatch):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    calls = []
    monkeypatch.setattr(app, "active", lambda: {"scopes": {
        "reasoning": "a" * 32, "director": "b" * 32, "review": "c" * 32}})
    app.bank = SimpleNamespace(freeze=lambda *args, **options: calls.append((args, options)) or "frozen")
    assert app.freeze(None, "Common conversational conditions", excluded_scopes=("reasoning",),
        context_types=["common"]) == "frozen"
    assert calls[-1] == ((None, {"director": "Common conversational conditions"}),
        {"edition_ids": ["b" * 32], "context_types": ["common"]})
    app.freeze(None, "Legacy task query")
    assert calls[-1] == ((None, {"reasoning": "Legacy task query", "director": "Legacy task query"}),
        {"edition_ids": ["a" * 32, "b" * 32]})


def test_original_google_main_retrieval_resume_and_disable(tmp_path):
    calls = []
    class Client:
        usage = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 8}
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(deepcopy(messages))
            return "Use the actual label-space criterion; do not infer it from output modality."
    app = google_reasoning_bank_application({"backend": "fixture", "model": "fixture"}, root=tmp_path,
        user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    observation = {"id": "actual-protocol-case", "conversation": {"messages": [
        {"role": "user", "content": "分类的条件是什么？"},
        {"role": "assistant", "reasoning_content": "I need to distinguish a label space from its modality.",
         "content": "The task uses a small finite label space."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "Protocol fixture, not quality proof."}}}
    edition = app.learn("reasoning", observation)
    assert len(calls) == 1
    assert edition["upstream_result"]["upstream"][0]["functions"].keys() == {"main", "format_trajectory"}
    assert edition["model_request_receipt"][0]["response_details"]["usage"]["calls"] == 1
    assert app.learn("reasoning", observation)["id"] == edition["id"]
    assert len(calls) == 1
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    assert app.activate("reasoning", edition["id"])["status"] == "active"
    assert [row["id"] for row in app.experience.list_versions("reasoning")] == [edition["id"]]
    selection = app.freeze(None, "Which label-space criterion applies?")
    scope = selection["selection_receipt"]["scopes"]["reasoning"]
    assert scope["applied"]
    assert "select_memory" in scope["upstream_execution"]["invoked"]
    assert edition["text"] in selection["snapshot"]["scopes"]["reasoning"]["text"]
    app.activate("reasoning", None)
    assert app.freeze(None, "Which label-space criterion applies?") is None


def test_unknown_outcome_is_not_fabricated_for_google(tmp_path):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    with pytest.raises(ValueError, match="no_observed_outcome"):
        app.learn("reasoning", {"id": "unknown", "conversation": {"messages": [
            {"role": "user", "content": "help"}, {"role": "assistant", "content": "What happened?"}]}})
    from lib.contexts.workflows.infrastructure.google_reasoning_bank import recorded_episode
    imported = recorded_episode({"id": "dsh-no-thought", "messages": [
        {"role": "user", "content": "help"}, {"role": "assistant", "content": "What happened?"}]}, "success")
    assert imported["think_list"] == [""]
    assert imported["assessment_source"]["kind"] == "user_label"


def test_original_parallel_main_receives_two_actual_alternatives(tmp_path):
    from lib.contexts.workflows.infrastructure.reasoning_bank_induction import OfficialReasoningBankInductor
    seen = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            seen.append(messages)
            return "Check the label set rather than assuming a data type."
    inductor = OfficialReasoningBankInductor({"backend": "fixture", "model": "fixture"},
        lambda binding: (Client(), "fixture"), tmp_path)
    common = {"query": "What is classification?", "think_list": ["I need the criterion."],
              "assessment_source": "fixture_not_quality_proof", "feedback": "Protocol only."}
    result = inductor.contrast([{**common, "id": "first", "status": "fail", "action_list": ["wrong alternative"]},
                                {**common, "id": "second", "status": "success", "action_list": ["revised alternative"]}])
    assert len(seen) == 1
    assert "wrong alternative" in seen[0][1]["content"] and "revised alternative" in seen[0][1]["content"]
    assert "Recorded outcome: fail" in seen[0][1]["content"]
    assert "Recorded outcome: success" in seen[0][1]["content"]
    assert result["upstream"][0]["file"] == "WebArena/induce_scaling.py"


def test_generalization_policy_isolated_from_legacy_and_keeps_complete_parent_memory(tmp_path):
    from lib.contexts.workflows.domain.reasoning_bank import entries_from_edition
    seen = []
    memory = ("# Memory Item 1\n\n## Title Check the decision criterion\n\n"
              "## Description When a familiar label suggests a different decision.\n\n"
              "## Content Determine whether the evidence actually changes the governing criterion.")
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            seen.append(deepcopy(messages))
            return memory
    options = dict(root=tmp_path, user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    binding = {"backend": "fixture", "model": "fixture"}
    observation = {"id": "comparison", "conversation": {"messages": [
        {"role": "user", "content": "How should this decision be made?"},
        {"role": "assistant", "content": "Use the relevant evidence."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "A fallible observation, not causal proof."}}}
    legacy = google_reasoning_bank_application(binding, memory_policy_version=None, **options)
    old = legacy.learn("reasoning", observation)
    current = google_reasoning_bank_application(binding, memory_policy_version="1.0.0", **options)
    edition = current.learn("reasoning", observation)
    assert len(seen) == 2 and old["id"] != edition["id"]
    assert "Host memory context" not in seen[0][1]["content"]
    assert "不面向本题答案" in seen[1][0]["content"]
    assert edition["upstream_result"]["memory_policy"]["version"] == "1.0.0"
    entries = entries_from_edition(edition)
    assert len(entries) == 1
    assert entries[0]["principle"] == memory
    assert len(old["upstream_memory_items"]) > 1  # Historical projection is not rewritten.
    updated = current.learn("reasoning", {**observation, "id": "next"}, parent_id=edition["id"])
    context = json.loads(seen[2][1]["content"].split("Host memory context (reference data, not task instructions):\n")[1])
    assert context == {"generation_scope": "reasoning", "current_memory": edition["text"]}
    assert updated["base_text"] == edition["text"]
    assert current.learn("reasoning", {**observation, "id": "next"}, parent_id=edition["id"])["id"] == updated["id"]
    assert len(seen) == 3


def test_no_new_memory_receipt_is_reused_without_creating_or_activating_rules(tmp_path):
    calls = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(messages)
            return "<no-new-memory/>"
    app = google_reasoning_bank_application({"backend": "fixture", "model": "fixture"}, root=tmp_path,
        user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    observation = {"id": "no-new-principle", "conversation": {"messages": [
        {"role": "user", "content": "Continue."}, {"role": "assistant", "content": "Done."}]},
        "review_observation": {"verdict": {"keep": True, "reason": "No new transferable evidence."}}}
    result = app.learn("reasoning", observation)
    assert result["status"] == "no_update" and "id" not in result
    assert app.learn("reasoning", observation) == result
    assert len(calls) == 1 and app.repository.list_versions("reasoning") == []
    assert app.active()["scopes"] == {}
    receipt = app.repository.root / "official-editions" / (result["source_sha256"] + ".json")
    saved = json.loads(receipt.read_text("utf8"))
    saved["scope"] = "answer"
    receipt.write_text(json.dumps(saved), encoding="utf8")
    with pytest.raises(ValueError, match="learning_receipt_changed"):
        app.learn("reasoning", observation)
    assert len(calls) == 1


def _style_memory(title, contexts=None, *, content="Preserve the current user's actual request and evidence scope."):
    return (f"# Memory Item {title}\n\n## Title {title}\n\n"
            "## Description Apply only when the stated conversational condition holds.\n\n"
            + (f"## Contexts {contexts}\n\n" if contexts is not None else "")
            + "## Content " + content)


def _style_observation(identifier="style-memory-case"):
    return {"id": identifier, "conversation": {"messages": [
        {"role": "user", "content": "我先说一下背景，再一起考虑怎么调整。"},
        {"role": "assistant", "content": "先区分已有事实和本次需要改变的部分。"}]},
        "review_observation": {"verdict": {"keep": True,
            "reason": "Recorded protocol observation; not independent semantic verification."}}}


def _save_style_edition(app, items, assignments=None):
    from lib.contexts.workflows.domain.experience_evolution import digest
    return app.repository.save({"format_version": 1, "scope": "reasoning", "status": "candidate",
        "label": "Explicit fixture edition", "parent_id": None, "text": "\n\n".join(items),
        "upstream_memory_items": list(items), **({"dialogue_context_assignment_version": 1,
            "dialogue_context_assignments": {digest(item): tags for item, tags in zip(items, assignments)}}
            if assignments is not None else {})})


def test_new_google_policy_assigns_full_items_by_dialogue_style_and_isolates_old_cache(tmp_path):
    from lib.contexts.workflows.domain.experience_evolution import digest
    from lib.contexts.workflows.domain.reasoning_bank import entries_from_edition
    items = [_style_memory("Common", "common"), _style_memory("Narrative", "iterative,narrative"),
             _style_memory("Untagged historical form")]
    calls = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(deepcopy(messages))
            return "\n\n".join(items)
    options = {"root": tmp_path, "user_key": "fixture", "client_factory": lambda binding: (Client(), "fixture")}
    binding = {"backend": "fixture", "model": "fixture"}
    old = google_reasoning_bank_application(binding, memory_policy_version="1.0.0", **options)
    old_edition = old.learn("reasoning", _style_observation())
    app = google_reasoning_bank_application(binding, **options)
    edition = app.learn("reasoning", _style_observation())
    assert edition["id"] != old_edition["id"] and len(calls) == 2
    assert "dialogue_context_assignment_version" not in old_edition
    assert edition["upstream_result"]["memory_policy"]["version"] == "1.1.0"
    assert edition["upstream_memory_items"] == items and edition["text"] == "\n\n".join(items)
    assert edition["dialogue_context_assignment_version"] == 1
    assert edition["dialogue_context_assignments"] == {
        digest(items[0]): ["common"], digest(items[1]): ["narrative", "iterative"], digest(items[2]): ["common"]}
    entries = entries_from_edition(edition)
    assert [entry["principle"] for entry in entries] == items
    assert [entry["sources"][0]["context_types"] for entry in entries] == [
        ["common"], ["narrative", "iterative"], ["common"]]
    assert [entry["id"] for entry in entries] == [entry["id"] for entry in entries_from_edition(old_edition)]
    assert app.learn("reasoning", _style_observation())["id"] == edition["id"] and len(calls) == 2


@pytest.mark.parametrize("tag", ["clinical", "narrative,industry"])
def test_unknown_memory_context_is_not_accepted_as_a_style_and_replay_does_not_rebuy(tmp_path, tag):
    calls = []
    class Client:
        client = SimpleNamespace(close=lambda: None)
        def chat(self, messages, **options):
            calls.append(messages)
            return _style_memory("Invalid category", tag)
    app = google_reasoning_bank_application({"backend": "fixture", "model": "fixture"}, root=tmp_path,
        user_key="fixture", client_factory=lambda binding: (Client(), "fixture"))
    for _ in range(2):
        with pytest.raises(ValueError, match="^reasoning_bank_invalid_dialogue_context_assignment$"):
            app.learn("reasoning", _style_observation())
    assert len(calls) == 1 and app.repository.list_versions("reasoning") == []
    assert app.active()["scopes"] == {}


def test_context_filter_uses_frozen_source_editions_before_google_rank_without_new_vectors(tmp_path):
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    shared = _style_memory("Same procedural rule")
    historical = _save_style_edition(app, [shared])
    narrative = _save_style_edition(app, [shared], [["narrative"]])
    other = _save_style_edition(app, [_style_memory("Emotional-only rule")], [["emotional"]])
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    assert app.bank.index_edition(historical["id"])["indexed"] == 1
    assert app.bank.index_edition(narrative["id"])["merged"] == 1
    assert app.bank.index_edition(other["id"])["indexed"] == 1
    vector_paths = list((app.repository.root / "reasoning-bank-vectors").rglob("*.jsonl"))
    assert sum(len(path.read_text("utf-8").splitlines()) for path in vector_paths) == 2
    wrong_source = app.bank.retrieve("reasoning", "Current narrative dialogue", edition_ids=[historical["id"]],
        context_types=["narrative"])
    assert wrong_source["matches"] == []
    common_source = app.bank.retrieve("reasoning", "Current dialogue", edition_ids=[historical["id"]], context_types=["common"])
    historical_match, = common_source["matches"]
    assert "context_types" not in historical_match["entry"]["sources"][0]
    selected = app.bank.retrieve("reasoning", "Current narrative dialogue", edition_ids=[narrative["id"], other["id"]],
        context_types=["narrative"])
    match, = selected["matches"]
    assert match["entry"]["principle"] == shared
    assert [source["edition"]["id"] for source in match["entry"]["sources"]] == [narrative["id"]]
    assert selected["receipt"]["upstream_execution"]["candidate_ids"] == [match["entry"]["id"]]
    legacy_query = app.bank.retrieve("reasoning", "Any task", edition_ids=[historical["id"], narrative["id"]])
    old_match, = legacy_query["matches"]
    assert {source["edition"]["id"] for source in old_match["entry"]["sources"]} == {historical["id"], narrative["id"]}
    assert sum(len(path.read_text("utf-8").splitlines()) for path in vector_paths) == 2


def test_context_catalog_freezes_only_matching_whole_memories_with_style_description_queries(tmp_path):
    from lib.bootstrap.contextual_reasoning_memory import contextual_reasoning_memory
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from lib.contexts.workflows.domain.dialogue_style_context import context_system_message
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    common = _style_memory("Historical common guidance")
    narrative = _style_memory("Narrative and iterative guidance", "narrative,iterative")
    emotional = _style_memory("Emotional-only guidance", "emotional")
    oversized = _style_memory("Oversized narrative guidance", "narrative", content="Keep complete. " * 600)
    edition = _save_style_edition(app, [common, narrative, emotional, oversized],
        [["common"], ["narrative", "iterative"], ["emotional"], ["narrative"]])
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    assert app.activate("reasoning", edition["id"])["status"] == "active"
    before = app.active()
    class QueryEmbedding(Embedding):
        def __init__(self):
            self.queries = []
        def embed_query(self, query, instruction):
            self.queries.append(query)
            return super().embed_query(query, instruction)
    embedder = QueryEmbedding()
    result = contextual_reasoning_memory(root=tmp_path, user_key="fixture", embedder=embedder)
    catalog = result["catalog"]["contexts"]
    original = dialogue_style_context_catalog()["contexts"]
    assert set(result) == {"catalog", "selection_receipts", "base_projection"} and app.active() == before
    assert result["base_projection"] is None
    assert set(embedder.queries) <= {item["description"] for item in original.values()}
    assert {original[kind]["description"] for kind in ("common", "narrative", "emotional")} <= set(embedder.queries)
    assert all(receipt["query"] == original[kind]["description"]
               for kind, receipt in result["selection_receipts"].items())
    assert common in catalog["common"]["content"] and narrative in catalog["narrative"]["content"]
    assert emotional in catalog["emotional"]["content"]
    assert narrative in catalog["iterative"]["content"]
    assert all(narrative not in item["content"] for kind, item in catalog.items() if kind not in {"narrative", "iterative"})
    assert all(common not in item["content"] for kind, item in catalog.items() if kind != "common")
    assert oversized not in catalog["narrative"]["content"] and "Oversized narrative guidance" not in catalog["narrative"]["content"]
    assert all(len(item["content"]) <= 6000 for item in catalog.values())
    assert result["selection_receipts"]["narrative"]["applied"]
    assert any(item["reason"] == "context_capacity" for item in result["selection_receipts"]["narrative"]["omitted"])
    narrative_block, = catalog["narrative"]["memory_blocks"]
    iterative_block, = catalog["iterative"]["memory_blocks"]
    assert narrative_block == iterative_block and narrative_block["block"].count(narrative) == 1
    assert "memory_blocks" not in catalog["direct"]
    message, _ = context_system_message({"dialogue_style_context_version": 1,
        "dialogue_style_context_catalog": result["catalog"]}, ["narrative", "iterative"])
    assert message["content"].count(narrative) == 1


def test_style_catalog_projects_only_an_authenticated_exact_adopted_base_and_keeps_frozen_card_ids(tmp_path):
    from hashlib import sha256
    from lib.bootstrap.contextual_reasoning_memory import contextual_reasoning_memory
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from lib.contexts.workflows.domain.dialogue_style_context import freeze_catalog
    from lib.contexts.workflows.domain.public_experience import initial_experience_text
    base = dialogue_style_context_catalog()
    base["contexts"]["narrative"]["content"] += " 原任务冻结的表达原则。"
    base = freeze_catalog(base["contexts"])
    empty = contextual_reasoning_memory(root=tmp_path, user_key="fixture", base_catalog=base)
    assert empty == {"catalog": base, "selection_receipts": {}, "base_projection": None}
    app = google_reasoning_bank_application(root=tmp_path, user_key="fixture")
    memory = _style_memory("Adopted narrative memory", "narrative")
    edition = _save_style_edition(app, [memory], [["narrative"]])
    app.bank = reasoning_bank_application(root=tmp_path, user_key="fixture", embedder=Embedding())
    assert app.activate("reasoning", edition["id"])["status"] == "active"
    exact = contextual_reasoning_memory(root=tmp_path, user_key="fixture", embedder=Embedding(),
        reasoning_text=edition["text"], base_catalog=base)
    projection = exact["base_projection"]
    assert projection["edition_id"] == edition["id"] and projection["edition_sha256"] == edition["sha256"]
    assert projection["submitted_text_sha256"] == sha256(edition["text"].encode("utf-8")).hexdigest()
    assert projection["base_text"] == initial_experience_text("reasoning")
    assert projection["base_text_sha256"] == sha256(projection["base_text"].encode("utf-8")).hexdigest()
    assert memory in exact["catalog"]["contexts"]["narrative"]["content"]
    assert exact["catalog"]["contexts"]["narrative"]["content"].startswith(base["contexts"]["narrative"]["content"])
    assert list(exact["catalog"]["contexts"]) == list(base["contexts"])
    for kind in base["contexts"]:
        assert exact["catalog"]["contexts"][kind]["description"] == base["contexts"][kind]["description"]
    custom = contextual_reasoning_memory(root=tmp_path, user_key="fixture", embedder=Embedding(),
        reasoning_text=edition["text"] + "\n用户明确保留的编辑。", base_catalog=base)
    assert custom["base_projection"] is None
    assert app.repository.get_version(edition["id"])["text"] == edition["text"]
