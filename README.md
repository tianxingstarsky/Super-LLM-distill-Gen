<p align="center">
  <img src="assets/showcase/hero.png" width="1200" alt="ShuJian Cube — make training data simpler to create" />
</p>

<h1 align="center">ShuJian Cube</h1>

<p align="center"><strong>Your knowledge. A visible workflow. Better training examples.</strong></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-2563eb?style=flat-square" alt="Apache License 2.0" /></a>
  <img src="https://img.shields.io/badge/Training_goals-9-0891b2?style=flat-square" alt="Nine training goals" />
  <img src="https://img.shields.io/badge/Interface-English_%26_Chinese-7c3aed?style=flat-square" alt="English and Chinese interface" />
</p>

<p align="center">
  <a href="#meet-the-workbench">Workbench</a> ·
  <a href="#follow-the-work">Workflow</a> ·
  <a href="#choose-your-data">Data types</a> ·
  <a href="#make-image-examples-by-hand">Image Q&amp;A</a> ·
  <a href="#start-in-a-few-steps">Get started</a>
</p>

ShuJian Cube helps you turn documents, agent conversations, and open briefs into training data. Choose a goal. Follow the work. Review the results. Return to your saved tasks whenever you need them.

## Meet the workbench

One place to start, follow, and refine your work. The home screen brings new tasks and recent activity together. The interface is available in English and Chinese.

<p align="center">
  <img src="screenshots/homepage.jpg" width="1200" alt="The ShuJian Cube home screen with creation shortcuts and recent work" />
</p>

| Bring your own sources | Stay close to the process | Build at your own pace |
| --- | --- | --- |
| Import MD, TXT, PDF, or DOCX files. Add agent context or describe a task. | Select a node to set its model and token limits. Open it to watch live output. | Generate in batches. Keep saved results. Return to local work history. |

<p align="center">
  <img src="assets/showcase/capabilities.png" width="1200" alt="Flexible sources, visible generation, human-created image examples, and saved work" />
</p>

## Follow the work

See the path from source material to a finished collection. Each stage has a clear purpose. Status and sample previews help you spot work that needs attention.

<p align="center">
  <img src="assets/showcase/workflow-map.svg" width="1200" alt="Product workflow: add sources, choose goals, configure nodes, generate, review, and export" />
</p>

Open a model node to watch its response arrive as a token stream. For larger jobs, choose a batch size and follow progress. If a run fails, retry from saved checkpoints. Completed results remain available.

<p align="center">
  <img src="screenshots/workflow.jpg" width="1200" alt="The actual workflow canvas with a selected node and its model settings" />
</p>

## Choose your data

Select one goal or combine several. Create CPT text, SFT instructions, DPO or ORPO preference pairs, and RLAIF feedback. You can also create agent traces, multi-turn conversations, visible CoT examples, and GSM8K-style arithmetic examples.

<p align="center">
  <img src="assets/showcase/data-goals.svg" width="1200" alt="Nine supported goals: CPT, SFT, DPO, ORPO, RLAIF, Agent, Multi-turn, CoT, and GSM8K" />
</p>

## Make image examples by hand

Some examples need a human touch. Add PNG, JPEG, or WEBP images. Write a question and a reference answer. Preview the sample, save it, and continue with the next one.

Find saved samples in the data library. Export a ZIP with the questions, answers, and original images. Manual samples are marked as unreviewed. Check them before use.

<p align="center">
  <img src="screenshots/manual-image-qa.jpg" width="1200" alt="The image Q&A editor with a real image preview, question, answer, and save action" />
</p>

## Start in a few steps

On Windows, open [the launcher](scripts/start_all.vbs). Then:

1. Add files, agent context, or a short brief.
2. Choose your data goals and configure the model nodes.
3. Start generation and open a node to follow its output.
4. Review your samples, then export the collection.

For image Q&A, choose **Create image Q&A** instead. No model connection is needed to write these samples.

## Responsible use

Generated data can be wrong. Review accuracy, safety, and privacy before training or sharing. Only use sources and images you have permission to process. Model services may receive the content you submit. Quality depends on your sources, model, and review.

## License and notices

ShuJian Cube is available under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for third-party acknowledgments. Screenshots show the real app with local demo files. Promotional illustrations explain the product and do not report measured results.
