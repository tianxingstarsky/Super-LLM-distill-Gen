"""Public reading context and private user simulation; no I/O or model calls."""
from copy import deepcopy
import re
from .dialogue import dialogue_digest

READING_PROMPTS = ("workflow.reading_scene", "workflow.reading_user", "workflow.reading_answer")
END_DIALOGUE = "<END_DIALOGUE>"


def has_public_reading_context(row):
    receipt = row.get("reading_session", {})
    messages = row.get("messages", [])
    if receipt.get("version") != 1 or not messages or messages[0].get("role") != "system":
        return False
    system = messages[0]
    prefix, marker, material = system.get("content", "").partition("\n\n<reading_material>\n")
    suffix = "\n</reading_material>"
    return bool(marker and prefix and material.endswith(suffix) and material[:-len(suffix)].strip()
        and dialogue_digest(system) == receipt.get("public_context_sha256")
        and dialogue_digest(material[:-len(suffix)]) == receipt.get("source_sha256"))


def reading_session_enabled(recipe):
    config = recipe.get("node_generation", {}).get("multiturn", {})
    return config.get("enabled") is True and config.get("reading_session") is True


def reading_source(unit, prepared):
    """A directed visibility boundary wins over the larger teacher source."""
    contract = unit.get("qa_contract")
    if contract is not None:
        if contract.get("qa_type") == "closed_book" or not contract.get("visible_context", "").strip():
            raise ValueError("reading_session_requires_visible_material")
        return contract["visible_context"]
    source = prepared.evidence_text if prepared.evidence_text is not None else unit.get("text")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("reading_session_requires_document")
    return source


def reading_context(template, source, instruction=""):
    # This exact system message is both generated against and exported.
    return [{"role": "system", "content": template + ("\n\n" + instruction if instruction else "")
             + "\n\n<reading_material>\n" + source + "\n</reading_material>"}]


def simulator_messages(template, scene, source, messages, *, protocol_version=1):
    """Flip conversational roles as in tau2's user simulator; never share CoT."""
    system = template + "\n\n<scenario>\n" + scene + "\n</scenario>\n\n<reading_material>\n" + source + "\n</reading_material>"
    if protocol_version == 2:
        # The story planner read the document. The actor knows only the
        # selected situation, rather than becoming an all-document expert.
        system = "<scenario>\n" + scene + "\n</scenario>\n\n" + template
    history = [{"role": "assistant" if m["role"] == "user" else "user", "content": m["content"]}
               for m in messages if m["role"] in {"user", "assistant"}]
    if not history:
        history = [{"role": "user", "content": "对话开始。"}]
    return [{"role": "system", "content": system}, *history]


def reading_request_messages(prompt_id, template, task, instruction=None):
    if prompt_id == "workflow.reading_scene":
        return [{"role": "system", "content": template + ("\n\n" + task["experience"] if task.get("experience") else "")},
                {"role": "user", "content": task["material"] + "\n\n场景需求：\n" + task.get("brief", "")}]
    # The application has already assembled the exact public learner context
    # or isolated simulator history. Do not append private workflow metadata.
    return deepcopy(task["messages"])


def utterance_advisories(text):
    """Diagnostics, not a length-based quality certificate or hard quota."""
    issues = []
    if re.match(r"\s*(?:用户|User|Human)\s*[:：]", text, re.I):
        issues.append("simulator_role_label")
    if re.match(r"\s*(?:\d+[.)、]|第[一二三四五]+[题问])", text):
        issues.append("simulator_numbered_opening")
    return issues
