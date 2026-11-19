"""Import a real greedy direct answer as historical evidence for a newly sampled run."""

import hashlib
import json
from urllib.parse import urlsplit

from ..domain.experience_evolution import digest
from ..domain.guided_answers import validate_source
from ..domain.guided_teacher_delivery import QWEN_SAMPLING_VERSIONS
from .guided_llama import qwen_text_sampling, wire_body


SAMPLING_KEYS = frozenset(qwen_text_sampling("qwen3.5-0.8b", False))
HISTORICAL_FIRST = "historical_greedy_first"

def _source_facts(source):
    # Teaching options for a new run are authenticated by its own independent registry.
    return digest({k: v for k, v in source.items() if k != "verified_teaching_steps"})


def historical_source_membership(input_path, wanted, source_input_sha):
    """Bind just the selected records to the complete input byte stream of the original run."""
    sha, selected = hashlib.sha256(), {}
    with input_path.open("rb") as lines:
        for number, line in enumerate(lines, 1):
            sha.update(line)
            if not line.strip():
                continue
            row = json.loads(line)
            ident = row["id"]
            if ident not in wanted:
                continue
            if ident in selected:
                raise ValueError("duplicate source id in the historical input")
            selected[ident] = {"line": number, "original_line_sha256": hashlib.sha256(line).hexdigest(),
                               "source_facts_sha256": _source_facts(validate_source(row))}
    if sha.hexdigest() != source_input_sha:
        raise ValueError("original input bytes differ from the historical source manifest")
    if selected.keys() != wanted:
        raise ValueError("historical input does not contain every requested source id")
    return {"source_input_sha256": source_input_sha, "rows": selected}


def historical_source_manifest(raw, current):
    prior = json.loads(raw)
    old = prior["endpoints"]["student"]
    new = {k: v for k, v in current["endpoints"]["student"].items() if k not in {"sampling_profile", "sampling_parameters"}}
    if (current["template_version"] not in QWEN_SAMPLING_VERSIONS or prior.get("schema") != "guided-answer-run/v3"
            or old != new or old.get("enable_thinking") is not False
            or old.get("context_evidence") != "llama_server_slots"
            or urlsplit(old.get("url", "")).hostname not in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("historical first answers require the same local nonthinking student endpoint and capacities")
    return {"source_binding": digest(prior), "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "source_input_sha256": prior["input_sha256"], "source_template_version": prior["template_version"]}


def _check_wires(old, proposed):
    if (type(old.get("temperature")) not in (int, float) or old["temperature"] != 0
            or old.keys() & SAMPLING_KEYS != {"temperature"}
            or old.get("chat_template_kwargs", {}).get("enable_thinking") is not False):
        raise ValueError("historical student did not send an explicitly greedy nonthinking request")
    def stable(wire):
        return wire_body({k: v for k, v in wire.items() if k not in SAMPLING_KEYS})
    if stable(old) != stable(proposed):
        raise ValueError("historical first student messages, grammar or other nonsampling wire fields differ")


def import_historical_answer(folder, source, proposed, history, current_binding, *, same_input, membership=None):
    path = folder / "001-student.json"
    raw = path.read_bytes()
    prior = json.loads(raw)
    ident = source["id"]
    old = prior["request"]
    response = prior.get("response") or {}
    original_identity = digest({"binding": history["source_binding"], "id": ident, "payload": old})
    if (prior.get("identity") != original_identity or prior.get("role") != "student" or prior.get("attempt") != 1
            or prior.get("status") != "complete" or prior.get("origin")
            or not prior.get("attempts") or prior["attempts"][-1].get("status") != "complete"
            or response.get("model") != old.get("model") or response.get("reasoning_content")
            or response.get("finish_reason") not in {"stop", "length"} or not str(response.get("content") or "").strip()):
        raise ValueError("historical first answer requires an original, completed, nonthinking student response")
    if membership is not None:
        if membership["source_facts_sha256"] != _source_facts(source):
            raise ValueError("task facts differ from the original input record")
    elif not same_input:
        case = json.loads((folder / "case.json").read_bytes())
        if (case.get("id") != ident or case.get("binding") != history["source_binding"]
                or case.get("source_sha256") != digest(source)):
            raise ValueError("historical first answer source row is not bound to its original run")
    _check_wires(old, proposed)
    return {
        "role": "student", "attempt": 1, "request": old, "status": "complete", "response": response,
        "identity": digest({"binding": current_binding, "id": ident, "payload": old}),
        "origin": {"kind": HISTORICAL_FIRST, **history, "source_id": ident, "source_identity": original_identity,
                   "source_receipt": "cases/" + folder.name + "/001-student.json",
                   "source_receipt_sha256": hashlib.sha256(raw).hexdigest(), "source_row_sha256": digest(source),
                   **({"original_input_record": membership} if membership is not None else {}),
                   "wire_request_sha256": hashlib.sha256(wire_body(old)).hexdigest(), "response_sha256": digest(response),
                   "current_unsent_wire_sha256": hashlib.sha256(wire_body(proposed)).hexdigest()},
        "attempts": [{"status": "historical_input", "new_model_calls": 0}],
    }


def checked_historical_answer(saved, source, history, binding, *, proposed=None, membership=None):
    origin = saved.get("origin", {})
    original = saved["request"]
    ident = source["id"]
    if proposed is not None:
        _check_wires(original, proposed)
        if origin.get("current_unsent_wire_sha256") != hashlib.sha256(wire_body(proposed)).hexdigest():
            raise ValueError("new sampling request no longer matches the frozen historical import")
    if (origin.get("kind") != HISTORICAL_FIRST or any(origin.get(k) != v for k, v in history.items())
            or (saved.get("status"), saved.get("role"), saved.get("attempt")) != ("complete", "student", 1)
            or origin.get("original_input_record") != membership
            or origin.get("source_id") != ident or origin.get("source_row_sha256") != digest(source)
            or origin.get("source_identity") != digest({"binding": history["source_binding"], "id": ident, "payload": original})
            or saved.get("identity") != digest({"binding": binding, "id": ident, "payload": original})
            or origin.get("wire_request_sha256") != hashlib.sha256(wire_body(original)).hexdigest()
            or origin.get("response_sha256") != digest(saved["response"])):
        raise ValueError("historical first answer differs from its frozen evidence")
    return saved["response"]
