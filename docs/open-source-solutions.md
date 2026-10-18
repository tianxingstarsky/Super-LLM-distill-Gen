# Open-source solutions and adoption status

Reviewed on 2026-10-19. This is an engineering decision log, not a claim that a
library or a paper guarantees correct training data. No paid model requests,
model downloads, or new research benchmark runs were performed for this change.

## Long-running agents

| Source | Useful mechanism | Current adoption |
| --- | --- | --- |
| [MemGPT paper](https://arxiv.org/abs/2310.08560) and [Letta](https://github.com/letta-ai/letta) | Keep a small active context and retrieve older memory through tools. Move information between tiers rather than appending all history to every request. | The local director adopts this architecture through a persistent, searchable archive and bounded retrieval. It does not install or wrap the Letta runtime. |
| [A-MEM paper](https://arxiv.org/abs/2502.12110), [paper reproduction](https://github.com/WujiangXu/A-mem), and [agent implementation](https://github.com/WujiangXu/A-mem-sys) | Structured notes and links can connect related experience across sessions. | Research candidate. No automatic LLM-written memory graph is enabled. An unverified generated summary must not become a source fact. |
| [LongMemEval paper](https://arxiv.org/abs/2410.10813) and [benchmark](https://github.com/xiaowu0162/LongMemEval) | Test retrieval across sessions, updates, temporal reasoning, and appropriate abstention. | Evaluation design reference. Local regression tests exercise archive integrity, source scope, stale results, and retrieval limits. They are not a LongMemEval score or a reproduction of the paper. |

The new archive remains local. A recalled result is a record of earlier work,
not proof that its answer was correct. Tool results carry identifiers and
provenance so the director can recover the original material. Only retrieved
items enter the active context. The role boundaries for the question actor,
answer actor, and reviewer remain separate.

Fresh persistent-context runs now keep up to 12 complete recent operations,
within a dynamic history allowance capped at 64 KiB. Older public messages
remain searchable in a local SQLite FTS archive. A large latest operation can
no longer hide every older plan. The archive excludes provider-private reasoning,
encrypted state, tool calls, and tool-result bodies; the original records remain
unchanged. Retrieval is bound to the current agent session and verifies the
stored source hash. It does not provide cross-project or semantic memory.

The archive tools use the existing bounded tool loop. There is no background
summarizer, embedding request, or extra reviewer. Searches use local lexical
matching and can miss paraphrases. The model can still make extra tool rounds
when it chooses to recall history. Input checkpoints preserve the exact recalled
snapshot on replay. Older frozen recipes keep their original behavior; create a
new task to use the new archive policy. Public LongMemEval results and real
generation quality remain unmeasured for this implementation.

The official Letta repository currently points to
[letta-code](https://github.com/letta-ai/letta-code) as its active source. Its
retired V1 API server is on an archive branch. A future full integration must
select and pin a concrete runtime; it must not assume that old Python examples
describe the current server. The repositories list Apache-2.0 for Letta and MIT
for A-MEM. Benchmark code and benchmark data require separate license checks
before redistribution.

## Document parsing: adopted as an optional backend

[Docling](https://github.com/docling-project/docling) provides a structured
document representation, PDF layout analysis, tables, and optional OCR. Its
[technical report](https://arxiv.org/abs/2408.09869) describes the underlying
document conversion approach. The code is MIT licensed; each model has its own
license. The new adapter uses the published
[2.60.1 API](https://github.com/docling-project/docling/tree/v2.60.1), with
`docling-core==2.48.4`. These versions are an explicit compatibility baseline,
not a statement that they are the latest release.

Select **Docling 本地版面** in the existing input node. Provide a local artifact
directory. PDF pages are processed by Docling; TXT, Markdown, TeX, LaTeX, and
DOCX continue to use the existing local parsers. JSON and JSONL keep their
existing import semantics. Standalone images still require the explicit
multimodal input mode. This first adapter does not add a new model service page.

The base installation does not include Docling. Install the optional environment
explicitly if this backend is needed:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docling.txt
```

Provision local model artifacts separately using the upstream
[offline model setup instructions](https://docling-project.github.io/docling/usage/advanced_options/).
The configured root must contain the following model directories:

```text
models/
  ds4sd--docling-layout-heron/
  ds4sd--docling-models/model_artifacts/tableformer/accurate/
```

For scanned PDFs, enable local OCR and supply an EasyOCR model directory and
language codes such as `ch_sim,en`. That directory must already contain the
corresponding detection and recognition weights. OCR downloads are explicitly
disabled. Remote services, external plugins, picture-description models,
picture-classification models, code enrichment, and formula enrichment are also
disabled. Consequently this adapter does not claim full diagram understanding
or specialized formula OCR. Existing multimodal review remains useful for such
material.

For programmatic workflow creation, the same node configuration is accepted:

```python
document_parser = {
    "mode": "docling",
    "artifacts_path": r"F:\models\docling",
    "ocr": True,
    "ocr_models_path": r"F:\models\easyocr",
    "ocr_languages": ["ch_sim", "en"],
}
# Pass document_parser=document_parser to create_run(...).
```

There is no automatic download, installation, or fallback to a different
parser. Missing dependencies or missing local assets produce an explicit setup
error. A failed or partial conversion is not published as a complete cache
entry. Empty results for a nonempty page are quarantined. Scanned pages without
OCR are marked as requiring visual reading, even if the parser emits a fragment.

The adapter uses the existing persistent source library. Cache identity includes
the source, backend, adapter version, dependency versions, model-content hashes,
OCR settings, and language selection. Original PDF page numbers, source hashes,
element references, and bounding-box provenance survive into generated records.
Cache reuse across runs does not depend on the requested sample count.

Within one worker, model hashes are memoized by resolved path, size, mtime,
ctime, and inode. A changed file is rehashed; a new worker hashes again. This
avoids repeatedly reading large weights for every PDF. Converters are also
reused by configuration and signature within a worker, with a bounded cache
and serialized access. The PDF preflight reads page counts and stream syntax;
it does not run native text extraction or decode page images first. It follows the existing
source-library change detection model; it is not a defense against adversarial
filesystem metadata forgery. Submitted runs reject changed parser signatures
instead of silently changing their interpretation of source documents.

Verification in this change uses genuine small PDF fixtures and a fake
Docling-shaped conversion result. It checks page provenance, partial failures,
scanned-page handling, cache reuse, changed weights, native TeX preservation,
and UI persistence. Docling and its weights were not present on the development
machine, so real layout/OCR quality has not been measured.

## Remaining candidates

| Priority | Project and license | Concrete next step | Boundary |
| --- | --- | --- | --- |
| High | [DataTrove](https://github.com/huggingface/datatrove), Apache-2.0; [FineWeb paper](https://arxiv.org/abs/2406.17557) | Compare its sharded MinHash and sentence deduplication with the current disk-backed corpus index on a frozen multilingual set. | Not integrated. MinHash is approximate lexical retrieval, not a guarantee of semantic uniqueness. Preserve numeric, formula, and code differences; measure false deletions. |
| Medium | [RAGChecker](https://github.com/amazon-science/RAGChecker), Apache-2.0; [paper](https://arxiv.org/abs/2408.08067) | Add an optional diagnostic benchmark for retrieval recall, evidence use, noise sensitivity, and unsupported claims. | Not integrated. It depends on references and model judgments. Claim entailment alone does not prove a multi-step explanation valid. It must not override unresolved review results. |
| Server edition | [DBOS Python](https://github.com/dbos-inc/dbos-transact-py), MIT | Evaluate durable queues and workflow recovery on the server deployment path. | Not integrated. Its documented main path adds PostgreSQL. A completed external API request whose result was not recorded can still be retried; durable execution does not make a provider call exactly-once. |

The project already has distilabel, optional Langfuse monitoring, local and
Qdrant knowledge access, cross-process per-model concurrency control, and
integrity-checked SQLite result checkpoints. Adding another framework should
replace a measured weakness, not duplicate these facilities.

Before changing the default backend, use fixed fixtures and report correctness,
missed duplicates, false removals, peak memory, recovery behavior, and cost.
Keep original outputs and unresolved errors so that a new evaluator cannot
erase earlier failures by changing the metric.
