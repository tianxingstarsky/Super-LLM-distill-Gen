# ShuJian Cube v0.0.2

A community preview for Windows x64.

## Install or upgrade

Download `ShuJian-Cube-0.0.2-Windows-x64-Setup.exe` from this release.
For an upgrade, close the installed app and choose its existing folder.
Saved tasks, local model settings, and user data are kept.
The first setup needs an internet connection. A system Python installation is not required.
The desktop window needs Microsoft Edge WebView2. The browser shortcut offers another way to open the app.

The installer remains unsigned. Compare its hash with `SHA256SUMS.txt` before use.
The release also includes a source and dependency manifest, plus installation validation results.
Optional Docling models and external agent hosts are installed separately.

## Changes

- A clearer desktop workbench and floating node settings.
- Independent DeepSeek Harness and Codex setup guides.
- Better continuation of user language in reading conversations.
- An explicit choice for imported conversations with missing reasoning: keep, supplement, or exclude affected records.
- Editable hint extraction and visible CoT reconstruction templates on the input node.
- Separate handling of tool decisions and final answers during reconstruction.
- Preserved source messages, native reasoning, request receipts, and frozen task settings.
- Feature-owned Agent prompt modules and a maintenance map for contributors.

## Missing-reasoning reconstruction: work in progress

This feature is experimental. It creates a synthetic explanation. It cannot recover the original internal reasoning.
It first extracts hints from the saved answer. A separate request uses those hints and the earlier conversation to write an explanation.
The program stores that visible text in the reasoning field. The model's native reasoning stays in the request evidence.

A completed reconstruction is not a quality approval. The saved answer may be wrong. A new explanation may disagree with it.
In one local DSH trial, V4.1-Flash completed both missing explanations. The final explanation contradicted two claims in the saved answer.
That record was not accepted as a training example. The run was configured to require manual review and exported no training records.
The contradiction was found during direct inspection. It was not an automatic rejection by the product.

The source conversation had thinking disabled to test missing reasoning. This observation does not establish why the answer was wrong.
We have not established a general success rate. This release makes no new claim about unattended million-example quality.
Existing reasoning is kept. Users choose whether to supplement missing reasoning.

## Help improve this with us

We welcome community work on faithful reconstruction, answer repair, and efficient consistency checks.
Useful reports include a small reproducible example, the observed failure, and a proposed change.
Check correct answers and known-wrong answers separately. Keep original evidence and report real request costs.
Please do not count format checks or completed requests as semantic success.

Start with the [Agent feature map](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.2/docs/architecture/agent-features.md).
Use [GitHub Issues](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/issues) for reproducible findings and proposals.
DeepSeek Harness and Codex integrations remain independent of the core application.

## Verification scope

Validation covers changed prompts, import policies, provenance, architecture boundaries, and affected desktop controls.
Prompt IDs, versions, and text are compared before and after the code reorganization.
The release installer is built from a fixed commit and tested in an isolated Windows folder.
See the attached `validation.json` for actual checks and installation results.
No new paid model experiment is part of this release preparation.

## License

Apache License 2.0. See [LICENSE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.2/LICENSE), [NOTICE](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.2/NOTICE), and [third-party licenses](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/blob/v0.0.2/docs/licenses/README.md).
