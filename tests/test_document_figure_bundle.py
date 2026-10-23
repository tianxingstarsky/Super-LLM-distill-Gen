"""Portable image/text pairs must remain honest about missing supervision."""
from copy import deepcopy
import base64
import hashlib
import json
from zipfile import ZipFile

import pytest
from docling_core.types.doc import DocItemLabel,ProvenanceItem,BoundingBox,CoordOrigin
from lib.infrastructure.document_figure_bundle import write_document_figure_bundle
from test_docling_picture_assets import fixture,run


@pytest.fixture(scope='session',autouse=True)
def mock_llm_server():yield


def record(tmp_path,*,caption=True):
    path,doc,picture=fixture(tmp_path)
    if caption:
        text='A red rectangle.'
        cap=doc.add_text(label=DocItemLabel.CAPTION,text=text,parent=picture,prov=ProvenanceItem(page_no=1,
            bbox=BoundingBox(l=20,b=10,r=110,t=20,coord_origin=CoordOrigin.BOTTOMLEFT),charspan=(0,len(text))))
        picture.captions.append(cap.get_ref())
    assets=run(doc,path)[1]
    return {'source_name':'source.pdf','document_reading':{'mode':'docling','source_page':1,
        'source_pdf_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'picture_assets':assets}}


def rows(archive,name):return [json.loads(line) for line in archive.read(name).splitlines()]


def test_original_image_caption_pairs_are_portable_without_synthetic_questions(tmp_path):
    unit=record(tmp_path);asset=unit['document_reading']['picture_assets']['items'][0]
    destination=tmp_path/'source_figures.zip'
    summary=write_document_figure_bundle([unit,deepcopy(unit)],destination)
    assert summary['figures']==summary['image_text_pairs']==summary['image_files']==1
    with ZipFile(destination) as archive:
        pair=rows(archive,'image_text.jsonl')[0]
        assert pair['text']=='A red rectangle.' and 'messages' not in pair
        assert archive.read(pair['image'])==base64.b64decode(asset['image'].split(',')[1])
        meta=rows(archive,'records.jsonl')[0]
        assert meta['sft_validated'] is False and meta['generated'] is False
        assert not rows(archive,'unpaired.jsonl')


def test_captionless_picture_is_an_unpaired_visual_source_not_an_invented_sft_sample(tmp_path):
    unit=record(tmp_path,caption=False);destination=tmp_path/'source_figures.zip'
    summary=write_document_figure_bundle([unit],destination)
    assert summary['image_text_pairs']==0 and summary['unpaired_images']==1
    with ZipFile(destination) as archive:
        assert not rows(archive,'image_text.jsonl')
        unpaired=rows(archive,'unpaired.jsonl')[0]
        assert unpaired['image'] and 'text' not in unpaired and 'messages' not in unpaired


@pytest.mark.parametrize('damage',['sha','size','base64','blank','expanded_failed','caption_sha','caption_ambiguous'])
def test_invalid_images_or_caption_bindings_cannot_enter_paired_training_rows(tmp_path,damage):
    unit=record(tmp_path);asset=unit['document_reading']['picture_assets']['items'][0]
    if damage=='sha':asset['image_sha256']='0'*64
    elif damage=='size':asset['width']+=1
    elif damage=='base64':asset['image']='data:image/png;base64,BROKEN'
    elif damage=='blank':asset['source_region_is_blank_white']=True
    elif damage=='expanded_failed':asset['content_coverage']={'status':'review_required','reason':'size_limit'}
    elif damage=='caption_sha':asset['source_caption']['items'][0]['text_sha256']='0'*64
    else:asset['source_caption']['status']='review_required'
    destination=tmp_path/'source_figures.zip';summary=write_document_figure_bundle([unit],destination)
    assert summary['image_text_pairs']==0 and summary['review_required']==1
    with ZipFile(destination) as archive:assert not rows(archive,'image_text.jsonl')


def test_mixed_source_versions_cannot_silently_replace_an_image_caption_pair(tmp_path):
    unit=record(tmp_path);other=deepcopy(unit)
    other['document_reading']['picture_assets']['items'][0]['source_caption']['text']='changed'
    destination=tmp_path/'source_figures.zip'
    with pytest.raises(ValueError,match='conflicting_source_versions'):
        write_document_figure_bundle([unit,other],destination)
    assert not destination.exists() and not (tmp_path/'.source_figures.zip.pending').exists()


def test_source_hash_binding_is_required_and_cancellation_keeps_previous_complete_export(tmp_path):
    unit=record(tmp_path);destination=tmp_path/'source_figures.zip'
    write_document_figure_bundle([unit],destination);before=destination.read_bytes()
    other=deepcopy(unit);other['document_reading']['source_pdf_sha256']='b'*64
    with pytest.raises(ValueError,match='source_binding_changed'):
        write_document_figure_bundle([other],destination)
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        write_document_figure_bundle([unit],destination,check_cancel=cancel)
    assert destination.read_bytes()==before


def test_no_figures_does_not_create_an_empty_zip(tmp_path):
    destination=tmp_path/'source_figures.zip'
    assert write_document_figure_bundle([{'text':'Only text.'}],destination)['figures']==0
    assert not destination.exists()


def test_source_caption_uses_original_text_when_formula_formatting_changes_parsed_caption(tmp_path):
    path,doc,pic=fixture(tmp_path)
    raw='The ratio is α = 0.05.'
    cap=doc.add_text(label=DocItemLabel.CAPTION,text=raw,parent=pic,
        prov=ProvenanceItem(page_no=1,bbox=BoundingBox(l=20,b=10,r=110,t=20,coord_origin=CoordOrigin.BOTTOMLEFT),
                            charspan=(0,len(raw))))
    pic.captions.append(cap.get_ref());cap.text=r'The ratio is $\alpha = 0.05$.'
    cap.prov[0].charspan=(0,len(cap.text))
    caption=run(doc,path)[1]['items'][0]['source_caption']
    assert caption['status']=='source_linked' and caption['text']==raw
    assert caption['items'][0]['parsed_text']==cap.text and caption['items'][0]['text_origin']=='Docling.orig'


def test_source_figure_corpus_is_visible_in_the_normal_file_catalog():
    from lib.domain.dataset_assets import Asset,output_category,common_asset
    name='workflows/abc/artifacts/source_figures.zip'
    category=output_category(name)
    assert category=='语料' and common_asset(Asset('out','output',name,category,100,1))


def test_multiple_source_captions_do_not_become_an_unreviewed_combined_training_target(tmp_path):
    path,doc,pic=fixture(tmp_path)
    for n in range(2):
        text=f'Figure {n+1}. A separate caption.'
        cap=doc.add_text(label=DocItemLabel.CAPTION,text=text,parent=pic,
            prov=ProvenanceItem(page_no=1,bbox=BoundingBox(l=20,b=10,r=110,t=20,coord_origin=CoordOrigin.BOTTOMLEFT),
                                charspan=(0,len(text))))
        pic.captions.append(cap.get_ref())
    caption=run(doc,path)[1]['items'][0]['source_caption']
    assert caption['status']=='review_required' and len(caption['items'])==2


def test_workflow_publishes_raw_visual_assets_even_without_text_training_rows(tmp_path,monkeypatch):
    from lib.infrastructure import training_workflow as engine
    from test_document_model_parser import configure
    configure(tmp_path)
    raw=record(tmp_path,caption=False)
    source=tmp_path/'source.pdf'
    source_id=hashlib.sha256(source.read_bytes()).hexdigest()
    unit={**raw,'id':'visual-unit','source_id':source_id,'kind':'document','text':'',
          'source_location':{'file':'source.pdf','record':'page:1'},'status':'quarantined','reason':'document_text_requires_vision'}
    monkeypatch.setattr(engine.Workflow,'iter_source_units',lambda *args:iter([unit]))
    def no_api(*args,**kwargs):pytest.fail('Preserving a source figure must not call a model API')
    monkeypatch.setattr(engine.Workflow,'ask',no_api)
    output=tmp_path/'output'
    identity=engine.create_run(output,sources=[source],targets=['cpt'],settings_root=tmp_path)
    run=engine.Workflow(output,identity,tmp_path);run.execute()
    manifest=engine.verify_artifacts(run.path)
    assert manifest['counts']['cpt']==0 and 'source_figures.zip' in manifest['sha256']
    assert run.state['quality']['source_figures']['unpaired_images']==1
    with ZipFile(run.path/'artifacts/source_figures.zip') as archive:
        assert len(rows(archive,'unpaired.jsonl'))==1 and not rows(archive,'image_text.jsonl')
