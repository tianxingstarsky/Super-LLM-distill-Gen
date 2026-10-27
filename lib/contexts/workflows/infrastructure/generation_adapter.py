"""Authenticated source reads, checkpointed requests and streaming reasoning delivery."""
from __future__ import annotations
from ..domain.generation import EmptyVisibleAnswer


class CheckpointedGenerationAdapter:
    def __init__(self, runtime, source_lookup=None):
        self._runtime = runtime
        self._source_lookup = source_lookup

    def generation_source_messages(self, *args):
        return self._source_lookup.messages(*args)

    def prepare_generation_context(self, context):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self._runtime).prepare_reasoning_context(context, include_source_scope=True)

    def generation_source_turn(self, *args, **kwargs):
        return self._source_lookup.turn(*args, **kwargs)

    def search_generation_sources(self, *args):
        return self._source_lookup.search(*args)

    @property
    def current_stage(self):
        return self._runtime.stage

    def source_evidence(self, source):
        from lib.infrastructure.workflow_source_context import pack_evidence_text
        return pack_evidence_text(source, self._runtime.path)

    def selected_evidence_text(self, source, contract):
        from lib.infrastructure.source_memory_messages import directed_evidence_text
        return directed_evidence_text(self._runtime.path, source, contract)

    def selected_evidence_blocks(self, source, contract):
        from lib.infrastructure.source_memory_messages import directed_evidence_blocks
        return directed_evidence_blocks(self._runtime.path, source, contract)

    def prepare_source_request(self, payload, source, contract):
        from lib.infrastructure.source_memory_messages import directed_source_request
        return directed_source_request(self._runtime.recipe, payload, source, contract)

    def validate_native_prefill(self):
        from lib.model_reasoning_prefill import validate_prefill_client
        validate_prefill_client(self._runtime.client("generation"))

    def request_native_answer(self, key, payload, *, catch_empty, **options):
        from lib.contexts.models.contracts import ModelRequestError
        try:
            return self._runtime.ask(key, "generation", "workflow.cot_native_answer", payload, **options)
        except (ValueError, ModelRequestError) as error:
            empty_visible = type(error) is ValueError and str(error) == "model_visible_output_missing"
            if isinstance(error, ModelRequestError):
                empty_visible = error.kind == "invalid" and error.code == "model_visible_output_missing"
            if not catch_empty or not empty_visible:
                raise
            raise EmptyVisibleAnswer() from error

    def request(self, *args, **kwargs):
        return self._runtime.ask(*args, **kwargs)

    def check_reasoning_cancel(self):
        self._runtime.check_cancel()

    def prepare_reasoning_context(self, context):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self._runtime).prepare_reasoning_context(context)

    def reasoning_progress(self, *args):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self._runtime).reasoning_progress(*args)

    def reasoning_tool_turn(self, *args):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self._runtime).reasoning_tool_turn(*args)

    def reasoning_review_identity(self):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self._runtime).reasoning_review_identity()

    def reject(self, *args, **kwargs):
        return self._runtime.rejected(*args, **kwargs)

    def assess(self, *args, **kwargs):
        return self._runtime.judge_answer(*args, **kwargs)

    def check_style(self, *args, **kwargs):
        return self._runtime.style_check(*args, **kwargs)

    def check_adherence(self, *args, **kwargs):
        return self._runtime.adherence_check(*args, **kwargs)

    def check_contract(self, *args, **kwargs):
        return self._runtime.qa_contract_check(*args, **kwargs)

    def generation_style(self, *args, **kwargs):
        return self._runtime.generation_style(*args, **kwargs)

    def independent_cot_assessment(self, *args, **kwargs):
        return self._runtime.independent_cot_assessment(*args, **kwargs)

    def separated_questions(self, *args, **kwargs):
        return self._runtime.separated_questions(*args, **kwargs)

    def checkpoint(self, *args, **kwargs):
        return self._runtime.checkpoint(*args, **kwargs)

    def checkpoint_has(self, *args, **kwargs):
        return self._runtime.checkpoint_has(*args, **kwargs)

    def invalidate_checkpoint(self, *args, **kwargs):
        return self._runtime.invalidate_checkpoint(*args, **kwargs)

    def event(self, *args, **kwargs):
        return self._runtime.event(*args, **kwargs)

    def generate_directed_sft(self, *args, **kwargs):
        return self._runtime.directed_sft(*args, **kwargs)

    def extract_cot(self, *args, **kwargs):
        return self._runtime._cot_result(*args, **kwargs)

    def generate_styled_cot(self, *args, **kwargs):
        return self._runtime.styled_cot(*args, **kwargs)

    def generate_native_answer(self, *args, **kwargs):
        return self._runtime.native_styled_answer(*args, **kwargs)


class StreamingReasoningAdapter(CheckpointedGenerationAdapter):
    def skip_trim_stage(self):
        if "trim" in self._runtime.state["stages"]:
            self._runtime.state["stages"]["trim"]["status"] = "skipped"

    def prepare_trim_stage(self):
        from ..domain.targets import STAGES
        self._runtime.state["stages"].setdefault("trim", {
            "label": STAGES["trim"], "status": "pending", "done": 0, "total": 0})

    def trim_collections(self, collections, targets):
        from lib.infrastructure.workflow_rows import WorkflowRows

        class TaggedRows:
            def __len__(self):
                return sum(len(collections[target]) for target in targets)

            def __iter__(self):
                for target in targets:
                    for sample in collections[target]:
                        yield {"target": target, "sample": sample}

        counts = {target: len(collections[target]) for target in targets}
        rows = self._runtime.stage_items("trim", TaggedRows(), self._runtime.trim_reasoning)
        for target in targets:
            collections[target] = WorkflowRows(rows.path, counts[target],
                predicate=lambda row, selected=target: row["trim_target"] == selected,
                transform=lambda row: {key: value for key, value in row.items() if key != "trim_target"})

    def apply_reasoning_trim(self, *args, **kwargs):
        return self._runtime.apply_reasoning_trim(*args, **kwargs)

    def publish_finalized_sft(self, collections):
        from lib.domain.workflow_reasoning_route import finalized_sft_rows
        from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl
        originals = collections["sft"]
        destination = self._runtime.path / "stage-results" / "sft-cot-final.jsonl"

        def checked_rows():
            for row in finalized_sft_rows(originals, collections["cot"]):
                self._runtime.check_cancel()
                yield row

        write_jsonl(destination, checked_rows())
        collections["sft"] = WorkflowRows(destination, len(originals))
