"""Project native generation onto real chat roles, with scoped system memory."""
from copy import deepcopy
import json

from ..domain.authored_task_contract import compile_task_contract, project_task_context, task_contract_instruction
from ..domain.native_cot import NATIVE_EXECUTION_STATE, NATIVE_CONTEXT_CONTRACT


def native_cot_messages(template, task, instruction):
    contract = compile_task_contract(task)
    mode = task.get(NATIVE_CONTEXT_CONTRACT) or {}
    learner_visible_source = (mode.get("learning_goal") == "document_reading"
        and mode.get("learner_excerpt_only") is True
        and mode.get("teacher_source_visibility") == "learner_visible")
    if learner_visible_source:
        # This host mode follows the application's actual-message check.
        # Reuse its exact excerpt without repeating it in private system memory.
        contract = {**contract, "source_scope": "learner_excerpt_only",
            "visible_excerpts": [task["source"]["text"]]}
    task = project_task_context(task, contract)
    system = template + "\n" + (instruction or "") + "\n" + task_contract_instruction(contract, version=2)
    if task.get(NATIVE_CONTEXT_CONTRACT) is not None:
        system += ("\n<" + NATIVE_CONTEXT_CONTRACT + ">\n"
            + json.dumps(task[NATIVE_CONTEXT_CONTRACT], ensure_ascii=False, separators=(",", ":"))
            + "\n</" + NATIVE_CONTEXT_CONTRACT + ">")
    source = task.get("source")
    if source and not learner_visible_source:
        system += "\n<knowledge>\n" + json.dumps(source, ensure_ascii=False) + "\n</knowledge>"
    # Prior output is an editable proposal, never an additional user request,
    # a verified assistant turn, or an independent source of facts.
    if task.get("previous_candidate") or task.get("revision_feedback"):
        system += ("\n<revision_context>下列旧稿和疑点仅供修订参考，可能错误；依据真实对话和知识重新判断，"
                   "同时修正推理和答案，不保全旧结论、不把疑点当事实。\n" + json.dumps({
                       k: task[k] for k in ("previous_candidate", "revision_feedback") if k in task},
                       ensure_ascii=False) + "\n</revision_context>")
    if NATIVE_EXECUTION_STATE in task:
        system += ("\n<" + NATIVE_EXECUTION_STATE + ">\n"
                   + json.dumps(task[NATIVE_EXECUTION_STATE], ensure_ascii=False, separators=(",", ":"))
                   + "\n</" + NATIVE_EXECUTION_STATE + ">")
    return [{"role": "system", "content": system}, *deepcopy(task["prompt"])]
