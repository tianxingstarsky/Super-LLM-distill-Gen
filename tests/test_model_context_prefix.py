"""A stable teacher prefix precedes dynamic tasks without leaking raw sources."""
from copy import deepcopy
import json

from lib.infrastructure.model_context_prefix import serialize_context_request
from lib.domain.workflow_quality import canonical


PACK = {"version": 1, "id": "shared", "prefix_sha256": "digest", "materials": [{"text": "Accepted full chapter."}]}
REF = {"version": 1, "id": "shared", "prefix_sha256": "digest"}


def loader(path, reference):
    assert reference == REF
    return deepcopy(PACK)


def request(identifier, feedback):
    return {"source_context": {"id": identifier, "text": "Accepted anchor.", "source_context_pack": REF,
                              "source_context": {"text": "RAW_SOURCE_SENTINEL"},
                              "document_reading": {"parsed_text": "RAW_PARSE_SENTINEL"}},
            "qa_contract": {"question": "Question " + identifier}, "feedback": feedback}


def test_reusable_prefix_is_byte_identical_across_different_tasks_and_feedback():
    first = serialize_context_request("unused", request("one", None), instruction="Concise", pack_loader=loader)
    second = serialize_context_request("unused", request("two", "Revise"), instruction="Reflective", pack_loader=loader)
    prefix = '{"teacher_context_pack":' + json.dumps([PACK], ensure_ascii=False, separators=(",", ":"))
    assert first.startswith(prefix + ",") and second.startswith(prefix + ",")
    assert first != second
    assert json.loads(first)["node_task_instructions"] == "Concise"
    assert json.loads(second)["source_context"]["id"] == "two"
    assert "RAW_" not in first


def test_repeated_pack_references_are_loaded_once_and_input_is_not_mutated():
    data = {"candidates": [request("one", None)["source_context"], request("two", None)["source_context"]]}
    before = deepcopy(data)
    seen = []
    def counted(path, reference):
        seen.append(reference)
        return loader(path, reference)
    rendered = serialize_context_request("unused", data, pack_loader=counted)
    assert len(seen) == 1 and data == before
    assert len(json.loads(rendered)["teacher_context_pack"]) == 1


def test_blinded_context_review_never_loads_teacher_pack():
    data = {"learner_context_only": True, "source_context_pack": REF, "learner_messages": []}
    def forbidden(*args): raise AssertionError("A blinded reviewer must not load private context.")
    assert serialize_context_request("unused", data, pack_loader=forbidden) == canonical(data)


def test_requests_without_packs_keep_historical_serialization():
    data = {"question": "Q", "feedback": None, "source": {"text": "S"}}
    assert serialize_context_request("unused", data, instruction="RULE") == canonical(data)


def test_review_uses_shared_pack_without_duplicating_its_full_text_in_suffix():
    text = PACK["materials"][0]["text"]
    data = {"source_evidence": {"source_context_pack": REF, "teacher_evidence": text,
                               "teacher_evidence_is_pack": True, "source_kind": "document"}}
    rendered = serialize_context_request("unused", data, pack_loader=loader)
    assert rendered.count(text) == 1
    assert "teacher_evidence" not in json.loads(rendered)["source_evidence"]
    assert data["source_evidence"]["teacher_evidence"] == text
