"""Source-bound visual review, discovery, correction and cache behavior."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import docling_vision_assist as assist
from lib.infrastructure import docling_visual_review as review
from lib.infrastructure import docling_document as adapter
from lib.infrastructure import training_workflow as engine
from lib.domain.document_parser import validate_document_parser
from test_docling_vision_assist import evidence,part,transcript
from test_docling_document import local_config
from test_document_vision import config as vision_config,parser as vision_parser
from test_document_model_parser import text_pdf,PAGE_ONE


@pytest.fixture(scope="session",autouse=True)
def mock_llm_server():
    yield


def judgment(verdict="pass",issues=None):
    return {"kind":"visual_review","verdict":verdict,
            "checks":{k:"pass" for k in ("text","numbers","math","structure","completeness")},"issues":issues or []}


def issue(ref="#/texts/0",quote="The result is x2.",category="formula"):
    return {"ref":ref,"category":category,"evidence":"The source has a superscript 2, but the candidate uses a baseline digit.",
            "candidate_quote":quote}


def page_geometry(monkeypatch):
    from lib.infrastructure import docling_math_regions
    monkeypatch.setattr(docling_math_regions,"extract_pdf_page_glyphs",lambda *args:{"width":100,"height":100,"glyphs":[]})


@pytest.mark.parametrize("change",["issues","check","numbers","math"])
def test_contradictory_approval_cannot_pass(change):
    value=judgment()
    if change=="issues":value["issues"]=[issue()]
    else:value["checks"][{"check":"completeness"}.get(change,change)]="uncertain" if change=="check" else "not_applicable"
    with pytest.raises(ValueError):review.validate_review(value,refs={"#/texts/0"},candidate="$x^2$")


@pytest.mark.parametrize("value",[None,{"kind":"visual_review"},judgment("revise")])
def test_malformed_or_unsupported_judgment_is_not_approval(value):
    with pytest.raises(ValueError):review.validate_review(value)


def test_reviewer_cannot_invent_source_refs_or_candidate_quotes():
    with pytest.raises(ValueError,match="unknown_source_ref"):
        review.validate_review(judgment("revise",[issue(ref="#/texts/invented")]),refs={"#/texts/0"})
    with pytest.raises(ValueError,match="invented_candidate_quote"):
        review.validate_review(judgment("revise",[issue()]),refs={"#/texts/0"},candidate="Another paragraph.")
    with pytest.raises(ValueError,match="not_owned"):
        review.validate_review(judgment("revise",[issue()]),refs={"#/texts/0"},candidate="The result is x2.",
                               bindings={"#/texts/0":"A different source paragraph."})


def test_region_recognition_requires_a_separate_visual_approval(evidence):
    source,original=evidence;calls=[]
    def call(payload,images,key):
        calls.append(payload)
        if payload["operation"]=="docling_visual_review":
            assert payload["scope"]=="region" and payload["candidate"]=="The result is $x^{2}$."
            return judgment()
        assert "candidate" not in payload
        return transcript()
    result=assist.assist_part(source,original,call,review_scope="regions")
    assert "status" not in result
    assert [c["operation"] for c in calls]==["docling_region_transcription"]*2+["docling_visual_review"]
    item=result["document_reading"]["vision_assistance"]["items"][0]
    assert item["visual_reviews"][0]["candidate_sha256"]==assist.digest(item["transcription"]["text"])


@pytest.mark.parametrize('correct_second',[True,False])
def test_extra_model_check_requires_a_bounded_schema_retry_not_silent_approval(evidence,correct_second):
    source,original=evidence;reviews=[]
    def call(payload,*args):
        if payload['operation']=='docling_region_transcription':return transcript()
        reviews.append(payload)
        value=judgment()
        if len(reviews)==1 or not correct_second:value['checks']['reading_order']='pass'
        return value
    result=assist.assist_part(source,original,call,review_scope='regions',max_rounds=1)
    assert len(reviews)==2 and 'schema_correction' in reviews[1]
    audit=result['document_reading']['vision_assistance']['items'][0]['visual_reviews'][0]
    assert audit['schema_attempts'][0]['error']=='vision_review_invalid_result'
    assert audit['verdict']==('pass' if correct_second else 'uncertain')
    assert (result.get('status')=='quarantined') is not correct_second


def test_retained_multimodal_source_requires_real_image_bytes_and_changes_review_identity(tmp_path):
    from test_document_figure_bundle import record
    unit=record(tmp_path);pictures=review._retained_pictures(unit)
    assert len(pictures)==1 and pictures[0]['destination']=='separate_source_multimodal_corpus'
    assert pictures[0]['generated'] is False and pictures[0]['sft_validated'] is False
    assert 'image' not in pictures[0]
    unit['document_reading']['picture_assets']['items'][0]['image_sha256']='f'*64
    with pytest.raises(ValueError,match='integrity'):review._retained_pictures(unit)


def test_visual_comparison_may_select_only_one_source_checked_candidate(evidence):
    source,original=evidence
    def call(payload,*args):
        if payload["operation"]=="docling_region_transcription":
            return transcript("The result is $x_2$." if payload["independent_read"] else "The result is $x^2$.")
        assert payload["scope"]=="comparison"
        return {**judgment(),"candidate_index":0,"selection_evidence":"The digit 2 is visibly above x, not below its baseline."}
    result=assist.assist_part(source,original,call,review_scope="regions")
    assert "status" not in result and result["text"]=="The result is $x^2$."
    item=result["document_reading"]["vision_assistance"]["items"][0]
    assert item["independent_readings_agree"] is False and item["visual_reviews"][0]["selected_candidate_sha256"]==assist.digest(result["text"])
    assert result["document_reading"]["evidence_level"]=="model_transcribed_visual_source"


@pytest.mark.parametrize("index",[True,2,None,"0"])
def test_comparison_cannot_select_an_invalid_or_invented_candidate(index):
    with pytest.raises(ValueError,match="invalid_selection"):
        review.validate_comparison({**judgment(),"candidate_index":index,"selection_evidence":"Visible superscript 2 is above the baseline."})


def test_wrong_ref_cannot_replace_another_complete_paragraph(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original["document_reading"]["elements"][0].pop("local_formula_transcription")
    original["document_reading"].pop("formula_review");original.pop("status");original.pop("reason")
    original["text"]+="\n\nAn unrelated whole paragraph."
    def call(payload,*args):
        assert payload["operation"]=="docling_visual_review"  # No OCR of a misbound quotation.
        return judgment("revise",[issue(quote="An unrelated whole paragraph.")])
    result=assist.assist_part(source,original,call,review_scope="pages")
    assert result["text"]==original["text"] and result["status"]=="quarantined"
    assert result["document_reading"]["vision_assistance"]["items"][0]["reason"]=="vision_review_quote_not_owned_by_source_ref"


def test_plain_pdf_reference_is_not_misread_as_a_markdown_link(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original.pop("status");original.pop("reason");original["document_reading"].pop("formula_review")
    original["document_reading"]["elements"][0].pop("local_formula_transcription")
    original["text"]="See [41] (Fig. 3, left), a 34layer network."
    old_images=assist._images
    monkeypatch.setattr(assist,"_images",lambda *args:(old_images(*args)[0],"See[41](Fig.3,left),a34-layernetwork."))
    def call(payload,*args):
        if payload["operation"]=="docling_region_transcription":return transcript("See [41] (Fig. 3, left), a 34-layer network.")
        if payload["scope"]=="page" and "34layer" in payload["candidate"]:
            return judgment("revise",[issue(quote=original["text"],category="text")])
        return judgment()
    result=assist.assist_part(source,original,call,review_scope="pages")
    assert "status" not in result and "34-layer" in result["text"] and "Fig. 3" in result["text"]


def test_rejected_candidate_returns_to_bounded_recognition_with_specific_feedback(evidence):
    source,original=evidence;calls=[]
    def call(payload,*args):
        calls.append(payload)
        if payload["operation"]=="docling_visual_review":
            return judgment("revise",[issue(quote=payload["candidate"])])
        return transcript()
    result=assist.assist_part(source,original,call,review_scope="regions")
    assert result["status"]=="quarantined" and result["text"]==original["text"]
    assert len(calls)==6
    assert all(c.get("review_findings") for c in calls[3:5])


def test_visual_review_never_overrides_a_native_content_conflict(evidence):
    source,original=evidence
    def call(payload,*args):
        assert payload["operation"]!="docling_visual_review"
        return transcript("The result is $y^2$.")
    assert assist.assist_part(source,original,call,review_scope="regions")["status"]=="quarantined"


def test_missing_native_layer_uses_explicit_model_transcription_evidence(evidence,monkeypatch):
    source,original=evidence;old_images=assist._images
    monkeypatch.setattr(assist,"_images",lambda *args:(old_images(*args)[0],""))
    result=assist.assist_part(source,original,lambda payload,*args:
        judgment() if payload["operation"]=="docling_visual_review" else transcript(),review_scope="regions")
    assert "status" not in result
    assert result["document_reading"]["evidence_level"]=="model_transcribed_visual_source"
    assert engine.document_evidence_level(result)=="model_transcribed_visual_source"
    item=result["document_reading"]["vision_assistance"]["items"][0]
    assert item["native_content_checked"] is False
    assert item["verification_basis"]=="visual_consensus_and_review"


def test_complex_borderless_table_can_be_recognized_and_visually_reviewed(evidence,monkeypatch):
    source,original=evidence
    original["text"]="| Item | x2 |\n| --- | --- |\n"
    e=original["document_reading"]["elements"][0]
    e.update(item_ref="#/tables/0",label="table")
    original["document_reading"]["page_export"]={"reading_order":{"elements":[{"ref":"#/tables/0","label":"table"}]}}
    original["document_reading"]["formula_review"]["undecoded_formula_refs"]=["#/tables/0"]
    old_images=assist._images
    monkeypatch.setattr(assist,"_images",lambda *args:(old_images(*args)[0],"Item x2"))
    def no_grid(*args):raise ValueError("vision_assist_native_grid_unavailable")
    monkeypatch.setattr(assist,"native_table_grid",no_grid)
    value={"kind":"table","rows":1,"columns":2,"uncertain":False,"cells":[
        {"row":0,"column":0,"row_span":1,"col_span":1,"text":"Item"},
        {"row":0,"column":1,"row_span":1,"col_span":1,"text":"$x^2$"}]}
    def call(payload,*args):return judgment() if payload["operation"]=="docling_visual_review" else value
    result=assist.assist_part(source,original,call,review_scope="regions")
    assert "status" not in result and "<table>" in result["text"]
    item=result["document_reading"]["vision_assistance"]["items"][0]
    assert "source_grid" not in item and item["visual_reviews"][0]["verdict"]=="pass"
    assert result["document_reading"]["evidence_level"]=="model_transcribed_visual_source"
    assert assist.assist_part(source,original,call)["status"]=="quarantined"  # Saved strict mode stays strict.


def test_clean_page_can_be_reviewed_without_recognition(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original.pop("status");original.pop("reason");original["document_reading"].pop("formula_review")
    original["document_reading"]["elements"][0].pop("local_formula_transcription")
    calls=[]
    def call(payload,*args):calls.append(payload);return judgment()
    result=assist.assist_part(source,original,call,review_scope="pages")
    assert len(calls)==1 and calls[0]["scope"]=="page" and result["text"]==original["text"]
    assert "status" not in result
    assert result["document_reading"]["visual_review"]["final"]["candidate_sha256"]==assist.digest(original["text"])


def test_page_review_discovers_an_unflagged_paragraph_and_rechecks_final_text(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original.pop("status");original.pop("reason");original["document_reading"].pop("formula_review")
    original["document_reading"]["elements"][0].pop("local_formula_transcription")
    original["text"]+="\n\nUnchanged tail."
    before=deepcopy(original);calls=[]
    def call(payload,*args):
        calls.append(payload)
        if payload["operation"]=="docling_region_transcription":return transcript()
        if payload["scope"]=="page" and "$" not in payload["candidate"]:return judgment("revise",[issue()])
        return judgment()
    result=assist.assist_part(source,original,call,review_scope="pages")
    assert original==before and result["text"]=="The result is $x^{2}$.\n\nUnchanged tail."
    assert len(calls)==5 and "status" not in result
    audit=result["document_reading"]["visual_review"]
    assert audit["initial"]["verdict"]=="revise" and audit["final"]["verdict"]=="pass"


def test_later_page_pass_cannot_erase_an_unhandled_ordering_issue(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    def call(payload,*args):
        if payload["operation"]=="docling_region_transcription":return transcript()
        if payload["scope"]=="page" and "$" not in payload["candidate"]:
            return judgment("revise",[issue(),issue(ref="page",quote="",category="reading_order")])
        return judgment()
    result=assist.assist_part(source,original,call,review_scope="pages")
    assert result["status"]=="quarantined"
    report=result["document_reading"]["visual_review"]
    assert report["final"]["verdict"]=="pass" and len(report["unresolved_findings"])==1
    assert report["unresolved_findings"][0]["category"]=="reading_order"


@pytest.mark.parametrize("rounds",[1,2])
def test_final_audit_new_finding_enters_a_bounded_second_cycle(evidence,monkeypatch,rounds):
    source,original=evidence;page_geometry(monkeypatch)
    original["text"]+="\n\nThe network has 34layers."
    original["document_reading"]["elements"].append({"item_ref":"#/texts/1","label":"text",
        "source_text":"The network has 34layers.","provenance":{"bbox":{"l":20,"b":1,"r":30,"t":10}}})
    old_images=assist._images
    monkeypatch.setattr(assist,"_images",lambda source,page,box:(old_images(source,page,box)[0],
        "The network has 34layers." if box["l"]==20 else "The result is x2."))
    calls=[]
    def call(payload,*args):
        calls.append(payload)
        if payload["operation"]=="docling_region_transcription":
            return transcript("The network has 34-layers." if payload["region_ref"]=="#/texts/1" else "The result is $x^2$.")
        if payload["scope"]=="page" and "$" in payload["candidate"] and "34layers" in payload["candidate"]:
            return judgment("revise",[issue(ref="#/texts/1",quote="The network has 34layers.",category="text")])
        return judgment()
    result=assist.assist_part(source,original,call,review_scope="pages",max_rounds=rounds)
    audit=result["document_reading"]["visual_review"]
    assert audit["correction_cycles"]==rounds and len(audit["history"])==rounds+1
    assert result["document_reading"]["vision_assistance"]["original_text"]==original["text"]
    assert sum(c.get("region_ref")=="#/texts/0" for c in calls)==2
    if rounds==2:
        assert "status" not in result and "34-layers" in result["text"]
        assert len(result["document_reading"]["vision_assistance"]["items"])==2
        assert not audit["unresolved_findings"]
    else:
        assert result["status"]=="quarantined" and audit["unresolved_findings"][0]["ref"]=="#/texts/1"


def test_resumed_review_does_not_forget_previous_unsupported_findings(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original["reason"]="document_vision_review_required"
    original["document_reading"]["visual_review"]={"unresolved_findings":[issue(ref="page",category="reading_order",quote="")]}
    result=assist.assist_part(source,original,lambda payload,*args:
        judgment() if payload["operation"]=="docling_visual_review" else transcript(),review_scope="pages")
    assert result["status"]=="quarantined"
    assert result["document_reading"]["visual_review"]["unresolved_findings"][0]["category"]=="reading_order"


def test_resume_binds_remaining_formula_markers_without_repeating_adopted_regions(evidence):
    source,original=evidence
    original["text"]="<!-- formula-not-decoded -->\n\n<!-- formula-not-decoded -->\n\n"
    first=original["document_reading"]["elements"][0]
    first.update(label="formula",undecoded_formula={"original_text":"The result is x2."})
    first.pop("local_formula_transcription")
    second=deepcopy(first);second["item_ref"]="#/texts/1"
    original["document_reading"]["elements"].append(second)
    original["document_reading"]["formula_review"]["undecoded_formula_refs"].append(second["item_ref"])
    original["document_reading"]["page_export"]={"reading_order":{"elements":[
        {"ref":e["item_ref"],"label":"formula"} for e in [first,second]]}}
    def partial(payload,*args):
        if payload["operation"]=="docling_visual_review":return judgment()
        return transcript() if payload["region_ref"]==first["item_ref"] else {"kind":"text","uncertain":True}
    interim=assist.assist_part(source,original,partial,review_scope="regions",max_rounds=1)
    assert interim["text"].count("<!-- formula-not-decoded -->")==1 and interim["status"]=="quarantined"
    def resumed(payload,*args):
        if payload["operation"]=="docling_visual_review":return judgment()
        assert payload["region_ref"]==second["item_ref"]
        return transcript()
    result=assist.assist_part(source,interim,resumed,review_scope="regions",max_rounds=1)
    assert "status" not in result and "<!-- formula-not-decoded -->" not in result["text"]
    evidence=result["document_reading"]["vision_assistance"]
    assert not evidence["remaining_refs"] and len(evidence["items"])==2 and evidence["original_text"]==original["text"]


@pytest.mark.parametrize("rounds",[0,3,True])
def test_invalid_bound_is_rejected_before_any_api(evidence,rounds):
    source,original=evidence
    def no_api(*args):pytest.fail("invalid bounds must not reach transport")
    with pytest.raises(ValueError,match="invalid_vision_assist_rounds"):
        assist.assist_part(source,original,no_api,review_scope="pages",max_rounds=rounds)


def test_known_unresolved_source_refs_survive_a_visual_pass(evidence,monkeypatch):
    source,original=evidence;page_geometry(monkeypatch)
    original["document_reading"]["formula_review"]["undecoded_formula_refs"].append("#/texts/unresolved")
    result=assist.assist_part(source,original,lambda payload,*args:
        judgment() if payload["operation"]=="docling_visual_review" else transcript(),review_scope="pages")
    assert result["status"]=="quarantined"
    assert result["document_reading"]["vision_assistance"]["remaining_refs"]==["#/texts/unresolved"]


@pytest.mark.parametrize("mode",["uncertain","unknown_ref","global_order"])
def test_unresolved_page_review_never_becomes_eligible(evidence,monkeypatch,mode):
    source,original=evidence;page_geometry(monkeypatch)
    original.pop("status");original.pop("reason");original["document_reading"].pop("formula_review")
    original["document_reading"]["elements"][0].pop("local_formula_transcription")
    value=judgment("uncertain") if mode=="uncertain" else judgment("revise",[
        issue(ref="page" if mode=="global_order" else "invented",quote="",category="reading_order")])
    result=assist.assist_part(source,original,lambda *args:value,review_scope="pages")
    assert result["status"]=="quarantined" and result["reason"]=="document_vision_review_required"
    assert result["text"]==original["text"]


def test_workflow_review_uses_existing_model_role_and_persistent_cache(tmp_path,local_config,monkeypatch):
    vision_config(tmp_path);source=text_pdf(tmp_path,pages=(PAGE_ONE,));original=part(source)
    parser={**local_config,"assistance":{"enabled":True,"binding":vision_parser()["binding"],"review_scope":"regions"}}
    monkeypatch.setattr(adapter,"docling_parts",lambda *args,**kwargs:iter([original]))
    # Valid raster for the real workflow message transport; no model API.
    from PIL import Image
    import io,base64
    stream=io.BytesIO();Image.new("RGB",(10,10)).save(stream,format="PNG")
    image="data:image/png;base64,"+base64.b64encode(stream.getvalue()).decode()
    monkeypatch.setattr(assist,"_images",lambda *args:([
        {"image":image,"sha256":str(i),"size":[10,10]} for i in range(2)],"The result is x2."))
    class Reader:
        model="writer"
        def __init__(self):self.calls=[];self.usage={"calls":0}
        def chat(self,messages,**kwargs):
            self.calls.append(messages);self.usage["calls"]+=1
            reviewing="You audit a document transcription" in messages[0]["content"]
            assert len([b for b in messages[1]["content"] if b["type"]=="image_url"])==2
            return json.dumps(judgment() if reviewing else transcript())
    client=Reader();output=tmp_path/"output"
    for _ in range(2):
        identity=engine.create_run(output,sources=[source],targets=["cpt"],settings_root=tmp_path,document_parser=parser)
        run=engine.Workflow(output,identity,tmp_path,generator=client)
        rows=list(run.iter_source_units(run.recipe["sources"][0]))
        assert rows and rows[0]["status"]=="ready"
        assert rows[0]["document_reading"]["vision_assistance"]["review_scope"]=="regions"
    assert len(client.calls)==3


@pytest.mark.parametrize("scope",["unknown",True,[],{}])
def test_invalid_review_scope_is_rejected(local_config,scope):
    with pytest.raises(ValueError,match="invalid_vision_review_scope"):
        validate_document_parser({**local_config,"assistance":{
            "enabled":True,"binding":vision_parser()["binding"],"review_scope":scope}})
