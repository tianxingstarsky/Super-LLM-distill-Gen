# ShuJian Cube Product Tour

Bring your sources. Choose your data goals. Follow each stage and review the results.

## Home

Start with documents, agent context, or an open brief. The document entry accepts MD, TXT, PDF, and DOCX. You can also choose **Create image Q&A**.

Knowledge sources are available in the generation workspace. Build a persistent local index from your files, or connect an existing Qdrant collection in the Input node. Search a topic and inspect the matched passages before starting. Local search uses full-text ranking. Qdrant search uses the collection's embedding model and vector dimensions. The task keeps a snapshot of the retrieved text and its source evidence.

The Input node also offers multimodal document reading. Confirm image support for the exact model before enabling it. PDF pages and images are read by that model. DOCX text is kept alongside readings of embedded images. You can follow the model output inside the running node. Extracted content still needs review.

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

Select **Parse input** to preview a document before running. Choose **Preview parsing and chunks**. Check the real text, character count, and chunk count. Move between chunks by number. This preview uses no model calls.

If you change the document or chunk size, preview it again. Preview size limits do not change the run's input scope.

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

Open a review queue directly from a completed task or its sample preview. The app keeps the selected task and data type. Agent traces open in the task's review area.

## Export a collection

**Release Packages** shows available collections and their files. Inspect the contents before downloading. Candidate exports and reviewed releases have different review states. Check that state before training or sharing.

## Browse your library

**Data Library** brings source files, sample previews, manual collections, and quality reports together. Search your files and inspect an item before using it.

Choose **Use for generation** on a source file to add it to your current draft. Your goals and node model choices stay in place. **Import sources** opens the workbench's upload area.

## Settings

Choose English or Chinese in **Settings**. You can also adjust generation preferences.

The advanced connection section maintains shared model connections and budget settings. Choose the model for a task inside its workflow nodes.
