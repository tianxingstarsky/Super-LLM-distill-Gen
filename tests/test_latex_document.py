"""TeX inputs retain knowledge without execution, external reads or half formulas."""
from pathlib import Path
import re

import pytest

from lib.bootstrap.document_previews import document_preview_application
from lib.bootstrap.knowledge import knowledge_application
from lib.bootstrap.local_inputs import local_input_application
from lib.doc2corpus import doc_to_corpus, import_text
from lib.domain.dataset_assets import Asset, generation_source_mode
from lib.domain.workflow_targets import INPUT_EXTENSIONS
from lib.infrastructure.latex_document import chunk_latex, parse_latex


def test_extracts_body_title_structure_and_keeps_formulas_macros_and_tables():
    macro = r"\newcommand{\metric}[1]{\frac{#1}{2}}"
    equation = r"\begin{align} E &= mc^2 \\ x &= \metric{y} \end{align}"
    table = r"\begin{tabular}{cc} A & B \\ 1 & 2 \end{tabular}"
    source = (r"\documentclass[12pt]{article}" + "\n" + r"\usepackage{amsmath}" + "\n"
              + macro + "\n" + r"\title{Energy}\author{A. Author}\date{2026}"
              + r"\fancyhead[L]{Repeated header}\fancyfoot{Page \thepage}"
              + "\n" + r"\begin{document}\maketitle\section{Definitions}"
              + "\nWe use $a^2 + b^2 = c^2$.\n" + equation + "\n" + table + r"\end{document}")
    text = parse_latex(source)
    assert "# Energy" in text and "## Definitions" in text
    assert "A. Author" in text and "2026" in text
    assert macro in text and equation in text and table in text
    assert "$a^2 + b^2 = c^2$" in text
    assert "Repeated header" not in text and "Page" not in text
    assert "documentclass" not in text and "usepackage" not in text
    assert "begin{document}" not in text and "end{document}" not in text


def test_comments_and_escaped_percent_follow_tex_semantics():
    text = parse_latex("Word% hidden comment\njoined.\nPercent \\% remains.\n% second comment\nNext.")
    assert "Wordjoined." in text
    assert r"\%" in text
    assert "hidden" not in text and "second comment" not in text


def test_literal_examples_preserve_percent_blank_lines_and_external_command_text():
    literal = "\\begin{verbatim}\n\\input{example-only} % literal\n\n\nvalue = 4\n\\end{verbatim}"
    inline = r"\verb|\include{example} % literal|"
    source = literal + "\n" + inline + r"\begin{comment}\input{ignored}\end{comment}"
    text = parse_latex(source)
    assert literal in text and inline in text
    assert "ignored" not in text


@pytest.mark.parametrize("command", [
    r"\input{private}", r"\include{chapters/one}", r"\includegraphics[width=3cm]{plot.png}",
    r"\bibliography{sources}", r"\addbibresource{sources.bib}", r"\lstinputlisting{code.py}",
    r"\import{path}{file}", r"\includepdf{other.pdf}", r"\write18{shell command}",
    r"\newcommand{\load}{\input{private}}",
])
def test_external_dependencies_are_explicit_even_when_hidden_in_a_macro(command):
    with pytest.raises(ValueError, match="^latex_external_dependencies$"):
        parse_latex(command)


def test_escaped_control_sequence_example_is_not_an_external_command():
    assert r"\\input{example}" in parse_latex(r"\\input{example}")


def test_character_rule_changes_are_not_silently_misparsed():
    with pytest.raises(ValueError, match="^latex_unsupported_syntax$"):
        parse_latex(r"\catcode`\%=12")


@pytest.mark.parametrize("source,reason", [
    (r"\begin{document}text", "latex_invalid_document_environment"),
    (r"\end{document}", "latex_invalid_document_environment"),
    (r"\begin{document}text\end{document}ignored facts", "latex_content_after_document"),
    (r"\section{broken", "latex_unbalanced_group"),
    (r"$\frac{1}{broken$", "latex_unbalanced_group"),
    ("$x + y", "latex_unclosed_math"),
    (r"\begin{align}x=1", "latex_unclosed_environment"),
])
def test_incomplete_source_is_an_explicit_error(source, reason):
    with pytest.raises(ValueError, match="^" + reason + "$"):
        parse_latex(source)


def test_macro_replacement_text_and_environment_definitions_are_not_reformatted():
    macro = r"\newcommand{\heading}[1]{\section{#1}}"
    environment = r"\newenvironment{heading}{\section{Start}}{\paragraph{End}}"
    text = parse_latex(macro + "\n" + environment + r"\section{Real heading}")
    assert macro in text and environment in text
    assert "## Real heading" in text
    assert "## #1" not in text and "## Start" not in text


def test_formula_and_section_counters_retain_reference_numbering_context():
    source = (r"\setcounter{equation}{5}\setcounter{section}{2}"
              + r"\section{Energy}\begin{equation}E=mc^2\label{energy}\end{equation}"
              + r"Refer to equation \ref{energy}.")
    text = parse_latex(source)
    assert r"\setcounter{equation}{5}" in text
    assert r"\setcounter{section}{2}" in text
    assert r"\label{energy}" in text and r"\ref{energy}" in text


def test_distinct_title_displays_keep_their_own_authors_and_dates_in_order():
    source = (r"\title{First}\author{Alice}\date{2025}\maketitle" + "\nFirst work.\n"
              + r"\title{Second}\author{Bob}\date{2026}\maketitle" + "\nSecond work.")
    text = parse_latex(source)
    assert text.count("# First") == 1 and text.count("# Second") == 1
    first, second = text.split("# Second")
    assert "Alice" in first and "2025" in first and "First work." in first
    assert "Bob" not in first and "2026" not in first
    assert "Bob" in second and "2026" in second and "Second work." in second
    assert "Alice" not in second


def test_metadata_without_title_display_is_preserved_once_and_late_changes_remain():
    assert parse_latex(r"\title{Standalone}\author{Alice}Body.").startswith("# Standalone\n\nAlice")
    text = parse_latex(r"\title{First}\maketitle Body.\title{Later}\author{Bob}")
    assert text.count("# First") == 1 and text.count("# Later") == 1
    assert text.index("# First") < text.index("Body.") < text.index("# Later")
    assert text.endswith("# Later\n\nBob")


@pytest.mark.parametrize("atom", [
    r"$" + "a + " * 30 + r"b$",
    r"$$" + "a + " * 30 + r"b$$",
    r"\[" + "a + " * 30 + r"b\]",
    r"\(" + "a + " * 30 + r"b\)",
    r"\begin{align}" + "a + " * 30 + r"b=0\end{align}",
    r"\begin{tabular}{cc}" + "A & B \\\\ " * 30 + r"\end{tabular}",
    "\\begin{verbatim}\n" + "code = data\n" * 30 + "\\end{verbatim}",
    r"\newcommand{\longdefinition}{" + "macro " * 30 + "}",
    r"\customformat{" + "formatted content " * 30 + "}",
])
def test_chunks_never_split_an_atomic_structure_even_when_larger_than_target(atom):
    text = "Before the structure.\n\n" + atom + "\n\nAfter the structure."
    chunks = chunk_latex(text, 40)
    assert any(atom in chunk for chunk in chunks)
    assert any(len(chunk) > 40 for chunk in chunks)
    assert re.sub(r"\s+", "", "".join(chunks)) == re.sub(r"\s+", "", text)


def test_many_small_inline_formulas_preserve_order_and_complete_tokens():
    text = " ".join(f"item {index} $x_{{{index}}}=1$" for index in range(300))
    chunks = chunk_latex(text, 50)
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert re.sub(r"\s+", "", "".join(chunks)) == re.sub(r"\s+", "", text)
    for index in range(300):
        assert any(f"$x_{{{index}}}=1$" in chunk for chunk in chunks)


@pytest.mark.parametrize("suffix,encoding", [(".tex", "utf-8"), (".LATEX", "gb18030")])
def test_upload_native_preview_and_asset_handoff_support_tex(tmp_path, suffix, encoding):
    formula = r"\begin{equation}" + "x+" * 150 + r"y=0\end{equation}"
    raw = (r"\section{设备维护}" + "\n更换电池必须断开电源。\n" + formula).encode(encoding)
    row = local_input_application(tmp_path).store([("manual" + suffix, raw)])[0]
    assert Path(row["path"]).read_bytes() == raw
    assert Path(row["path"]).suffix.lower() in INPUT_EXTENSIONS
    preview = document_preview_application(root=tmp_path)
    result = preview.preview(row["path"], 200)
    assert result["signature"] == preview.signature(row["path"], 200)
    assert any(formula in chunk for chunk in result["chunks"])
    assert "断开电源" in import_text(row["path"])
    asset = Asset("id", "source", "manual" + suffix, "来源资料", len(raw), 1)
    assert generation_source_mode(asset) == "文档资料"


def test_corpus_export_preserves_verbatim_and_complete_formula(tmp_path):
    formula = r"\[" + "a + " * 100 + r"b\]"
    literal = "\\begin{verbatim}\nfirst\n\n\nlast\n\\end{verbatim}"
    source = tmp_path / "source.tex"
    source.write_text("Prose.\n\n" + formula + "\n\n" + literal, encoding="utf-8")
    result = doc_to_corpus(source, target_chars=50)
    assert any(formula in row["text"] for row in result["entries"])
    assert any(literal in row["text"] for row in result["entries"])
    with pytest.raises(ValueError, match="latex_overlap_not_supported"):
        doc_to_corpus(source, target_chars=50, overlap=10)


@pytest.mark.parametrize("suffix", [".tex", ".latex"])
def test_tex_corpus_keeps_code_blocks_that_differ_in_meaningful_indentation(tmp_path, suffix):
    from lib.doc2corpus import chunk_hash

    first = "\\begin{verbatim}\nif ready:\n    send()\n    log()\n\\end{verbatim}"
    second = "\\begin{verbatim}\nif ready:\n    send()\nlog()\n\\end{verbatim}"
    assert chunk_hash(first) == chunk_hash(second), "The legacy hash contract must remain unchanged."
    source = tmp_path / ("code" + suffix)
    source.write_text(first + "\n\n" + second, encoding="utf-8")
    manifest = set()
    result = doc_to_corpus(source, target_chars=30, manifest=manifest)
    assert result["stats"]["kept"] == 2 and result["stats"]["dups"] == 0
    assert [row["text"] for row in result["entries"]] == [first, second]
    repeated = doc_to_corpus(source, target_chars=30, manifest=manifest)
    assert repeated["stats"]["kept"] == 0 and repeated["stats"]["dups"] == 2


def test_external_dependency_preview_and_rag_errors_are_specific_and_keep_old_index(tmp_path):
    upload = local_input_application(tmp_path)
    row = upload.store([("source.tex", b"Original facts about battery replacement.")])[0]
    knowledge = knowledge_application(root=tmp_path)
    original = knowledge.index([row["path"]])
    Path(row["path"]).write_text(r"\input{C:/private/file.tex}", encoding="utf-8")
    with pytest.raises(ValueError, match="^preview_latex_external_dependencies$"):
        document_preview_application(root=tmp_path).preview(row["path"], 200)
    with pytest.raises(ValueError, match="^knowledge_latex_external_dependencies$"):
        knowledge.index([row["path"]])
    assert knowledge.status()["revision"] == original["revision"]
    assert "Original facts" in knowledge.retrieve("local", "battery", 1)["hits"][0]["text"]


def test_rag_preserves_whole_formula_in_a_retrieved_source(tmp_path):
    formula = r"\begin{equation}" + "x + " * 200 + r"y=0\end{equation}"
    row = local_input_application(tmp_path).store([
        ("energy.tex", ("Energy conservation.\n\n" + formula).encode())])[0]
    knowledge = knowledge_application(root=tmp_path)
    assert knowledge.index([row["path"]], 200)["documents"] == 1
    hits = knowledge.retrieve("local", "equation", 5)["hits"]
    assert any(formula in hit["text"] for hit in hits)


def test_rag_rejects_oversized_formula_without_cutting_it(tmp_path):
    formula = r"\[" + "x + " * 5100 + r"y\]"
    row = local_input_application(tmp_path).store([("large.tex", formula.encode())])[0]
    knowledge = knowledge_application(root=tmp_path)
    with pytest.raises(ValueError, match="^knowledge_latex_atomic_block_limit$"):
        knowledge.index([row["path"]], 200)
    assert knowledge.status()["documents"] == 0


def test_real_tex_rag_receipt_preserves_formula_through_model_workflow_parser(tmp_path):
    """A materialized Markdown snapshot retains its complete TeX provenance unit."""
    import hashlib
    import json

    from lib.infrastructure import training_workflow as engine
    from tests.test_document_model_parser import Reader, configure, model_parser

    formula = r"\begin{equation}" + "x + " * 250 + r"y=0\end{equation}"
    assert 600 < len(formula) < 12_000
    source = local_input_application(tmp_path).store([
        ("energy.tex", ("Energy conservation.\n\n" + formula).encode("utf-8"))])[0]
    knowledge = knowledge_application(root=tmp_path)
    knowledge.index([source["path"]], 200)
    retrieval = knowledge.retrieve("local", "equation", 1)
    assert len(retrieval["hits"]) == 1
    assert retrieval["hits"][0]["text"] == formula
    saved = knowledge.materialize(retrieval)
    assert Path(saved[0]["path"]).suffix == ".md"
    persisted = json.loads(Path(saved[0]["receipt"]).read_text(encoding="utf-8"))
    assert persisted.pop("sources")[0]["path"] == saved[0]["path"]
    assert persisted == retrieval

    configure(tmp_path)
    output = tmp_path / "output"
    run_id = engine.create_run(
        output, sources=[saved[0]["path"]], targets=["cpt"], settings_root=tmp_path,
        document_parser=model_parser(), cpt_processing={"mode": "native"},
        chunk_chars=600, knowledge_retrieval=persisted)
    reader = Reader()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=reader)
    assert workflow.recipe["version"] >= 14
    receipt_hit = workflow.recipe["knowledge_retrieval"]["hits"][0]
    assert receipt_hit["snapshot"] == {
        "file": workflow.recipe["sources"][0]["file"],
        "sha256": hashlib.sha256(formula.encode("utf-8")).hexdigest()}
    units = workflow.parse_source(workflow.recipe["sources"][0])
    assert len(units) == 1 and units[0]["status"] == "ready"
    assert units[0]["text"] == formula
    assert len(reader.calls) == 1 and reader.calls[0][2]["text"] == formula
    assert units[0]["document_reading"]["source_text"] == formula
