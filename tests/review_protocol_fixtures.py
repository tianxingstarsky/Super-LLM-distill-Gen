"""Source-bound offline replies for the current condition-review wire contract.

The caller still owns the fixture's semantic verdict. These helpers do not
call production builders/validators or convert an invalid provider reply into
a pass; the real Workflow must validate and checkpoint every returned reply.
"""
from copy import deepcopy
import json


def source_condition_response(wire, request, verdict, *, quote, derivation, refuted_text=None):
    assert request["claim_review_protocol_version"] == 4
    assert request["condition_contract_version"] == 1
    memories = []
    for message in wire:
        if message["role"] == "system" and message["content"].startswith("<untrusted_source_memory>\n"):
            memories.append(json.loads(message["content"].removeprefix("<untrusted_source_memory>\n")
                                       .removesuffix("\n</untrusted_source_memory>")))
        elif message["role"] == "tool" and message.get("name") == "source_memory":
            memories.append(json.loads(message["content"]))
    if "source_evidence_ref" in request:
        memory = next(item for item in memories if "source_evidence" in item)
        assert quote and quote in memory["source_evidence"]["teacher_evidence"]
        assert request["source_evidence_ref"]["sha256"] == memory["source_evidence_sha256"]
    else:
        # The saved memory-v1 transport sends authenticated selected packs as
        # tool results while retaining only evidence metadata in the task.
        materials = [material for item in memories for pack in item.get("teacher_context_pack", [])
                     for material in pack["materials"]]
        assert quote and any(quote in material.get("text", "") for material in materials)
        assert request["source_evidence"]["source_line_scope"] == "selected"
    common = {"claim_review_protocol_version": 4, "scope_sha256": request["scope"]["scope_sha256"]}
    units = request["target_units"]
    premises = [{"origin": "source", "use": "support", "quote": quote, "source_path": ["teacher_evidence"]}]
    if request["phase"] == "claim_challenge":
        return {**common, "phase": "claim_challenge", "issues": [], "limitations": [],
                "condition_ledger": [{"unit_id": unit["id"], "premises": []} for unit in units]}
    assert request["phase"] == "claim_adjudication"
    return {**common, "coverage": "complete", "checked_unit_ids": [unit["id"] for unit in units],
        "claim_checks": [{"unit_id": unit["id"], "status":
            "refuted" if refuted_text and refuted_text in unit["text"] else "supported",
            "severity": "material", "premises": deepcopy(premises),
            "basis": {"kind": "source", "evidence": [{"quote": quote, "source_path": ["teacher_evidence"]}],
                      "derivation": derivation}} for unit in units],
        "challenge_checks": [], "verdict": deepcopy(verdict)}
