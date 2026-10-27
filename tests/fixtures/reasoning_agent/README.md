Real malformed review tool calls from the 2026-10-25 V4.1-Flash pilot. Raw arguments are retained byte-for-byte, with parse diagnostics. No network is needed. Tests must reject execution, retain native reasoning, and recover using exact schema feedback. They do not repair candidate content or weaken evidence checks.

`authored_case09.json` preserves the complete 2026-10-26 failed article-reading
candidate and original source. The upstream strict tool succeeded, but the body
quoted material outside the user's excerpt and answered an English user in
Chinese. `test_authored_task_contract.py` replays these observable violations
without editing the reasoning or asking a model to rejudge it. A hash identifies
the original local record. Detecting these issues does not prove general semantic
entailment or diagnose every overstrong conclusion.

`environment_sessions.json` retains five historical review sessions: complete candidate text, supplied evidence, original tool arguments and receipts. Its source path and SHA-256 identify the original local comparison. It contains no provider request headers. `scripts/replay_reasoning_environment.py` explicitly projects the old wire fields to the new protocol; original opinions and candidate content are not rewritten. Replay is not a new model-quality experiment.
