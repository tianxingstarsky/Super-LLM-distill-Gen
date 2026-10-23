"""Figure scope, duplicate labels and source ownership must survive export."""
from copy import deepcopy
import hashlib

import pytest
from docling_core.types.doc import DoclingDocument,DocItemLabel,ProvenanceItem,BoundingBox,Size
from docling_core.transforms.serializer.markdown import MarkdownParams
from lib.infrastructure.docling_picture_structure import PICTURE_STRUCTURE_POLICY,partition_source_lines
from lib.infrastructure.docling_picture_labels import label_candidates,associate_picture_labels,validated_picture_labels
from lib.infrastructure.docling_picture_export import serialize_picture_page
from test_docling_picture_structure import line,box,DIGEST


def fixture():
    doc=DoclingDocument(name="Native plot labels")
    doc.add_page(page_no=1,size=Size(width=400,height=400))
    def prov(b,text=""):return ProvenanceItem(page_no=1,bbox=BoundingBox(**b),charspan=(0,len(text)))
    pic=doc.add_picture(prov=prov(box(40,100,360,300)))
    cap=doc.add_text(label=DocItemLabel.CAPTION,text="Original figure caption.",parent=pic,
                     prov=prov(box(40,70,360,90),"Original figure caption."))
    pic.captions.append(cap.get_ref())
    labels=[doc.add_text(label=DocItemLabel.TEXT,text="iteration",prov=prov(box(x,98,x+35,106),"iteration")) for x in (100,260)]
    outside=doc.add_text(label=DocItemLabel.TEXT,text="Unrelated body text.",prov=prov(box(40,30,350,44),"Unrelated body text."))
    mapping={"policy":PICTURE_STRUCTURE_POLICY,"source_sha256":DIGEST,"items":{},"pictures":{pic.self_ref:{}}}
    for n,item in enumerate(labels):
        bbox=item.prov[0].bbox.model_dump(mode="json");native=line(n,item.text,bbox=bbox)
        mapping["items"][item.self_ref]={**partition_source_lines(item.text,bbox,[native],page_no=1,source_sha256=DIGEST),
            "item_ref":item.self_ref,"label":"text","original_parent_ref":"#/body","page_no":1,"review_required":False,
            "source_provenance":[p.model_dump(mode="json") for p in item.prov]}
    associations=associate_picture_labels(doc,mapping)
    mapping["spatial_label_associations"]=associations
    mapping["pictures"][pic.self_ref]["external_label_refs"]=[a["label_ref"] for a in associations]
    return doc,mapping,pic,cap,labels,outside


def test_repeated_label_text_keeps_distinct_native_refs_without_changing_source_graph():
    doc,mapping,pic,cap,labels,outside=fixture();before=doc.model_dump_json()
    entries=validated_picture_labels(doc,mapping)
    assert [a['label_ref'] for a in entries]==[i.self_ref for i in labels]
    assert len({a['native_line_id'] for a in entries})==2
    assert all(a['picture_ref']==pic.self_ref for a in entries)
    assert outside.self_ref not in [a['label_ref'] for a in entries]
    assert doc.model_dump_json()==before and all(i.parent.cref=='#/body' for i in labels)


@pytest.mark.skipif('traverse_pictures' not in MarkdownParams.model_fields,reason='modern official serializer required')
def test_axis_labels_are_inside_figure_before_caption_once_each():
    doc,mapping,pic,cap,labels,outside=fixture();before=doc.model_dump_json()
    markdown,audit=serialize_picture_page(doc,1,picture_structure=mapping)
    assert markdown.count('> iteration')==2 and markdown.index('> iteration')<markdown.index(cap.text)
    assert markdown.count('iteration')==2 and markdown.index(cap.text)<markdown.index(outside.text)
    assert len(audit['spatial_label_projection_events'])==2
    assert all(e['render_count']==1 for e in audit['spatial_label_projection_events'])
    assert not audit['duplicate_span_refs'] and doc.model_dump_json()==before


@pytest.mark.parametrize('change',['text','source','hidden','ocr','chars','page','quote','geometry','two_pictures','provenance'])
def test_uncertain_ownership_cannot_move_a_body_item(change):
    doc,mapping,pic,cap,labels,_=fixture();item=labels[0];plan=mapping['items'][item.self_ref]
    segment=plan['segments'][0];native=segment['native_line']
    if change=='text':item.text+=' changed'
    elif change=='source':native['source_sha256']='b'*64
    elif change=='hidden':segment['display']=False
    elif change=='ocr':native['from_ocr']=True
    elif change=='chars':native['native_character_sequence_verified']=False
    elif change=='page':native['page_no']=2
    elif change=='quote':segment['text']='another label'
    elif change=='geometry':native['bbox']['l']-=50
    elif change=='two_pictures':doc.add_picture(prov=deepcopy(pic.prov[0]))
    else:plan['source_provenance'][0]['charspan'][1]-=1
    assert item.self_ref not in [a['label_ref'] for a in associate_picture_labels(doc,mapping)]
    with pytest.raises(ValueError,match='binding_changed'):validated_picture_labels(doc,mapping)


@pytest.mark.skipif('traverse_pictures' not in MarkdownParams.model_fields,reason='modern official serializer required')
def test_valid_source_receipt_cannot_authorize_a_different_export_view():
    doc,mapping,_,_,labels,_=fixture();view=doc.model_copy(deep=True)
    view.texts[int(labels[0].self_ref.rsplit('/',1)[1])].text='tampered'
    with pytest.raises(ValueError,match='invalid_output'):
        serialize_picture_page(view,1,picture_structure=mapping,source_document=doc)
