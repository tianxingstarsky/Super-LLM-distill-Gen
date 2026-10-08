# ShuJian Cube Product Tour

Bring your sources. Choose your data goals. Follow each stage and review the results.

## Home

Start with documents, agent context, or an open brief. MD and TXT files have direct import shortcuts. You can also choose **Create image Q&A**.

All nine training goals have visible shortcuts:

- **CPT** for domain text.
- **SFT** for instructions and answers.
- **DPO** and **ORPO** for preference pairs.
- **RLAIF** for AI feedback.
- **Agent** for verified traces.
- **Multi-turn** for conversations.
- **CoT** for visible reasoning examples.
- **GSM8K** for arithmetic examples in that format.

Choose a shortcut to open its goal in the workbench. Recent work helps you return to earlier tasks.

## Create data

The workbench has two modes: automatic generation and manual image Q&A.

For automatic generation, import documents or agent context. You can also write an open brief. Document imports accept MD, TXT, PDF, and DOCX files. Imported files remain in the local library.

Select one training goal or combine several. Choose the candidate count and batch size. The workflow shows the stages for your selection.

## Configure a node

Select a workflow node to choose its model. Set its context window and maximum output tokens there. Adjust these limits to fit the selected model.

Each node keeps its own choices. Once a run starts, that run uses its saved configuration.

## Follow live work

Open a running node to watch its response arrive as a token stream. Read its status, recent events, and available sample previews.

Larger jobs run in batches. Completed items have saved checkpoints. If a run fails, open it from **Work Manager** and retry from those checkpoints. Saved drafts and run history remain available after you reopen the app.

## Make image Q&A

Choose **Create image Q&A** in the workbench. Create a collection or open an existing one. Add PNG, JPEG, or WEBP images. Write a question and a reference answer.

Check the preview, then save the sample. Continue with the next one. No model connection is needed for this mode.

Find these collections under **Data Library → Manual datasets**. Export a ZIP with the questions, answers, and original images. Manual samples are marked as unreviewed.

## Review examples

**Human Review** has areas for CPT, SFT, DPO, ORPO, and RLAIF. Read the source and result. Check the example, make changes where needed, and record your decision.

Automatic checks help find problems. They do not replace human review.

## Export a collection

**Release Packages** shows available collections and their files. Inspect the contents before downloading. Candidate exports and reviewed releases have different review states. Check that state before training or sharing.

## Browse your library

**Data Library** brings source files, sample previews, manual collections, and quality reports together. Search your files and inspect an item before using it.

## Settings

Choose English or Chinese in **Settings**. You can also adjust generation preferences.

The advanced connection section maintains shared model connections and budget settings. Choose the model for a task inside its workflow nodes.
