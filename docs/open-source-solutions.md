# Open-source solutions and adoption status

Reviewed on 2026-10-20. This is an engineering decision log, not a claim that a
library or a paper guarantees correct training data. The initial adoption used
offline fixtures. Local model provisioning and real-PDF checks are separate
from public research benchmark results; no model API is needed for Docling.

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
license. The original in-process adapter uses the published
[2.60.1 API](https://github.com/docling-project/docling/tree/v2.60.1), with
`docling-core==2.48.4`. These versions are an explicit compatibility baseline,
not a statement that they are the latest release.

The in-process adapter explicitly uses Docling's official `PyPdfiumDocumentBackend` on
every platform, as supported by the upstream
[custom conversion example](https://docling-project.github.io/docling/_generated/examples/custom_convert/).
The default docling-parse backend failed to find its glyph assets under the
Windows installation's Chinese path even though the files existed. PDFium
handles the PDF text and raster input; Docling's layout, table and optional
formula models still perform their usual work. The backend name is part of the
parse signature. This is an explicit backend choice, never an error-triggered
fallback or a modification of installed package files.

For better native word alignment, an optional isolated worker now uses
[Docling 2.140.0](https://github.com/docling-project/docling/tree/v2.140.0),
`docling-core==2.102.0`, `docling-parse==7.22.3`, and the default TableFormer V1.
Its native parser is installed under an ASCII Windows path. The application
does not upgrade its own environment or change existing saved workflows.
The worker option and its formula engine are documented below.

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
disabled by default. Formula recognition can now be enabled separately in the
same input node. It uses local CodeFormulaV2, never a cloud endpoint. Old node
configurations keep this option off. Neither setting guarantees complete
diagram or formula understanding.

For formula recognition, also prepare `ds4sd--CodeFormulaV2/` under the configured
artifact root. The official model moved from `ds4sd/CodeFormulaV2` to
[docling-project/CodeFormulaV2](https://huggingface.co/docling-project/CodeFormulaV2),
but Docling 2.60.1 still requires the old local directory name. A reviewed model
revision is `ecedbe111d15c2dc60bfd4a823cbe80127b58af4` (about 640 MB including the
tokenizer and processors). Its model license is CDLA-Permissive-2.0, separate
from Docling's code license. Keep the complete inference assets together;
preflight checks reject missing weights, processor files, or tokenizer files.
Parsing uses the absolute local directory, with remote services and plugins
disabled. For a strictly offline verification process, also set
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before launching it.

With formula recognition off, a detected formula can become
`<!-- formula-not-decoded -->` in Markdown. This is the pinned upstream
[reading-order behavior](https://github.com/docling-project/docling/blob/v2.60.1/docling/models/readingorder_model.py)
and [serialization behavior](https://github.com/docling-project/docling-core/blob/v2.48.4/docling_core/transforms/serializer/markdown.py).
When enabled, the local model reads detected formula regions and emits LaTeX.
Missed regions, inline formulas, unusual symbols, and incorrect crops still
require comparison with the PDF. A conversion status of `success` does not
prove mathematical accuracy. Inspect formula `orig`, `text`, and page
provenance in the full Docling document, as well as undecoded Markdown markers.
The adapter quarantines pages with an undecoded formula marker or an empty
formula item. The original extracted text, formula references, and page
provenance remain available for review. It never fills a formula from guesswork.
Nonempty generated LaTeX is not marked as mathematically verified.

For programmatic workflow creation, the same node configuration is accepted:

```python
document_parser = {
    "mode": "docling",
    "artifacts_path": r"F:\models\docling",
    "formula_enrichment": True,  # Requires local CodeFormulaV2 assets.
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
OCR settings, language selection, and the formula-recognition setting. When
formula recognition is enabled, its weights and Transformers/Accelerate
versions also participate in cache identity. Original PDF page numbers, source hashes,
element references, and bounding-box provenance survive into generated records.
Cache reuse across runs does not depend on the requested sample count.

The pinned upstream Markdown serializer assigns an entire merged paragraph to
its first provenance page. The adapter instead makes a non-mutating page view
and slices cross-page text by its original `provenance.charspan` ranges. Item
references, parent/child links and table structures retain their original
identities. Per-page metadata retains the original spans and lists split item
references. Invalid, overlapping or incomplete spans stop parsing rather than
guessing a page. Cross-page enriched formulas and non-text structures also
require review because original character spans cannot safely split them.
The pagination policy is part of the cache signature, preventing reuse of
earlier outputs with text assigned to the wrong page.

Within one worker, model hashes are memoized by resolved path, size, mtime,
ctime, and inode. A changed file is rehashed; a new worker hashes again. This
avoids repeatedly reading large weights for every PDF. Converters are also
reused by configuration and signature within a worker, with a bounded cache
and serialized access. The PDF preflight reads page counts and stream syntax;
it does not run native text extraction or decode page images first. It follows the existing
source-library change detection model; it is not a defense against adversarial
filesystem metadata forgery. Submitted runs reject changed parser signatures
instead of silently changing their interpretation of source documents.

Initial adapter verification used genuine small PDF fixtures and a fake
Docling-shaped conversion result. It checks page provenance, partial failures,
scanned-page handling, cache reuse, changed weights, native TeX preservation,
and UI persistence. These regressions do not measure real layout/OCR quality.
Real-PDF acceptance must report which formulas, table cells and page references
were compared with the source, and retain any unresolved extraction defects.

### Initial local paper validation (2026-10-19)

The installed adapter was exercised on all 15 pages of
[Attention Is All You Need, arXiv v7](https://arxiv.org/abs/1706.03762v7).
The PDF SHA-256 was
`bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`.
The runs used CPU inference, local weights and a Python audit hook that blocked
socket connections. No model API was called. Baseline extraction took 34.516 s;
formula enrichment with corrected page assignment took 70.047 s. These are
single-document timings, not throughput guarantees.

- All 15 physical pages were retained. Eight fragments from four cross-page
  paragraphs were checked against their original character spans. All eight
  belonged to the correct page after the pagination fix. Continuation text and
  floating tables can still appear in a different within-page reading order.
- Five displayed formula regions became nonempty. Three retained the checked
  mathematical structure. Two still had identifier errors, including
  `MultiHead` becoming `MutiHead` and corrupted learning-rate variable names.
  Inline math and equation numbering were not fully preserved.
- Four tables were detected. Tables 1–3 had structural or mathematical-text
  defects; Table 4 retained the checked structure and scores. Disabling cell
  matching improved column separation but introduced false parameter values,
  so that alternative was rejected.
- EasyOCR loaded local English and Chinese weights with downloading disabled.
  The English title smoke test passed. The Chinese/English reader confused
  `All` with `AII`; successful loading is not a claim of OCR accuracy.

These results do not qualify the extracted mathematical content for unattended
training-data use. Keep the source PDF and structured provenance for review;
nonempty LaTeX and a successful conversion status are not correctness checks.
No source PDF, model weights or local validation report is bundled in Git.

### Isolated native parser and local formula crops

Install the worker in a separate environment, using an ASCII path on Windows:

```powershell
python -m venv F:\docling-runtime
F:\docling-runtime\Scripts\python.exe -m pip install -r requirements-docling-runtime.txt
```

In the existing input node, select **Docling 本地版面** and set
**独立解析环境 Python（可选）** to that environment's Python executable.
Prepare these complete local model directories:

```text
models/
  docling-project--docling-layout-heron/
  docling-project--docling-models/model_artifacts/tableformer/accurate/
  breezedeus--pix2text-mfr-1.5/
```

The third directory is required only when local formula recognition is enabled.
It uses the official [Pix2Text MFR 1.5 pure-formula ONNX route](https://huggingface.co/breezedeus/pix2text-mfr-1.5/blob/1cef9f0bdcd6a4c63df7de1311fb0894593340cc/README.md),
with `TrOCRProcessor` and `ORTModelForVision2Seq`. The model is MIT licensed.
The reviewed revision is `1cef9f0bdcd6a4c63df7de1311fb0894593340cc`.
Keep both encoder and decoder ONNX weights, all processor/tokenizer files,
and generation settings together. No custom model implementation or remote
code is loaded.

Detected display formulas are rendered from their original PDF coordinates at
240 dpi with a 2-point margin. Pure mathematical table cells use a smaller
0.5-point margin, clipped by adjacent cell geometry. Each result retains its
original text, page, crop coordinates, image hash, raw prediction, and status.
Mixed prose and math remain unchanged and require review. Rotated or offset
page coordinates that the character selector cannot safely map also require
review. A failed crop affects that item; it does not discard unrelated pages.
Model initialization or inference failures still stop conversion.

Numeric conservation is only a rejection check: changed digits are rejected,
but matching digits do not prove correct order, exponents, or meaning.
Truncated, empty, or unknown-symbol predictions are not published as valid
formulas. A page with unresolved formula evidence is quarantined. Recognized
formulas remain **unverified** until compared with the PDF.

Table span refinement uses visible PDF rules and explicit cell boundaries.
It does not infer missing table values or substitute paper-specific answers.
Unsupported table geometry retains the upstream structure and an explanation.
Inline formulas inside prose are not replaced using guessed glyph clusters.

The worker disables remote services and downloads. A Python audit hook blocks
its socket operations; inference uses only local assets. This is a runtime
guard for the pinned libraries, not an operating-system network sandbox.
The parent serializes local parsing across application processes, validates
all page results before publishing any, and checks the source and model
signature before and after parsing. Cancellation stops the owned process tree.
The persistent source library reuses matching parsed documents across jobs.
Changing the backend, weights, crop policy, or selection policy invalidates
that reuse.

UI preflight checks installed package metadata and required local files.
It does not hash large weights on every rerender or load neural models.
Full content hashes are checked when a task starts. Setup errors remain
actionable inside the input node.

### Follow-up paper validation (2026-10-20)

The actual isolated-worker adapter parsed the same fixed 15-page PDF in
61.235 seconds on CPU. Model API calls remained disabled. The final audit was
bound to hashes of the document, formulas, tables, page parts, Markdown, and
run metrics.

- All six display-formula items, representing five source formula groups,
  retained the checked mathematical structure.
- All four table shapes, spans, values outside mathematical cells, and
  original blank cells matched the frozen source checks. The audit covered
  187 nonempty nonmathematical cells and 144 explicit blank cells.
- Thirty-four of 35 mathematical table cells were accepted after source
  comparison. The remaining mixed text/math header, `params ×10⁶`, was
  deliberately left unchanged and page 9 quarantined. An earlier local
  proposal read it correctly, but the conservative mixed-content guard
  cannot authorize replacing that cell automatically.
- All 15 physical page identities and eight cross-page text fragments were
  correct. On pages 9 and 10, a continuation paragraph still precedes a
  floating table in exported reading order.
- Six of eight checked inline-math examples still lost fractions, scripts,
  summation limits, or dimension relationships. The two literal successes
  do not establish general inline recognition.
- Picture coordinates and captions remain in the document. Embedded image
  payloads and complete diagram semantics are not produced. A title inside
  the page-13 figure remains in structured data but not in page Markdown.

TableFormer V2, Camelot, and Granite crop experiments were retained as
comparisons, not silently adopted. Some corrected an individual region but
introduced other numeric or structural errors. Reference answers were used
only by the independent evaluator; none entered the extraction pipeline.

An additional isolated experiment uses the official
[PP-DocLayoutV3 ONNX model](https://huggingface.co/PaddlePaddle/PP-DocLayoutV3_onnx)
through PaddleOCR/PaddleX 3.7.0. Code and model are Apache-2.0. Local detection
on pages 4–7 took 2.929 seconds after initialization, with
`filter_overlap_boxes=False` to retain formulas nested inside text blocks.
Seven of eight frozen examples had their main formula covered, but one lost
a variable prefix and two crops included neighboring text. Reading the actual
predicted crops with MFR passed formula-fragment checks in five of eight cases
(eight of eleven fragments), not full paragraph reconstruction. It also converted stray
ink into false accents in one projection formula. The first experiment did
not authorize automatic paragraph replacement.

### Optional inline formula refinement

The input node now accepts an optional **Inline formula Python environment**.
Enable local formula recognition and set both the Docling interpreter and this
separate detector interpreter. Install `requirements-docling-inline.txt` in the
detector environment. Place the pinned PP-DocLayoutV3 ONNX assets in
`PaddlePaddle--PP-DocLayoutV3_onnx` under the same local model directory.
The tested model revision is
`46bbdf188bb0a772c08aed74882ce7e51a8f1ea6`. Parsing does not download assets.

The detector is isolated because PaddleX and the current Docling runtime need
different NumPy and OpenCV packages. Both processes block Python socket access.
The existing process-tree cancellation boundary also owns this child process.
Model hashes, package versions, crop policies and export policies participate
in the source-library cache signature.

The refinement path uses a full-page view and 480-dpi paragraph crops. Each
paragraph is centered on a white square so the detector preserves its aspect
ratio. This avoids stretching a short line into a square. Native PDF glyphs
and raster ink constrain boundary changes. They can recover a clipped
identifier prefix or remove detached ink from a neighboring text line.
The raw detector box, proposed box, character IDs and changed-pixel counts
remain available as evidence.
Detector boxes are intersected with the actual pasted source image before
mapping back to PDF coordinates. White padding cannot introduce characters
from an unseen neighboring line. Both raw and clipped image boxes are retained.

Overlapping views are compared by their source character coverage. A crop
containing only part of an expression cannot suppress a safe larger crop.
Conflicting coverage requires review. Adjacent rows and formulas separated by
prose are never joined just because their boxes are close.

Mixed formula boxes receive an additional check before whole-box OCR. Native
multiword text triggers a finer layout probe. An unknown-language code box with
math fonts may also be probed; declared-language and fixed-pitch code is
protected. The probe uses official Docling Parse `textline_cells` for physical
line views, then closes each view around native glyph extents. It keeps the
line IDs and source coordinates. Overlapping script or fraction rows share
detector context; this does not assert that they form one equation.

An overbroad detector box may be retained as a layout container only when its
glyphs and raster ink are accounted for by formula candidates and untouched
ordinary prose. Exact source-character alignment can place adjacent formula
fragments when the original whitespace proves their boundaries. Independent
expressions remain separate. A successful mixed-block reconstruction uses a normal text
item with embedded math, while preserving its reference and provenance.
Code items keep their original type even when a probe produces matching math:
character agreement alone cannot establish that programming-language content
was mislabeled. Their proposed reconstruction remains review evidence.
Unresolved items keep their original representation and review evidence.

Before insertion, the pipeline checks native-parser characters against the
PDF ink glyphs. `pylatexenc` decodes the candidate only for a variable/digit
inventory check. This catches omissions and changes such as Latin `v` becoming
Greek upsilon; it does not prove mathematical equivalence. Exact prose anchors
and source character inventories then locate the replacement spans. Native
line-end word hyphens may be ignored during anchor matching only when glyph
geometry and the existing Docling paragraph establish the correspondence.
Published prose is retained verbatim.
Ordinary words next to parentheses are not sufficient evidence of a function
call. The limited roman-name exception requires an operator, a continuous
name and an adjacent opening parenthesis, with a mathematical variable on the
same baseline. Spaces within the name or before its parenthesis prevent this
exception. Operator spacing is allowed only inside a complete enclosing
mathematical expression, with every intervening source ID proven to be a
same-line space. Missing, duplicate or nonspace source characters block it.

For a supported arithmetic continuation, physical lines must belong to the
same source paragraph and preserve reading order. A later line must begin with
an arithmetic operator and must not introduce another equation. The pipeline
masks neighboring prose from one source crop, checks that no formula ink was
removed, and runs Pix2Text again on the complete expression. It accepts this
whole prediction only when its parsed structure agrees with the verified
rows. Export uses one math span, not concatenated row predictions. A failed
whole-expression check blocks publication of the partial reconstruction.

Source PDF fonts also provide evidence. Pinned `pypdf` and `fontTools` readers
can resolve a Type1 symbol encoding or an explicit bold declaration from the
embedded font program. Each claim must bind to a source character, position,
font and file hash. Unsupported encodings remain unresolved. Explicit bold
commands in OCR output must agree with uniquely identified source glyphs;
unknown or ambiguous weights require review. A complete prediction with a
variable mismatch or a known unsupported bold style may receive two bounded
retries of the identical region, at 240 and 360 dpi. All raw attempts remain
available. The pipeline never fixes a candidate by replacing variables or
stripping its bold commands.

Already readable scalar values, identifiers, punctuation and author footnote
markers can remain native text when the source glyphs, parser characters and
paragraph agree. These records are marked as preserved, not new OCR fixes.
Paragraphs spanning pages are processed by their proven source character spans
and reassembled atomically. Any unresolved part keeps the entire original
paragraph available for review.

Only a paragraph with no outstanding boundary, coverage or assembly issue is
rewritten. Other paragraphs keep their original text and proposed candidates
for review; their pages remain quarantined. Proven formula spans receive
protected Markdown serialization, so a subscript `_` does not become a literal
`\_`, and a comparison `<` is not converted into an HTML entity inside math.

This remains a conservative native-PDF path. Scanned pages, rotated coordinate
frames, ambiguous multi-page paragraphs, unsupported equation continuations and
missed inline regions are not silently treated as solved. Recognition, complete
paragraph assembly and actual export are evaluated separately. None of the
paper's expected formulas or acceptance strings enter the recognizer.

The initial 2026-10-20 local adapter run on the same 15-page Attention paper took
181.062 seconds. It published 17 formula spans in 10 paragraphs. Independent
checks passed all 17 inserted spans and confirmed that reverting those
insertions recovered the original prose. Five of the eight fixed difficult
inline examples passed, compared with two before refinement. This is a
small, selected benchmark, not a general accuracy estimate. Twenty-six
inline records still required review, and pages 1–10 remained quarantined.
Existing display-formula and table outputs were unchanged.

A separate two-page native PDF fixture in that initial run took 39.188 seconds through the same
adapter. A polynomial split across two physical lines was reconstructed as
two correct, ordered math spans. Its trailing prose stayed outside LaTeX.
Two adjacent independent equations remained separate. A code-labeled region
and an incorrectly capitalized symbol were held for review. This demonstrates
physical line recovery and prose preservation; it does not establish semantic
continuation for arbitrary equations. No remote model API was used. The
relevant regression suite finished with 594 passed and one skipped.

The follow-up run on the same unchanged paper took 204.359 seconds. The original
26 review records were resolved as 12 mathematical-structure repairs, four
formatting repairs and ten source-preservation cases. Across the paper, 26
paragraphs contain 55 published math spans. All 458 original text fields remain
unchanged. Display formulas and tables are byte-identical to the prior run.
Page 9 still requires review for the existing mixed table-header issue; this
is separate from the resolved inline records.

The final two-page fixture took 42.438 seconds. Its wrapped polynomial now
exports as one complete `aligned` expression, with the trailing prose outside
the math span. Independent neighboring equations remain separate. Real crop
retries restore the lowercase variable and the ordinary-weight subscripted
variable, while retaining the rejected uppercase and bold outputs. The
code-labeled example remains held because character agreement cannot resolve
its document role. Both final runs used the actual adapter with networking
disabled. The broader regression run passed 784 tests with one skipped; after
the final boundary changes, 180 relevant regression tests passed again.
These are results on this paper and fixture, not a general PDF accuracy claim.

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
