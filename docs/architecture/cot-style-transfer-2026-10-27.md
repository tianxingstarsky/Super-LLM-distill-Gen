# Open reasoning recipes and faithful style transfer

This integration adapts data interfaces and a bounded generation recipe. It
does not install or run the complete NeMo Skills or OpenThoughts framework.
Styled explanations are authored text, not a claim about hidden model reasoning.

## Upstream decisions

- [NeMo Skills solution generation](https://github.com/NVIDIA-NeMo/Skills/blob/bcf059af55c20a89f797724598f9908d126153e6/recipes/openmathreasoning/pipeline/solution_generation.py)
  separates generation, judgment, revised summaries and data preparation.
  We adapt that separation, preserving original traces and checking edits.
  We do not copy the heuristic accepting matching final answers even when both
  are wrong, or use the longest candidate as a quality criterion.
- [OpenThoughts Stratos conversion](https://github.com/open-thoughts/open-thoughts/blob/e86cf2dbbe69ae178aec302be33a3454a7183457/dcft/data_strategies/Stratos/convert.py)
  preserves thought and solution with explicit delimiters. We support this
  format and its separate system message, without synthesizing missing traces.
- [OpenThoughts 3](https://github.com/open-thoughts/open-thoughts/tree/e86cf2dbbe69ae178aec302be33a3454a7183457/open_thoughts_3)
  remains a generation and downstream-evaluation reference. Format support does
  not reproduce its data mix, teacher or training results.
- [NeMo's OpenAI client](https://github.com/NVIDIA-NeMo/Skills/blob/bcf059af55c20a89f797724598f9908d126153e6/nemo_skills/inference/model/openai.py)
  does not directly accept our DeepSeek request extensions. Existing provider
  transport, strict submission, concurrency and checkpoints remain in place.

These are independently implemented adapters, not vendored upstream source.
Links pin inspected revisions. No new framework dependency is needed for import.
Future framework execution adapters should be optional and outside stable
workflow and dataset contracts.

## User path and inputs

Use the existing Agent context JSON / JSONL upload. Select CoT, enable styled
generation and choose **Faithful style transfer**. Language, depth and node
prompts remain editable; personal styles and restore-default controls apply.

Supported envelopes:

- NeMo `problem` / `generation`, with an explicit `<think>...</think>` prefix.
- OpenThoughts `conversations` or compatible `messages`, with `<think>` markers
  or Stratos thought/solution delimiters.
- Existing conversations with separate `reasoning_content` use normal import.

Only delimiter framing whitespace is removed. Full history stays intact.
Incomplete markers, conflicting reasoning fields and invalid roles/tools are
rejected. If a thought-opening marker is missing from NeMo's generation because
it was part of an omitted prompt, no missing boundary is guessed. Plain answers
remain plain answers and cannot enter faithful style transfer without reasoning.
Upstream scores never grant acceptance. Original input bytes remain cached.

## Execution and ownership

`datasets/domain/reasoning_import.py` exposes its pure adapter through
`datasets/contracts.py`. The source-library adapter invokes it only for recipes
with `external_reasoning_import_version=1`. Old tasks do not reinterpret saved
input. JSONL and JSON processing retain their streaming path and source cache.

`GenerateCot` owns `faithful_restyle`. Pure receipt/input rules are in
`workflows/domain/cot_style_transfer.py`.

1. Require an original trace and answer before calling the model.
2. Send the original as private editorial context, separate from learner roles
   and factual evidence. Use the existing rationale submission schema.
3. Request reasoning and its evidence trace only. The program keeps the answer
   byte for byte. An unexpected answer field is rejected, not silently dropped.
4. Preserve both versions. Check factual support, necessary derivations,
   conditions, units and scope. Matching answers and self-check declarations do
   not prove correctness; the original answer may itself be wrong.
5. Quarantine failure without buying another full rewrite automatically.

The integrated-reflection path needs one author request and one content
assessment. Optional source tools, request recovery and separately selected
review nodes can add calls. Existing model bindings control the reviewer.
Only the final assistant turn is edited. Earlier turns remain history.
Changing a wrong answer belongs to generation or the existing repair branch.

Receipts retain original text, content hashes and assessment status. Reports
show the original in a separate comparison view. Training projections exclude
editing receipts and keep their existing fields. Restart reuses completed work.

## Verification and limits

Offline checks cover real upstream envelopes, incomplete delimiters, immutable
answers, semantic rejection, role-separated transport, disk execution, restart
reuse and export identity. Affected generation, configuration and DDD checks
run alongside them. Desktop UI is inspected in the real local service.

No paid API was used for this implementation. Offline doubles verify program
behavior, not model quality, production cost or semantic pass rate. The content
assessor can still be wrong. This optional mode needs a real V4.1-Flash comparison
before becoming the default production recipe.

Validation on 2026-10-27:

- DDD, CoT modes, generation use cases and durable drafts: 118 passed.
- Import/cache, role projection, report rendering and related paths: 164 passed.
- Final UI-label and prompt-library checks: 32 passed.

These sets overlap; the counts are not a combined total. Desktop verification
used the real service on port 8764 at 1600 × 1100. The CoT popup offered both
the editing prompt and the content-assessment prompt. The prior draft mode was
restored after inspection. The screenshot is kept locally at
`.dataforge/cot-style-integration/node-zh.png`. No full-repository regression,
model-quality evaluation or paid request was performed.
