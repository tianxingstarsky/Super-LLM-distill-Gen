"""Image assistance preserves source evidence and rejects unsafe automatic patches."""
from copy import deepcopy
import hashlib
import json

import pytest

from lib.infrastructure import docling_vision_assist as assist
from lib.infrastructure import docling_document as adapter
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.source_library import SourceLibrary
from lib.domain.document_parser import validate_document_parser
from test_docling_document import local_config
from test_document_model_parser import text_pdf, PAGE_ONE
from test_document_vision import config as vision_config, parser as vision_parser
from test_source_library import registered


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # All transports in this module are in-memory doubles.


def transcript(text="The result is $x^{2}$."):
    return {"kind":"text","text":text,"uncertain":False}


def part(source):
    text="The result is x2."
    return {"location":"page:1","text":text,"status":"quarantined",
        "reason":"document_docling_formula_undecoded","document_reading":{
            "mode":"docling","source_pdf_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_page":1,"parsed_text_sha256":assist.digest(text),
            "formula_review":{"undecoded_formula_refs":["#/texts/0"]},
            "elements":[{"item_ref":"#/texts/0","label":"text","provenance":{
                "bbox":{"l":1,"b":1,"r":10,"t":10}},"local_formula_transcription":{
                    "status":"review_required","original_text_slot":text}}]}}


@pytest.fixture
def evidence(tmp_path,monkeypatch):
    source=tmp_path/"source.pdf";source.write_bytes(b"immutable-source")
    monkeypatch.setattr(assist,"_images",lambda *args:([
        {"image":"data:image/png;base64,AA==","sha256":str(i),"size":[10,10]} for i in range(2)],"The result is x2."))
    return source,part(source)


def test_two_blind_reads_preserve_original_and_complete_wrapped_region(evidence):
    source,original=evidence;before=deepcopy(original);calls=[]
    def read(payload,images,key):
        calls.append((payload,images,key))
        assert "transcript" not in payload and "expected" not in payload
        return transcript("The result is $x^2$." if len(calls)==2 else "The result is $x^{2}$.")
    result=assist.assist_part(source,original,read)
    assert original==before and "status" not in result
    report=result["document_reading"]["vision_assistance"]
    assert report["original_text"]==original["text"]
    assert report["items"][0]["native_content_checked"]
    assert report["mathematical_correctness_certified"] is False
    assert [c[0]["independent_read"] for c in calls]==[0,1]
    assert all(len(c[1])==2 for c in calls)


@pytest.mark.parametrize("replacement",["The result is $x_2$.","The result is $y^2$.","A different result is $x^2$."])
def test_actual_script_or_content_disagreement_stays_quarantined(evidence,replacement):
    source,original=evidence
    result=assist.assist_part(source,original,lambda payload,*args:transcript(
        replacement if payload["independent_read"] else "The result is $x^2$."))
    assert result["status"]=="quarantined" and result["text"]==original["text"]


def test_agreed_wrong_prose_is_rejected_and_retry_is_bounded(evidence):
    source,original=evidence;calls=[]
    def read(*args):calls.append(args);return transcript("A made up result is $x^2$.")
    result=assist.assist_part(source,original,read)
    assert len(calls)==4
    assert result["document_reading"]["vision_assistance"]["items"][0]["reason"]=="vision_assist_source_text_changed"
    assert result["status"]=="quarantined"


def test_agreed_extra_operator_is_rejected_despite_identical_letter_inventory(evidence):
    source,original=evidence
    result=assist.assist_part(source,original,lambda *args:transcript("The result is $x^{+2}$."))
    assert result["status"]=="quarantined"
    assert result["document_reading"]["vision_assistance"]["items"][0]["reason"]=="vision_assist_source_operators_changed"


@pytest.mark.parametrize("reason",["document_docling_reading_order_requires_review","document_docling_picture_export_requires_review"])
def test_assisting_formulas_does_not_clear_other_quarantine_reasons(evidence,reason):
    source,original=evidence;original["reason"]=reason
    result=assist.assist_part(source,original,lambda *args:transcript())
    assert result["status"]=="quarantined" and result["reason"]==reason


def test_unknown_refs_and_unbound_markers_are_not_silently_cleared(evidence):
    source,original=evidence
    original["document_reading"]["formula_review"]["undecoded_formula_refs"].append("#/texts/unknown")
    result=assist.assist_part(source,original,lambda *args:transcript())
    assert result["status"]=="quarantined"
    assert result["document_reading"]["vision_assistance"]["remaining_refs"]==["#/texts/unknown"]
    original["document_reading"]["formula_review"]["undecoded_formula_refs"].pop()
    original["text"]+="\n<!-- formula-not-decoded -->"
    assert assist.assist_part(source,original,lambda *args:transcript())["status"]=="quarantined"


def test_no_api_for_clean_pages_or_unbound_duplicate_paragraphs(evidence):
    source,original=evidence
    clean=deepcopy(original);clean["document_reading"]["elements"]=[]
    def forbidden(*args):pytest.fail("Unexpected API call")
    assert assist.assist_part(source,clean,forbidden)==clean
    original["text"]+="\n\n"+original["text"]
    result=assist.assist_part(source,original,forbidden)
    assert result["status"]=="quarantined"


def test_display_formula_marker_is_bound_only_to_matching_export_order(evidence):
    source,original=evidence;original["text"]="<!-- formula-not-decoded -->\n\nOther source text."
    element=original["document_reading"]["elements"][0]
    element.pop("local_formula_transcription");element["label"]="formula"
    element["undecoded_formula"]={"original_text":"The result is x2.","decoded_text":""}
    original["document_reading"]["page_export"]={"reading_order":{"elements":[{"label":"formula","ref":"#/texts/0"}]}}
    result=assist.assist_part(source,original,lambda *args:transcript())
    assert "status" not in result and result["text"].endswith("Other source text.")
    original["text"]+="\n\n<!-- formula-not-decoded -->\n\nThe result is x2."
    def forbidden(*args):pytest.fail("Ambiguous marker ownership must not call the API")
    assert assist.assist_part(source,original,forbidden)["status"]=="quarantined"


def test_source_mutation_and_cancellation_abort_without_patch(evidence):
    source,original=evidence
    def cancel():raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError,match="cancelled"):
        assist.assist_part(source,original,lambda *args:transcript(),check_cancel=cancel)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError,match="source_snapshot_changed"):
        assist.assist_part(source,original,lambda *args:transcript())


def table():
    return {"kind":"table","uncertain":False,"rows":2,"columns":2,"cells":[
        {"row":0,"column":0,"row_span":2,"col_span":1,"text":"A & <B>"},
        {"row":0,"column":1,"row_span":1,"col_span":1,"text":r"$x^2$"},
        {"row":1,"column":1,"row_span":1,"col_span":1,"text":""}]}


def test_table_grid_keeps_merged_and_empty_cells_and_escapes_source():
    value=assist.validate_transcript(table(),"table")
    rendered=assist.table_markdown(value)
    assert 'rowspan="2"' in rendered and "A &amp; &lt;B&gt;" in rendered
    assert rendered.count("<td ")==3 and "$x^2$" in rendered
    other=deepcopy(value);other["cells"][0]["row_span"]=1
    with pytest.raises(ValueError,match="incomplete_grid"):assist.validate_transcript(other,"table")
    other=deepcopy(value);other["cells"].append(other["cells"][0])
    with pytest.raises(ValueError,match="overlapping_grid"):assist.validate_transcript(other,"table")


def test_merged_table_remains_an_indivisible_source_chunk():
    from lib.doc2corpus import chunk_text
    table_text=assist.table_markdown(table())
    text="Intro.\n\n"+table_text+"\n\nAfter the table."
    chunks=chunk_text(text,30)
    assert "".join(chunks)==text
    assert sum("<table>" in chunk and "</table>" in chunk for chunk in chunks)==1
    assert all("</table>" not in chunk or "<table>" in chunk for chunk in chunk_text(text,30,overlap=5))


def test_native_grid_merges_only_empty_subdivisions_not_conflicting_text():
    proof={"grid":table()};value=deepcopy(table())
    value["cells"][0]["row_span"]=1
    value["cells"].append({"row":1,"column":0,"row_span":1,"col_span":1,"text":""})
    assert assist.bind_source_grid(value,proof)==proof["grid"]
    value["cells"][-1]["text"]="extra content"
    with pytest.raises(ValueError,match="ambiguous_text"):assist.bind_source_grid(value,proof)
    value=deepcopy(table());value["cells"][0]["col_span"]=2
    with pytest.raises(ValueError,match="source_grid_changed"):assist.bind_source_grid(value,proof)


def test_real_pdf_rules_prove_merged_cells_without_outer_vertical_lines(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject
    writer=PdfWriter();page=writer.add_blank_page(200,200)
    stream=DecodedStreamObject()
    stream.set_data(b"10 190 m 190 190 l S 10 60 m 190 60 l S 100 130 m 190 130 l S 100 60 m 100 190 l S")
    page[NameObject("/Contents")]=writer._add_object(stream)
    source=tmp_path/"ruled.pdf"
    with source.open("wb") as handle:writer.write(handle)
    proof=assist.native_table_grid(source,1,{"l":10,"b":60,"r":190,"t":190})
    assert proof["grid"]["rows"]==2 and proof["grid"]["columns"]==2
    assert len(proof["grid"]["cells"])==3
    assert any(c["column"]==0 and c["row_span"]==2 for c in proof["grid"]["cells"])


@pytest.mark.parametrize("left,right",[(r"$x_i$",r"$x^i$"),(r"$x^{ab}$",r"$x^ab$"),
    (r"$\mathbf{x}$",r"$x$"),(r"$\begin{bmatrix}1\\2\end{bmatrix}$",r"$\begin{bmatrix}12\end{bmatrix}$")])
def test_math_consensus_preserves_scripts_styles_and_matrix_rows(left,right):
    assert assist.transcript_signature(transcript(left))!=assist.transcript_signature(transcript(right))


def test_consensus_ignores_only_prose_formatting_and_latex_delimiter_spelling():
    a=r"There are $100$ cases with $x^{2}$ and $3\times3$ in **bold**."
    b=r"There are 100 cases with $x^2$ and 3×3 in bold."
    assert assist.transcript_signature(transcript(a))==assist.transcript_signature(transcript(b))
    with pytest.raises(ValueError):assist.validate_transcript(transcript(r"$x^{2$"),"text")
    with pytest.raises(ValueError):assist.validate_transcript(transcript(r"$\input{evil}$"),"text")


def test_latex_array_alignment_is_not_a_matrix_content_change():
    forms=[r"$\begin{bmatrix}1\\2\end{bmatrix}$",r"$\left[\begin{matrix}1\\2\end{matrix}\right]$",
           r"$[\begin{array}{l}1\\2\end{array}]$"]
    assert all(assist.transcript_signature(transcript(f))==assist.transcript_signature(transcript(forms[0])) for f in forms)
    assert assist.transcript_signature(transcript(forms[0]))!=assist.transcript_signature(transcript(r"$\begin{pmatrix}1\\2\end{pmatrix}$"))


def test_evidence_cache_replays_quarantine_but_strict_consumers_rebuild(tmp_path):
    library,source,_=registered(tmp_path);signature={"mode":"fixture"};receipts=[]
    row={"text":"unresolved","status":"quarantined"};builds=[]
    def build():builds.append(1);yield row
    for _ in range(2):assert list(library.iter_parts(source,signature,build,
        valid=lambda r:False,cache_incomplete=True,on_status=receipts.append))==[row]
    assert len(builds)==1 and receipts[-1]["cached"] and receipts[-1]["usable_parts"]==0
    # The cache status never changes the quarantined row into eligible data.
    list(library.iter_parts(source,signature,build,valid=lambda r:False))
    assert len(builds)==2
    assert not list(library.path.rglob("parse.json"))


def test_workflow_assistance_uses_bound_role_two_images_and_cross_run_cache(tmp_path,local_config,monkeypatch):
    vision_config(tmp_path);source=text_pdf(tmp_path,pages=(PAGE_ONE,));original=part(source)
    # Keep the local parser independent from the API setting.
    binding=vision_parser()["binding"]
    parser={**local_config,"assistance":{"enabled":True,"binding":binding}}
    assert adapter.docling_parse_signature(parser)==adapter.docling_parse_signature(local_config)
    parsed=[]
    def parse(*args,**kwargs):parsed.append(1);yield original
    monkeypatch.setattr(adapter,"docling_parts",parse)
    from PIL import Image
    import base64,io
    stream=io.BytesIO();Image.new("RGB",(10,10),"white").save(stream,format="PNG")
    image="data:image/png;base64,"+base64.b64encode(stream.getvalue()).decode()
    monkeypatch.setattr(assist,"_images",lambda *args:([
        {"image":image,"sha256":str(i),"size":[10,10]} for i in range(2)],"The result is x2."))
    class Reader:
        model="writer"
        def __init__(self):self.calls=[];self.usage={"calls":0}
        def chat(self,messages,**kwargs):
            self.calls.append(messages);self.usage["calls"]+=1
            assert messages[0]["role"]=="system" and "bounded region" in messages[0]["content"]
            assert len([b for b in messages[1]["content"] if b["type"]=="image_url"])==2
            return json.dumps(transcript())
    reader=Reader();output=tmp_path/"output"
    for _ in range(2):
        identity=engine.create_run(output,sources=[source],targets=["cpt"],settings_root=tmp_path,document_parser=parser)
        run=engine.Workflow(output,identity,tmp_path,generator=reader)
        assert run.recipe["endpoint_pins"]["ingest"]["vision"]["model"]=="writer"
        rows=list(run.iter_source_units(run.recipe["sources"][0]))
        assert rows and all(row["status"]=="ready" for row in rows)
        assert rows[0]["document_reading"]["vision_assistance"]["items"][0]["status"]=="adopted"
    assert len(reader.calls)==2 and len(parsed)==1
    identity=engine.create_run(output,sources=[source],targets=["cpt"],settings_root=tmp_path,document_parser=parser,
        node_prompts={"ingest":{"workflow.document_vision":run.prompt_text("workflow.document_vision",stage="ingest")+"\nPreserve visible punctuation."}})
    changed=engine.Workflow(output,identity,tmp_path,generator=reader)
    assert list(changed.iter_source_units(changed.recipe["sources"][0]))
    assert len(reader.calls)==4 and len(parsed)==1
    vision_config(tmp_path,confirmed=False)
    with pytest.raises(ValueError,match="document_model_vision_not_confirmed"):
        engine.Workflow(output,identity,tmp_path,generator=reader)


def test_missing_assistance_model_is_rejected_before_start(local_config):
    with pytest.raises(ValueError,match="document_vision_model_required"):
        validate_document_parser({**local_config,"assistance":{"enabled":True}})


def test_prompt_extension_keeps_previous_document_reader_snapshot():
    from lib.prompts import get
    current=get("workflow.document_vision")
    old=get("workflow.document_vision",version="1.0.0")
    assert current.version=="1.3.0" and "docling_region_transcription" in current.template
    assert "docling_visual_review" in current.template
    assert get("workflow.document_vision",version="1.1.0").version=="1.1.0"
    assert get("workflow.document_vision",version="1.2.0").version=="1.2.0"
    assert "docling_region_transcription" not in old.template
