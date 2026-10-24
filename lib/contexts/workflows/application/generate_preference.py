"""Build a configured negative answer while preserving the original task."""
from copy import deepcopy
from typing import Protocol
from ..domain.generation import qa_metadata
from ..domain.preference_design import validate_preference_design
from lib.domain.workflow_quality import text_issue, same_answer
from lib.contexts.review.contracts import find_reviewed_failure


class PreferenceGenerationPort(Protocol):
    def request(self, key, role, prompt_id, payload, **options): ...
    def reject(self, record, reason): ...


class GeneratePreferencePair:
    def __init__(self, port: PreferenceGenerationPort, recipe):
        self._port, self._recipe = port, recipe

    def execute(self, sample):
        prefix, chosen = deepcopy(sample["messages"][:-1]), deepcopy(sample["messages"][-1])
        design = validate_preference_design(self._recipe.get("preference_design"))
        reused = (find_reviewed_failure(sample) if self._recipe.get("preference_design_version") == 3
                  and design.get("reuse_reviewed_failures") is True else None)
        if reused:
            alternative = deepcopy(reused["response"][0])
        else:
            value = self._port.request([sample["id"], "contrastive_alternative_v1", design["mode"]], "generation", "workflow.alternative",
            {"prompt": prefix, "reference_response": chosen, "source": sample.get("source_context"),
             "contrastive_design": design, "qa_contract": sample.get("qa_contract")},
                allow_reasoning_fallback=False)
            if not isinstance(value, dict) or any(text_issue(value.get(key)) for key in ("answer", "reasoning")):
                return [self._port.reject(sample, "invalid_dpo_candidate")]
            alternative = {"role": "assistant", "content": value["answer"], "reasoning_content": value["reasoning"]}
        if same_answer(chosen["content"], alternative["content"]):
            return [self._port.reject(sample, "identical_dpo_answers")]
        return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
            "prompt": prefix, "chosen": [chosen], "rejected": [alternative], "tools": deepcopy(sample.get("tools", [])),
            "source_context": deepcopy(sample.get("source_context")), "quotes": deepcopy(sample.get("quotes", [])),
            "evidence_level": sample["evidence_level"], **qa_metadata(sample),
            "preference_design": design, **({"preference_negative_source": reused["source"]} if reused else {}),
            "preference_response_origins": {"chosen": sample.get("reasoning_origin"),
                "rejected": "reviewed_failure_preserved" if reused else "prompt_contrastive_generation"}}]
