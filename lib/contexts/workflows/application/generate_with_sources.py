"""Bounded source lookup in the author's own conversation before final delivery."""
from copy import deepcopy
import json

from lib.contexts.models.contracts import parse_json_robust
from ..domain.generation_sources import GenerationSources, SOURCE_LOOKUP_INSTRUCTION, evidence_tools, validate_source_lookup
from ..domain.reasoning_checklist import checklist_digest


class GenerateWithSources:
    def __init__(self, port, config, *, strict_submission=False, local_edit=False, edit_base=None):
        self.port = port
        self.config = validate_source_lookup(config)
        self.strict_submission = strict_submission
        self.local_edit = local_edit and strict_submission
        self.edit_base = deepcopy(edit_base) if strict_submission else None

    def execute(self, key, prompt_id, task, instruction):
        port = self.port
        context = port.prepare_generation_context(task)
        sources = GenerationSources(context)
        messages = port.generation_source_messages(prompt_id, task,
            instruction + SOURCE_LOOKUP_INSTRUCTION, sources.index())
        tools = evidence_tools()
        from ..domain.authored_submission import (SUBMIT_AUTHORED, authored_submission_tool,
            strict_tool_schema, assemble_authored_submission)
        submission_tools = []
        if self.strict_submission:
            for tool in tools:
                tool["function"].update(strict=True, parameters=strict_tool_schema(tool["function"]["parameters"]))
            submission_tools = [authored_submission_tool(answer_required=prompt_id == "workflow.cot_generate")]
            if self.edit_base is not None:
                from ..domain.cot_style_transfer import style_edit_tool
                submission_tools = [style_edit_tool()]
            elif self.local_edit:
                from ..domain.authored_edits import authored_edit_tool
                submission_tools.append(authored_edit_tool(answer_required=prompt_id == "workflow.cot_generate"))
            tools.extend(submission_tools)
        transcript, cache, seen_ids = [], {}, set()
        issue, candidate, submission_key = None, None, None
        turns = self.config["max_rounds"] + 1
        binding = checklist_digest([messages, sources.binding, self.config, tools])
        for turn in range(turns):
            port.check_reasoning_cancel()
            current_tools = submission_tools if self.strict_submission and turn == turns - 1 else tools
            turn_key = [*key, "source_lookup_v1", binding, turn]
            assistant = port.generation_source_turn(turn_key,
                deepcopy(messages), current_tools, final=turn == turns - 1,
                **({"edit_base": self.edit_base} if self.edit_base is not None else {}))
            transcript.append({"assistant": deepcopy(assistant), "observations": []})
            if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
                issue = "invalid_author_response"
                break
            calls = assistant.get("tool_calls", [])
            if not isinstance(calls, list):
                issue = "invalid_author_tools"
                break
            # Preserve complete provider-native thoughts, tool IDs and provider
            # items. They remain in the same assistant history for continuation.
            messages.append(deepcopy(assistant))
            from ..domain.authored_edits import EDIT_AUTHORED, apply_authored_edits
            delivery_tool = EDIT_AUTHORED if self.edit_base is not None else SUBMIT_AUTHORED
            if self.strict_submission and any(isinstance(c, dict) and c.get("function", {}).get("name") == delivery_tool for c in calls):
                submission_key = turn_key
                try:
                    if self.edit_base is not None:
                        candidate, patch = apply_authored_edits(self.edit_base, assistant, answer_required=False)
                        transcript[-1]["applied_patch"] = patch
                    else:
                        candidate = assemble_authored_submission(assistant, answer_required=prompt_id == "workflow.cot_generate")
                except ValueError as error:
                    issue = str(error)
                break
            if not calls:
                if self.strict_submission:
                    issue = "authored_submission_missing_or_ambiguous"
                    break
                try:
                    content = assistant.get("content")
                    if not isinstance(content, str):
                        raise ValueError("invalid_author_content")
                    candidate = parse_json_robust(content)
                except (ValueError, TypeError):
                    issue = "author_response_json_invalid"
                break
            if (turn == turns - 1 or len(calls) > 4
                    or any(not isinstance(c, dict) or not isinstance(c.get("id"), str)
                           or not c["id"] or c["id"] in seen_ids for c in calls)
                    or len({c["id"] for c in calls}) != len(calls)):
                issue = "author_tool_limit_or_invalid_ids"
                break
            repeated = True
            for call in calls:
                port.check_reasoning_cancel()
                seen_ids.add(call["id"])
                function = call.get("function", {})
                name = function.get("name") if isinstance(function, dict) else None
                raw = function.get("arguments") if isinstance(function, dict) else None
                try:
                    arguments = json.loads(raw) if isinstance(raw, str) and len(raw) <= 16000 else None
                except ValueError:
                    arguments = None
                identity = checklist_digest([name, arguments, raw if arguments is None else None])
                cached = identity in cache
                repeated &= cached
                if not cached:
                    cache[identity] = self._execute_tool(sources, name, arguments)
                observation = {**deepcopy(cache[identity]), "cached": cached}
                transcript[-1]["observations"].append({"call_id": call["id"], "result": observation})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(observation, ensure_ascii=False)})
            if repeated:
                # A repeated lookup cannot trigger an unbounded author/reviewer
                # loop. Preserve the actual draft and observations for inspection.
                issue = "repeated_source_lookup_without_progress"
                break
        receipt = {"version": 1, "mode": "during_generation", "independent_review": False,
                   "semantic_status": "not_assessed", "source_sha256": sources.binding,
                   "config": deepcopy(self.config), "model_turns": len(transcript),
                   "status": "incomplete" if issue else "completed", "issue": issue,
                   "candidate_sha256": checklist_digest(candidate), "transcript": transcript}
        if self.local_edit and submission_key is not None:
            receipt["submission_key"] = submission_key
        return candidate, receipt

    def _execute_tool(self, sources, name, arguments):
        if not isinstance(arguments, dict):
            return {"ok": False, "issue": "invalid_source_tool_arguments"}
        if name == "search_evidence" and set(arguments) == {"query"}:
            query = arguments["query"]
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
                return {"ok": False, "issue": "invalid_evidence_query"}
            locations = self.port.search_generation_sources(sources.passages, query, self.config["max_hits"])
            return sources.search_receipt(locations)
        if name == "read_evidence" and set(arguments) == {"source_id", "start", "length"}:
            return sources.read(arguments["source_id"], arguments["start"], arguments["length"])
        return {"ok": False, "issue": "unknown_or_invalid_source_tool"}
