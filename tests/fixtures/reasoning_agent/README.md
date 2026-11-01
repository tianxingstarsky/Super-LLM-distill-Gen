Real malformed review tool calls from the 2026-10-25 V4.1-Flash pilot. Raw arguments are retained byte-for-byte, with parse diagnostics. No network is needed. Tests must reject execution, retain native reasoning, and recover using exact schema feedback. They do not repair candidate content or weaken evidence checks.

`dialogue_constraint_sources.json` preserves the 2026-10-31 call 008 director
payload, raw response, native reasoning, and complete public dialogue messages.
The old state paraphrased an assistant suggestion as a user constraint. The
completed dialogue supplies the later explicit user choice for contrast. Paths
and hashes identify the originals. Offline source selection checks attribution
and chronology only; it does not certify the recorded state or regenerate it.

`authored_case09.json` preserves the complete 2026-10-26 failed article-reading
candidate and original source. The upstream strict tool succeeded, but the body
quoted material outside the user's excerpt and answered an English user in
Chinese. `test_authored_task_contract.py` replays these observable violations
without editing the reasoning or asking a model to rejudge it. A hash identifies
the original local record. Detecting these issues does not prove general semantic
entailment or diagnose every overstrong conclusion.

`environment_sessions.json` retains five historical review sessions: complete candidate text, supplied evidence, original tool arguments and receipts. Its source path and SHA-256 identify the original local comparison. It contains no provider request headers. `scripts/replay_reasoning_environment.py` explicitly projects the old wire fields to the new protocol; original opinions and candidate content are not rewritten. Replay is not a new model-quality experiment.

`adaptive_dialogue_context.json` retains the 2026-10-31 English native response
and the Chinese director request/response that confused an illustrative role's
preference with the current user. Source paths and hashes identify the records.
The English answer and native reasoning remain complete, including their known
defect. `test_dialogue_context_scope.py` uses them to inspect actual provider
requests, frozen-task behavior and lossless output storage. The Chinese state
remains a fallible model claim; replay does not certify it or measure improved
language quality. No credentials or request headers are included.

`native_instruction_scope.json` preserves the complete subsequent English reply
and native reasoning from run `a780b10600224639baf641c6cd42bff7`. Two references
describe a demo application's prompt; a separate unsupported numeric verbosity
claim describes the author's own response. The source stage SHA-256 identifies
the original record. `test_native_instruction_scope.py` checks those distinctions
and counterexamples without editing either output channel or requesting a model.
