# ShuJian Cube v0.0.1

Initial public preview for Windows x64.

## Install

Download `ShuJian-Cube-0.0.1-Windows-x64-Setup.exe` from this release.
The installer includes a separate Python runtime. A system Python installation
is not required. The first setup needs an internet connection to download the
locked application packages. Allow about 2 GB of free space for the app,
private environment, and download cache.

Use the desktop shortcut or the browser shortcut to open the same local workbench.
The desktop window also needs Microsoft Edge WebView2. The browser shortcut is
available when the desktop window cannot be used.

Documents, saved tasks, model settings, and request receipts stay on the computer.
The download contains no API keys, personal histories, or pretrained model weights.
Local Docling models and legacy OpenCUA command-line tools are not included.

## In this preview

- Create training examples from documents, DSH conversations, or an open brief.
- Configure models and prompts on workflow nodes.
- Follow streamed output and resume saved tasks.
- Keep native thinking and the answer from the same response.
- Save personal style editions and propose lessons from DSH histories.
- Inspect samples and export training files.

New native CoT tasks also carry a small program-generated execution state. It
identifies the user turn, source scope, unverified assistant history, editable old
drafts, and available tools. It adds no model request and no fixed thinking steps.
Older tasks retain their frozen behavior. Internal state is kept out of training
message fields. Complete native thinking is still preserved.

The design borrows context and tool-state mechanisms from Codex, OpenCode, and
Google ADK. It does not bundle those agent runtimes or require switching models.
See [the design and sources](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.1/docs/architecture/codex-harness-adoption-2026-10-29.md).

## Quality limits

This is a preview, not a guarantee of unattended million-example production.
The latest local ten-scenario experiment yielded six usable complete records
after one style update. This was a known-batch observation, not an independent
benchmark. The new execution state does not establish a higher semantic pass rate.

Unreviewed results remain unreviewed. Proposed experience rules need inspection
before adoption. Automatic production feedback collection and continuous
experience updates are not yet a complete loop. Reading conversations still need
further work on long-context management.

The installer is not code signed. Verify the downloaded file with the SHA256
checksums attached to this release.

## Verification

The affected native-node, DDD, request, and reading-session regressions passed
all 52 checks. One new V4.1-Flash business request also completed in 10.18 seconds.
It carried the expected third-user-turn state, unverified history, and editable
draft status. Complete native thinking and the answer were preserved. No separate
judge was called. Local checks accepted the record; this does not certify semantic
quality. Direct inspection found no claimed improvement before measurement, but
also found extra background and limited bilingual interpretation. This single
known case is not a pass-rate comparison.

The Windows installer was run in a separate folder with spaces and Chinese
characters. Its private Python 3.11.9 environment and 127 locked packages loaded.
All 4,546 immutable payload files passed their hash checks. Installation made no
model API calls. A real browser session checked the home page, workflow canvas,
node settings, empty task history, sidebar collapse and reopen, and English
switching in Settings. The normal desktop shortcut also opened the installed
workbench in its native window.

Offline dependency failure returned an error and wrote no success receipt.
Setup also rejected an unrelated Python environment without changing it.
Repeated setup completed with a new verification receipt. It preserved edited
settings, sample files, saved work, and the existing private environment.
Files with the expected hash do not need another copy. Changed files still
require replacement and the complete installed-file check. A changed launcher
and screenshot were restored to their exact release hashes by setup.
Earlier core, interface, and parser checks are documented in the
[27 October readiness record](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.1/docs/release-readiness-2026-10-27.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.1/LICENSE),
[NOTICE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.1/NOTICE), and
[third-party licenses](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.1/docs/licenses/README.md).
