"""Copy immutable run settings into a separate, editable creation draft."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat

import streamlit as st
from filelock import Timeout

from lib.domain.creation_draft import validate_creation_draft
from lib.domain.workflow_scale import validate_node_models
from lib.domain.workflow_generation import validate_node_generation
from lib.domain.workflow_package_review import validate_package_review
from lib.domain.workflow_qa_director import validate_qa_director
from lib.domain.reasoning_trim import validate_reasoning_trim
from lib.domain.workflow_node_prompts import validate_node_prompts, active_node_prompt_ids
from lib.domain.workflow_graph import execution_graph


_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"})
_AGENT_SUFFIXES = frozenset({".json", ".jsonl"})
_MAX_SOURCE_BYTES = 50 * 1024 * 1024
_MAX_MATCH_BYTES = 200 * 1024 * 1024
_ERROR = "配置未能复制，当前草稿仍保留。请检查本机存储后重试。"


def _source_mode(recipe: dict) -> str:
    sources = recipe.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 200 or any(not isinstance(row, dict) for row in sources):
        raise ValueError("invalid_reusable_recipe")
    if not sources:
        return "开放需求"
    return ("Agent 上下文" if all(Path(str(row.get("file", ""))).suffix.casefold() in _AGENT_SUFFIXES
                               for row in sources) else "文档资料")


def _matching_sources(sources: list[dict], inventory: list[dict], suffixes: frozenset[str]):
    """Only inventory paths can be selected; recipe paths are never opened.

    A matching basename alone cannot distinguish changed files. Check its
    content digest as well, with bounded reads, and report unmatched sources.
    """
    by_name = {}
    for row in inventory:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        path = Path(row["path"])
        if path.suffix.casefold() in suffixes:
            by_name.setdefault(path.name.casefold(), []).append(path)
    digests = {}
    remaining = _MAX_MATCH_BYTES
    selected, missing = [], []
    for source in sources:
        name = source.get("name")
        if not isinstance(name, str) or not name or len(name) > 4096:
            raise ValueError("invalid_reusable_recipe")
        expected = source.get("sha256")
        match = None
        if isinstance(expected, str) and re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            for path in by_name.get(name.casefold(), []):
                try:
                    info = path.lstat()
                    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0)
                            or info.st_nlink > 1 or info.st_size > _MAX_SOURCE_BYTES):
                        continue
                    if any(parent.is_symlink() or getattr(parent.lstat(), "st_reparse_tag", 0)
                           for parent in path.parents):
                        continue
                    if path not in digests:
                        if info.st_size > remaining:
                            continue
                        digest = hashlib.sha256()
                        size = 0
                        with path.open("rb") as handle:
                            while chunk := handle.read(min(1024 * 1024, remaining + 1)):
                                remaining -= len(chunk)
                                size += len(chunk)
                                if remaining < 0 or size > _MAX_SOURCE_BYTES:
                                    raise ValueError("source_matching_limit")
                                digest.update(chunk)
                        digests[path] = digest.hexdigest()
                    if digests[path] == expected.casefold():
                        match = str(path)
                        break
                except (OSError, ValueError):
                    continue
        if match is None:
            missing.append(name)
        elif match not in selected:
            selected.append(match)
    return selected, missing


def recipe_to_draft(recipe: dict, name: str, workspace: str, source_files: list[dict]) -> dict:
    """Return validated form values and separate, session-only model references."""
    if not isinstance(recipe, dict) or not isinstance(name, str):
        raise ValueError("invalid_reusable_recipe")
    mode = _source_mode(recipe)
    values = {
        f"workflow-name:{workspace}": name,
        f"workflow-source-mode:{workspace}": mode,
        f"workflow-preset:{workspace}": "自动推荐",
        f"workflow-targets:{workspace}:自动推荐": recipe.get("targets", []),
        f"workflow-count:{workspace}": (recipe["sample_count"] if recipe.get("sample_count") is not None
                                       else recipe.get("tasks", 10)),
        f"workflow-max-units:{workspace}": recipe.get("max_units", 100),
        f"workflow-concurrency:{workspace}": recipe.get("concurrency", 1),
        f"workflow-batch-size:{workspace}": recipe.get("batch_size", 100),
        f"workflow-chunk-chars:{workspace}": recipe.get("chunk_chars", 2000),
        f"workflow-turns:{workspace}": recipe.get("conversation_turns", 3),
    }
    values[f"workflow-open-brief:{workspace}" if mode == "开放需求" else
           f"workflow-source-brief:{workspace}:{mode}"] = recipe.get("brief", "")
    style = recipe.get("sft_output_style")
    if style is None:
        preferences = recipe.get("generation_preferences") or {}
        style = (preferences.get("values") or {}).get("cot_style", "separated")
        if style not in {"separated", "drop"}:
            style = "separated"
    values[f"workflow-sft-output-style:{workspace}"] = style
    for node, generation in validate_node_generation(recipe.get("node_generation")).items():
        values[f"workflow-generation-enabled:{workspace}:{node}"] = generation["enabled"]
        values[f"workflow-generation-style:{workspace}:{node}"] = generation["style"]
        values[f"workflow-generation-instruction:{workspace}:{node}"] = generation["instruction"]
    trim = validate_reasoning_trim(recipe.get("reasoning_trim"))
    if trim is not None:
        values[f"workflow-trim-enabled:{workspace}"] = trim["enabled"]
        values[f"workflow-trim-template:{workspace}"] = trim["template"]
        values[f"workflow-trim-instruction:{workspace}"] = trim["instruction"]
        values[f"workflow-trim-prompt:{workspace}"] = trim["custom_prompt"]
    package_review = validate_package_review(recipe.get("package_review"))
    if "package_review" in recipe:
        values[f"workflow-package-review-enabled:{workspace}"] = package_review["enabled"]
    if package_review["enabled"]:
        values[f"workflow-package-review-mode:{workspace}"] = package_review["mode"]
        values[f"workflow-package-review-percent:{workspace}"] = package_review["sample_percent"]
        values[f"workflow-package-review-limit:{workspace}"] = package_review["max_samples_per_target"]
    director = validate_qa_director(recipe.get("qa_director"))
    if "qa_director" in recipe:
        values[f"workflow-director-enabled:{workspace}"] = director["enabled"]
    if director["enabled"]:
        values[f"workflow-director-batch:{workspace}"] = director["batch_size"]
        values[f"workflow-director-history:{workspace}"] = director["history_limit"]
        values[f"workflow-director-question-rules:{workspace}"] = director["question_rules"]
        values[f"workflow-director-answer-rules:{workspace}"] = director["answer_rules"]
        for name, weight in director["type_weights"].items():
            values[f"workflow-director-weight:{workspace}:{name}"] = weight
    prompt_mode = ("多模态文档" if (recipe.get("document_parser") or {}).get("mode") == "vision" else mode)
    copied_prompts = validate_node_prompts(recipe.get("node_prompt_templates", recipe.get("node_prompts")))
    prompt_nodes, _ = execution_graph(recipe.get("targets", []), reasoning_trim=bool((trim or {}).get("enabled")),
                                     qa_director=director)
    for node in prompt_nodes:
        for prompt_id in active_node_prompt_ids(node, prompt_mode, node_generation=recipe.get("node_generation"),
                                                package_review=package_review, qa_director=director):
            if prompt_id in copied_prompts.get(node, {}):
                values[f"workflow-node-prompt:{workspace}:{node}:{prompt_id}"] = copied_prompts[node][prompt_id]
    research = recipe.get("web_research")
    if isinstance(research, dict):
        values[f"workflow-web-research-query:{workspace}"] = research.get("query", "")
        values[f"workflow-web-research-count:{workspace}"] = research.get("count", 3)
        extra = research.get("more_queries", [])
        if not isinstance(extra, list) or any(not isinstance(query, str) for query in extra):
            raise ValueError("invalid_reusable_recipe")
        values[f"workflow-web-research-more:{workspace}"] = "\n".join(extra)
    suffixes = _AGENT_SUFFIXES if mode == "Agent 上下文" else _DOCUMENT_SUFFIXES
    selected, missing = _matching_sources(recipe.get("sources", []), source_files, suffixes)
    if mode != "开放需求":
        values[f"workflow-sources:{workspace}:{mode}"] = selected
    return {"values": validate_creation_draft(values), "missing_sources": missing,
            "node_bindings": validate_node_models(recipe.get("node_models")),
            "evaluation_references_omitted": bool(recipe.get("evaluation_references"))}


def reuse_run_as_draft(application, draft_application, workspace: str, run_id: str) -> bool:
    """Button callback: preserve current edits, then open copied settings.

    On success ``workflow-reuse-notice:{workspace}`` holds source warnings.
    On failure ``workflow-reuse-error:{workspace}`` holds a safe user message;
    form values, navigation and the immutable source run remain untouched.
    """
    try:
        recipe = application.recipe(run_id)
        state = application.state(run_id)
        mode = _source_mode(recipe)
        suffixes = _AGENT_SUFFIXES if mode == "Agent 上下文" else _DOCUMENT_SUFFIXES
        # Search the full bounded library rather than the former 501-row
        # picker window. Content matching still verifies each candidate.
        inventory = (application.source_files(workspace, suffixes, limit=5000)
                     if recipe.get("sources") else [])
        copied = recipe_to_draft(recipe, state.get("name", ""), workspace, inventory)
        current = st.session_state.get(f"workflow-form-draft:{workspace}")
        if current is None:
            current = draft_application.load()
        backup = draft_application.save_snapshot(values=current) if current else None
        draft_application.replace(copied["values"])
    except (OSError, ValueError, Timeout, TypeError, AttributeError, KeyError):
        st.session_state[f"workflow-reuse-error:{workspace}"] = _ERROR
        return False

    for key in list(st.session_state):
        if not isinstance(key, str):
            continue
        belongs = key.endswith(f":{workspace}") or f":{workspace}:" in key
        if belongs and key.startswith(("workflow-", "node-model:")):
            st.session_state.pop(key, None)
    st.session_state[f"workflow-form-draft:{workspace}"] = copied["values"]
    st.session_state[f"workflow-draft-loaded:{workspace}"] = True
    st.session_state[f"workflow-web-research-enabled:{workspace}"] = False
    st.session_state[f"workflow-web-research-session-consent:{workspace}"] = False
    st.session_state[f"workflow-node-bindings:{workspace}"] = copied["node_bindings"]
    if backup:
        st.session_state[f"work-draft-switch-backup:{workspace}"] = backup
    else:
        st.session_state.pop(f"work-draft-switch-backup:{workspace}", None)
    st.session_state[f"workflow-reuse-notice:{workspace}"] = {
        "missing_sources": copied["missing_sources"],
        "evaluation_references_omitted": copied["evaluation_references_omitted"],
    }
    st.session_state.pop(f"work-drafts-error:{workspace}", None)
    st.session_state["nav"] = "自动工作流"
    return True
