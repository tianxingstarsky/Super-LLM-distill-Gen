# Reference evidence and tool recovery

This is a bug-fix revision of the version 5 environment. The runtime binding now contains `evidence-recovery-5`. New tool feedback cannot reuse a previous positive checkpoint. Protocols 1–4, frozen prompts, candidate text, task files, cache contents and training export fields are unchanged.

## Responsibilities and limits

- `reasoning_references.py` locates exact paragraphs and competing quantities. It computes ratios from literally cited operands using Decimal. A result proves arithmetic conditional on those operands; it does not prove an antecedent or a universal language claim.
- `reasoning_environment_tools.py` advertises exact citation IDs as an alternative to retyping body spans. It makes scope-test and numerical-check obligations explicit. Decimal fields reject percent signs, grouping separators and verbal values. Source quotation text keeps its original punctuation.
- `reasoning_workspace.py` resolves registered IDs before the existing strict validators. Unknown IDs and conflicting representations fail. Batch failures remain local to an entry. A failed calculation input is a tool error; a valid calculation is labelled conditional, not candidate approval.
- `reasoning_assertions.py` fixes a version 5 sentence-boundary defect: a right curly quote after a sentence terminator no longer becomes the start of the next sentence. Historical anchor IDs remain traceable. Complete claim/context binding remains mandatory.
- `reasoning_environment.py` explains source scope, quote versus inference, and the difference between a local example and a universal rule. It does not prescribe a reasoning sequence or allow a reviewer to add missing premises.

Registered body citations expand to the exact existing field and quote. They are not fuzzy matching, generated evidence or automatic semantic endorsement. An unrelated context ID still fails full-anchor binding. Strong claims still require a substantive scope test. Valid negative observations and committed records cannot be overwritten by a later positive summary.

The ratio tool also accepts verbal approximations without inventing a reported number. It returns no truth value for “about half”. A unique explicit half comparison can be evaluated, but the referent and denominator still need evidence. Multiple possible referents, missing source coverage and reviewer disagreement remain unresolved.

## Validation

`tests/test_reasoning_references.py` covers exact ID expansion, negation retention, wrong fields, unknown IDs, conflicting context, partial batch admission, decimal formats, input-error classification, competing ratios and approximation limits. `tests/test_reasoning_environment.py` retains the previous coverage and non-waiver checks. Run these with the architecture suites, then the complete `tests` and `ui_tests` suites sequentially.

The first full regression exposed three stale assertions in `test_native_same_task_regeneration.py` and `test_system_source_memory_protocol.py`. The common SDK fixture already requires the version 2 newline boundary, while the final assertions still expected an empty boundary. The final exact request assertions now match version 2 and additionally check the receipt version and literal visible prefix. The authored reasoning and historical parent remain byte-for-byte asserted. No production behavior was changed to satisfy these tests.

Two director batching fixtures configured a 150,000-token director but left the downstream SFT binding at 131,072 tokens. With the expanded frozen template, unchanged 60 KB custom rules and 32,768-token output reservation, the downstream capacity guard correctly refused the request. The positive fixtures now explicitly configure both model bindings at 150,000; their payload and budget assertions remain. A new negative test retains the smaller downstream limit and requires refusal before any provider call. No production capacity limit was weakened.

A live revision 4 trial hit the 32,768-token response limit. `ExecuteToolTurn` now preserves incomplete provider details and observed reasoning before propagating the terminal error. `SDKToolRequestAdapter` retains the response body for Chat length, Responses incomplete and Anthropic max_tokens responses. No partial tool is returned or executed; no completion event or automatic retry is permitted. Protocol tests assert single usage settlement and all three response forms. This cannot recover the response discarded by the older code during that historical trial.

The local report is `http://127.0.0.1:8782/agent-evidence-resolution/`. It retains the earlier report as a baseline and separates saved-call replay, diagnostic live trials and final tool recovery. Live trials use only the upstream-verified DeepSeek-V4.1-Flash. They re-review frozen historical candidates; they do not regenerate or repair training records. Model disagreement is not a confirmed defect, and admission is not semantic correctness.

The original source fixture and its report SHA-256 remain unchanged. Model requests, replies, provider reasoning, tool traces, tokens and regression XML stay in the local evidence directory. They are not training exports or repository secrets.

### Completed checks

- Final main suite: 10,424 passed, zero failures, 51 pre-existing environment/platform skips. Its source-file snapshot matches the delivered code.
- Console interaction suite: 227 passed, zero failures or skips.
- The existing isolated runtime at `F:/docling-local-validation/modern-venv` has Docling 2.140.0 and Docling-core 2.102.0. Its six relevant parser suites passed all 106 tests. These cover all 50 Docling-related skipped test/module entries from the main environment. The remaining skip is a POSIX signal test; Windows termination already forcibly stops the process.
- Regression processes ran sequentially with external socket connections blocked. No provider was called by these tests.
- The bounded version 5 live calculator recovery used two V4.1-Flash turns. The first retained a count/proportion input error. The second corrected that entry after reading tool feedback. Four conditional arithmetic receipts were retained, with no candidate edits or sample-level approval. This is a tool-recovery check, not a new whole-sample pass-rate measurement.
- Earlier live trials retain a genuine ratio-direction finding, a semantic reviewer disagreement, and an output-limit failure. They are labelled by their actual runtime revision; none is relabelled as a successful version 5 review.
