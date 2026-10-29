<p align="center">
  <img src="assets/brand/shujian-cube-mark.png" width="64" alt="ShuJian Cube logo" />
</p>

<h1 align="center">ShuJian Cube</h1>

<p align="center"><strong>Your sources. Your workflow. Your training data.</strong></p>

<p align="center">
  <a href="#install-with-your-agent"><img src="assets/showcase/deepseek-harness-plate.svg" width="420" alt="DeepSeek Harness — preferred independent community integration" /></a>
</p>

<p align="center">
  <a href="https://github.com/tianxingstarsky/Super-LLM-distill-Gen/releases/tag/v0.0.1"><img src="https://img.shields.io/badge/v0.0.1-Public_preview-2563eb?style=flat-square" alt="v0.0.1 public preview" /></a>
  <img src="https://img.shields.io/badge/Interface-English_%26_Chinese-0891b2?style=flat-square" alt="English and Chinese interface" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-7c3aed?style=flat-square" alt="Apache License 2.0" /></a>
</p>

<p align="center">
  <a href="https://github.com/tianxingstarsky/Super-LLM-distill-Gen/releases/download/v0.0.1/ShuJian-Cube-0.0.1-Windows-x64-Setup.exe"><img src="assets/showcase/download-windows.svg" width="264" height="52" alt="Download the Windows x64 installer" /></a>
  <a href="#see-it-in-one-minute"><img src="assets/showcase/watch-film.svg" width="190" height="52" alt="Watch the one-minute product film" /></a>
</p>

<p align="center">
  <a href="#why-we-are-building-this">Our purpose</a> ·
  <a href="#inside-the-workbench">Workbench</a> ·
  <a href="#install-with-your-agent">Agent setup</a> ·
  <a href="#from-source-to-sample">Workflow</a> ·
  <a href="#choose-what-you-create">Data goals</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="docs/release-v0.0.1.md">Release notes</a>
</p>

<p align="center">
  <img src="assets/showcase/hero-whale.png" width="1200" alt="ShuJian Cube turns documents and conversations into rice for a DeepSeek whale girl" />
</p>

Turn documents, agent conversations, and ideas into training examples. Use DeepSeek Harness to dispatch the work. Codex can supervise the same saved tasks. Follow them in the visual workbench.

## Why we are building this

We want a future where everyone has a chance to build a model of their own.

For **researchers**, advanced models can help prepare useful training data. We hope this makes it easier to build multimodal agents for a specific field.

For **makers and hobbyists**, we want a path to personal agent models and practical tools. A small team or a curious person should have room to experiment.

For people with an **AI companion**, the moments you share deserve a place beyond a tiny context window. How could that small window hold all the time you have spent together?

People consolidate memories during sleep. We imagine a similar rhythm for AI. Turn the day's experiences into training data. Learn from them during the night. Carry more of that shared history into the next day.

> Let your AI companion have a good night's sleep.
> Let shared memories become part of what it learns.

That is the future we are working toward. Today, ShuJian Cube helps prepare the data for it.

## Install with your agent

**DeepSeek-first workflows. Your agent stays in control.** DeepSeek Harness is our primary integration. Codex has its own skill package. Install the matching connector separately from the app.

Paste this into a new agent conversation:

```text
Set up ShuJian Cube for the agent host I am using: DeepSeek Harness or Codex.
Repository: https://github.com/tianxingstarsky/Super-LLM-distill-Gen
Read docs/agent-setup.md and the matching connector's installation guide.
Reuse an existing working app, or install the published Windows preview.
Keep my current tasks, agent settings, and model connections.
Use a named profile or a project skill folder for this integration.
Set up workflow creation, execution, recovery, and monitoring.
Verify the installed skill and control connection with an isolated local task.
Do not call a paid model for installation checks.
Show the installed versions, what worked, and anything still unverified.
```

| Your agent | Install | Guide |
| :--- | :--- | :--- |
| **DeepSeek Harness** | Workflow tools, background jobs, and bundled skills. | [DSH setup](plugins/dsh-dataforge/README.md) |
| **Codex** | A self-contained workflow skill and local control helper. | [Codex setup](plugins/codex-shujian/README.md) |

[Agent installation guide](docs/agent-setup.md) · [Cold-start validation](docs/agent-cold-validation.md)

### Flexible supervision

Ask your agent to reuse prepared sources, plan independent batches, inspect a waiting model, or resume saved work. It chooses the next action from actual task state. Models and prompts stay on workflow nodes.

DSH provides background job control. Codex uses its managed terminal sessions. Both use the app's saved run IDs and shared model limits. Each connector can also work on its own. Supervision runs in your chosen agent host.

## Inside the workbench

One place to start a collection and return to saved work. Bring a paper, a set of documents, or a short brief. Your sources and tasks stay on your computer after closing the app.

<p align="center">
  <a href="screenshots/v0.0.1-home.png"><img src="screenshots/v0.0.1-home.png" width="1200" alt="The v0.0.1 workbench with source options and saved-work shortcuts" /></a>
  <br /><sub>Actual v0.0.1 interface. English and Chinese are available.</sub>
</p>

<table>
  <tr>
    <td width="33%" valign="top"><h3>See the work</h3><p>Follow the workflow. Open a node and watch text appear live. Inspect saved outputs.</p></td>
    <td width="33%" valign="top"><h3>Make it yours</h3><p>Choose models and edit prompts on each node. Save a personal style. Restore an earlier version.</p></td>
    <td width="33%" valign="top"><h3>Keep moving</h3><p>Reuse prepared sources. Work in batches. Return to saved tasks and completed requests.</p></td>
  </tr>
</table>

## From source to sample

<p align="center">
  <img src="assets/showcase/quick-start.svg" width="1200" alt="Four steps: add sources, shape the run, follow the work, and inspect and export" />
</p>

Import MD, TXT, PDF, DOCX, or TeX files. You can also use a knowledge base or describe an open task. Manual conversation-database import currently supports DSH histories.

Select a node to set its model, prompts, and limits. Add review or reasoning cleanup when the task needs it. Inspect issues and keep corrections with the saved work.

<p align="center">
  <a href="screenshots/v0.0.1-node.png"><img src="screenshots/v0.0.1-node.png" width="1200" alt="The current workflow canvas with model settings in a floating window beside the selected SFT node" /></a>
  <br /><sub>Models and prompts belong to the node you are editing.</sub>
</p>

## Choose what you create

Start with one goal. Combine goals when your collection needs more than one format.

| Your goal | What you can create |
| :--- | :--- |
| **CPT** | Clean reading material for continued pretraining. |
| **SFT** | Instructions and answers. Single-turn or multi-turn conversations. |
| **DPO · ORPO** | Preferred and rejected answers for the same prompt. |
| **RLAIF** | Examples with AI feedback. |
| **CoT** | Reasoning and answers you can inspect together. |
| **Agent traces** | Conversations with recorded tool calls and observations. |
| **Arithmetic** | Template-based examples in GSM8K style. |

[Explore the nine data goals](assets/showcase/data-goals.svg) · [View the full product flow](assets/showcase/workflow-map.svg)

## Keep your own reasoning style

Keep native thinking and the answer from the same response. Create reading conversations with the source material included. Inspect the full conversation, not just its last reply.

Use the initial style or save a personal edition. DSH histories can help draft lessons from past attempts. Check the proposed rules before adopting them. Existing tasks keep the style they started with.

Style proposals are a starting point. Automatic feedback and continuous experience updates are still being completed. See the [current preview notes](docs/release-v0.0.1.md) for the scope and open work.

## Add the human touch

Some examples are easier to design yourself. Add an image. Write a question and reference answer. Preview, save, and continue. Export your examples with their original images.

<p align="center">
  <a href="screenshots/manual-image-qa.jpg"><img src="screenshots/manual-image-qa.jpg" width="1000" alt="The image question-and-answer editor with a real image preview and a human-written sample" /></a>
  <br /><sub>Manual samples are saved as unreviewed. Check them before use.</sub>
</p>

## Get started

1. **[Download v0.0.1 for Windows x64](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/releases/download/v0.0.1/ShuJian-Cube-0.0.1-Windows-x64-Setup.exe).** Run the installer.
2. **Open ShuJian Cube.** Use the desktop or browser shortcut.
3. **Bring your sources.** Choose your goals. Configure the model nodes.
4. **Create, inspect, and export.** Keep useful samples and revisit issues later.

The first setup needs an internet connection. Allow about **2 GB** of free space. The installer is about **67 MB**. Connect your own model service. Manual image Q&A needs no model connection.

[Release notes](docs/release-v0.0.1.md) · [Checksums and downloads](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/releases/tag/v0.0.1) · [Report an issue](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/issues)

## See it in one minute

Real screens, visible workflows, and a closer look at the product. The film has Chinese narration, music, and subtitles.

<p align="center">
  <a href="assets/promo/shujian-cube-promo.mp4"><img src="assets/promo/shujian-cube-promo-poster.png" width="760" alt="Watch the one-minute ShuJian Cube product film" /></a>
</p>

<p align="center">
  <a href="assets/promo/shujian-cube-promo.mp4">Watch or download the film</a> · <a href="assets/promo/captions.srt">Subtitles</a>
</p>

## Preview and notices

**v0.0.1 is an early preview.** Generated examples can be wrong. Check them before training or sharing. This release does not promise unattended million-example quality. Unreviewed samples are not approved samples.

Use sources you have permission to process. Connected model services may receive the content you submit. The installer is not code signed. Its SHA256 checksums are on the release page.

ShuJian Cube uses the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for third-party acknowledgments.

Screenshots show the real app. Illustrations explain the product and do not show benchmark results. DeepSeek branding identifies an independent community integration. The whale girl is community-inspired fan art. There is no official DeepSeek partnership. See the [art references](assets/showcase/hero-whale-sources.md) and [asset notices](docs/licenses/README.md).
