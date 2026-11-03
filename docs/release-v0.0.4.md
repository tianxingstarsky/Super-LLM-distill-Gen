# ShuJian Cube v0.0.4

A community preview for Windows x64.

## Install or upgrade

Download `ShuJian-Cube-0.0.4-Windows-x64-Setup.exe` from this release.
For an upgrade, close the installed app and choose its existing folder.
Saved tasks, local model settings, and user data are kept.

This is an online installer. The first setup needs an internet connection.
A system Python installation is not required.
The desktop window needs Microsoft Edge WebView2. The browser shortcut offers another way to open the app.
Optional Docling models and external agent hosts are installed separately.
DeepSeek Harness and Codex connectors remain independent of the app.

The installer is unsigned. Compare its hash with `SHA256SUMS.txt` before use.
The release includes a build manifest and installation validation results.

## Changes

- Mix knowledge questions and document-reading questions in one workflow. Choose how often reading questions appear.
- Make local edits to recorded Agent CoT. Keep the saved final answer and all tool records unchanged.
- Keep flow suggestions separate from actions that actually happened.
- Configure the Agent editor's model and prompts on its workflow node.
- Use adopted reasoning experience in the editor's system context. Keep the chosen version with the task.
- Edit knowledge reasoning with its original context. Full reasoning rewrites are rejected in local-edit mode.
- Keep original reasoning, model responses, request records, and actual usage for inspection.

## Recorded Agent editing

The editor works from saved conversations and tool observations. It does not need to run those tools again.
By default, it edits one final assistant CoT per trajectory.
Other turns stay unchanged. An earlier turn can be selected explicitly.
That turn sees only the history available at its position.

New tool calls, changed arguments, and different execution orders remain proposals.
They are not exported as actions that were carried out.
Keeping the final answer unchanged does not prove that the answer is correct.
An editor can flag a source problem without inventing reasons to defend it.

A real V4.1-Flash trial used ten saved NVIDIA trajectories:

- Each trajectory used one editing request. No source tools were executed.
- All ten final answers and tool records stayed unchanged.
- Nine local patches were accepted. One failed exact text matching and kept its original CoT.
- Four records received source-answer doubts from the editor. Those doubts were not independently verified.
- Five final answers had no CoT. Their latest existing reasoning turns were selected instead. No missing native reasoning was invented.

These are editing and consistency results, not a quality pass rate.
See the [editing scope and recorded findings](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/docs/architecture/agent-trajectory-evolution-2026-11-03.md).

## Experience and subagent limits

The Windows installer does not bundle the Google ReasoningBank source component or its local learning dependencies.
Install those optional parts separately before using experience learning or retrieval.
Follow the [Google ReasoningBank setup guide](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/docs/architecture/google-reasoning-bank-integration.md).
Basic recorded edits can still use the initial baseline without an adopted experience edition.

Google ReasoningBank can retrieve an adopted experience edition for the editor.
The edition is fixed when the task is created.
New experience is not applied to a running task without a new task version.

The ten-trajectory trial had no adopted reasoning edition.
It used the initial baseline and did not buy an extra learning call.
It does not show that experience evolution improved the output.
Unknown outcomes remain unknown.

DSH can import a complete child conversation as a single session.
Parent-session details are kept in the import audit.
Automatic linking of parents, children, sibling agents, and parallel branches is not complete.
The editor still receives a single conversation history.
A parent's summary cannot recover a child's missing internal record.

## Missing reasoning: work in progress

Imported conversations may lack reasoning.
Choose whether to keep them, supplement missing reasoning, or exclude affected records.
Existing reasoning is retained.

A supplemented CoT is a synthetic explanation. It is not recovered native thinking.
It may disagree with the saved answer. A completed request is not a quality approval.
This preview makes no new promise about unattended million-example quality.

## Help improve this with us

We welcome work on faithful local edits, answer repair, and multi-agent trace support.
Useful reports include a small example, original evidence, the observed issue, and actual call usage.
Keep completed edits separate from verified training examples.

Start with the [Agent feature map](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/docs/architecture/agent-features.md).
Share reproducible findings through [GitHub Issues](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/issues).

## Verification scope

The release uses a fixed source revision.
The attached `validation.json` records the actual installation checks.
Installation checks do not call paid model APIs.
They do not establish the semantic quality of generated training data.

## License

Apache License 2.0. See [LICENSE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/LICENSE), [NOTICE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/NOTICE), and [third-party licenses](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.4/docs/licenses/README.md).
