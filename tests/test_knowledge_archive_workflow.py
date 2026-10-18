"""Exercise archival recall through the real workflow with offline clients."""
from copy import deepcopy
import json

import pytest

from lib.domain.knowledge_agent import KNOWLEDGE_AGENT_ARCHIVE_INSTRUCTION
from lib.infrastructure.knowledge_agent_session import digest
from lib.infrastructure.knowledge_agent_tools import KnowledgeAgentTools
from lib.infrastructure.knowledge_archive_tools import KnowledgeArchiveTools
from tests.test_workflow_knowledge_agent import Client, Worker, context, final, material, tool


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class ArchiveWorker(Worker):
    def __init__(self, path, client):
        super().__init__(path, client)
        self.recipe["knowledge_agent_archive_version"] = 1
        self.recipe["knowledge_agent_instruction"] += "\n\n" + KNOWLEDGE_AGENT_ARCHIVE_INSTRUCTION


def seed(store, session, operation, text, *, private=""):
    messages = [{"role": "user", "content": text},
                {"role": "assistant", "content": '{"tasks":[]}',
                 **({"reasoning_content": private} if private else {})}]
    store.remember_turn(session, operation, messages, archive=True)
    return messages


def test_large_recent_turn_retains_archive_and_early_work_is_recallable(tmp_path, context):
    client = Client([])
    worker = ArchiveWorker(tmp_path, client)
    session = context[1]["session_id"]
    store = worker.knowledge_store()
    seed(store, session, "early-operation", "Find the cobalt actuator failure condition.")
    for number in range(101):
        seed(store, session, f"later-{number}", f"Unrelated planning operation {number}")
    original = seed(store, session, "large-latest", "Still need to check the pressure limit.",
                    private="private-provider-state " * 5000)
    assert store.history(session) == []
    hit = store.archive_search(session, "cobalt")["hits"][0]
    client.replies = [tool("conversation_search", {"query": "cobalt"}, "recall"),
                      tool("conversation_read", {"ref": hit["ref"]}, "read"), final()]
    assert worker.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    memory = worker.state["qa_director"]["knowledge_context"]
    assert memory["archived_operations"] == 103
    assert memory["archive_previews"] > 0
    assert memory["retained_messages"] == 0
    assert memory["summary_kind"] == "extractive_public_archive"
    initial = json.loads(client.requests[0][-1]["content"])
    assert initial["knowledge_agent_memory"]["conversation_archive"]["entries"]
    result = json.loads(client.requests[2][-1]["content"])
    assert result["history_is_not_evidence"] and not result["archive_is_dialogue"]
    assert "cobalt" in result["archive"]["text"]
    assert "private-provider-state" not in json.dumps(client.requests)
    assert store.history(session, max_bytes=200000)[-2:] == original
    worker.commit_knowledge_plan(["batch", 0])
    resumed = ArchiveWorker(tmp_path, Client([]))
    assert resumed.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    resumed.commit_knowledge_plan(["batch", 0])
    assert resumed._client.requests == []
    assert resumed.knowledge_store().archive_overview(session)["total_operations"] == 104
    # Frozen input metrics remain those of the request, not newly appended work.
    assert resumed.state["qa_director"]["knowledge_context"]["archived_operations"] == 103
    assert len(client.requests) == 3


def test_disabled_tools_keeps_overview_without_calling_a_summarizer(tmp_path, context):
    worker = ArchiveWorker(tmp_path, Client(['{"tasks": []}']))
    worker.recipe["qa_director"]["tools_mode"] = "disabled"
    seed(worker.knowledge_store(), context[1]["session_id"], "previous", "An unfinished pressure check")
    worker.knowledge_agent_plan(["batch", 0], {}, context)
    request = json.loads(worker._client.requests[0][-1]["content"])
    assert not request["knowledge_agent_memory"]["archive_tools_available"]
    assert request["knowledge_agent_memory"]["conversation_archive"]["total_operations"] == 1
    assert len(worker._client.requests) == 1


def test_rejected_plan_does_not_enter_archive(tmp_path, context):
    worker = ArchiveWorker(tmp_path, Client([final()]))
    worker.knowledge_agent_plan(["batch", 0], {}, context)
    worker.invalidate_knowledge_plan(["batch", 0])
    worker.commit_knowledge_plan(["batch", 0])
    assert worker.knowledge_store().archive_overview(context[1]["session_id"])["total_operations"] == 0


def test_archive_version_changes_session_not_knowledge_identity(tmp_path):
    class EmptyPublished:
        def source_recent(self, *args, **kwargs):
            return []

    worker = Worker(tmp_path, Client([]))
    worker._director_evidence_materials = lambda unit: [material()]
    old = worker.prepare_knowledge_agent([{}], EmptyPublished(), ["sft"])[1]
    worker.recipe["knowledge_agent_archive_version"] = 1
    new = worker.prepare_knowledge_agent([{}], EmptyPublished(), ["sft"])[1]
    assert old["knowledge_id"] == new["knowledge_id"]
    assert old["session_id"] != new["session_id"]


def test_legacy_recipe_has_no_new_memory_or_tools(tmp_path, context):
    client = Client([final()])
    worker = Worker(tmp_path, client)
    worker.knowledge_agent_plan(["batch", 0], {}, context)
    assert "conversation_archive" not in json.dumps(client.requests)
    assert "archive_version" not in worker.state["qa_director"]["knowledge_context"]
    worker.commit_knowledge_plan(["batch", 0])
    assert worker.knowledge_store().archive_overview(context[1]["session_id"])["total_operations"] == 0


def test_archive_tools_keep_exact_scope_and_cannot_forge_source_tools(tmp_path):
    worker = ArchiveWorker(tmp_path, Client([]))
    store = worker.knowledge_store()
    seed(store, "session-a", "old", "reserved private experiment")
    ref = store.archive_overview("session-a")["entries"][0]["ref"]
    tools = KnowledgeArchiveTools(KnowledgeAgentTools([material()]), store, "session-b")
    assert tools.execute("conversation_search", {"query": "experiment"})["archive"]["hits"] == []
    with pytest.raises(ValueError):
        tools.execute("conversation_read", {"ref": ref})
    result = tools.execute("source_read", {"material_id": "material-a", "start_line": 1, "end_line": 1})
    assert result["evidence"]["text"] == "测试压力为45 kPa。\n"
    assert {row["function"]["name"] for row in tools.schemas()} == {
        "source_search", "source_read", "conversation_search", "conversation_read"}


@pytest.mark.parametrize("name,args", [
    ([], {}), ("conversation_search", {"query": "x", "session": "another"}),
    ("conversation_search", {"query": "x", "limit": True}),
    ("conversation_search", {"query": ""}),
    ("conversation_read", {"ref": "a" * 64, "offset": -1}),
    ("conversation_read", {"ref": "a" * 64, "limit": 2001}),
    ("conversation_read", {"ref": "a" * 64, "offset": False}),
    ("conversation_read", {"ref": "../secret"}),
])
def test_archive_tool_arguments_are_bounded(tmp_path, name, args):
    worker = ArchiveWorker(tmp_path, Client([]))
    tools = KnowledgeArchiveTools(KnowledgeAgentTools([material()]), worker.knowledge_store(), "session")
    with pytest.raises(ValueError):
        tools.execute(name, args)
