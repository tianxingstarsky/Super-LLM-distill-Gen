"""Project native, uniquely overlapping body labels into their figure export.

Docling owns the source graph and provenance. This projection changes only
serialization; nearby prose and ambiguous picture boundaries are never moved.
"""
from __future__ import annotations

import hashlib
import math


def label_candidates(document):
    """Find small body boxes predominantly inside exactly one source picture."""
    from .docling_picture_structure import _label, _bounds
    body=[r.cref for r in document.body.children]
    satellites={r.cref for collection in (document.pictures,document.tables)
                for owner in collection for role in ("captions","footnotes") for r in getattr(owner,role,[])}
    candidates=[]
    for item in document.texts:
        if (_label(item)!="text" or item.children or not item.text.strip() or item.text!=item.orig
                or item.parent is None or item.parent.cref!=document.body.self_ref or body.count(item.self_ref)!=1
                or item.self_ref in satellites or len(item.prov)!=1
                or getattr(item.content_layer,"value",item.content_layer)!="body"):
            continue
        p=item.prov[0];page=document.pages.get(p.page_no)
        if page is None or page.size is None or list(p.charspan)!=[0,len(item.text)]:continue
        try:
            b=_bounds(p.bbox.to_bottom_left_origin(page.size.height).model_dump(mode="json"))
            height=b[3]-b[1]
            if not 0<height<=24:continue
            hits=[]
            for picture in document.pictures:
                for ordinal,prov in enumerate(picture.prov):
                    if prov.page_no!=p.page_no:continue
                    r=_bounds(prov.bbox.to_bottom_left_origin(page.size.height).model_dump(mode="json"))
                    overlap=max(0,min(r[2],b[2])-max(r[0],b[0]))*max(0,min(r[3],b[3])-max(r[1],b[1]))
                    if (r[0]<=(b[0]+b[2])/2<=r[2] and r[1]<=(b[1]+b[3])/2<=r[3]
                            and overlap>=.5*(b[2]-b[0])*height
                            and max(r[0]-b[0],b[2]-r[2],r[1]-b[1],b[3]-r[3])<=height/2):
                        hits.append((picture,ordinal))
            if len(hits)==1:candidates.append((item,*hits[0]))
        except (AttributeError,KeyError,TypeError,ValueError):continue
    return candidates


def associate_picture_labels(document,mapping):
    """Require a whole, visible native line before accepting an export owner."""
    from .docling_picture_structure import _bounds,_contains,_compact
    result=[]
    for item,picture,ordinal in label_candidates(document):
        record=mapping.get("items",{}).get(item.self_ref,{})
        try:
            if (record.get("review_required") or not record.get("exact_source_partition")
                    or record.get("item_ref")!=item.self_ref or record.get("label")!="text"
                    or record.get("original_parent_ref")!=item.parent.cref
                    or record.get("source_provenance")!=[p.model_dump(mode="json") for p in item.prov]
                    or record.get("source_text")!=item.text or record.get("trailing_separator")!=""
                    or record.get("source_text_sha256")!=hashlib.sha256(item.text.encode()).hexdigest()
                    or len(record.get("segments",[]))!=1):continue
            segment=record["segments"][0];line=segment["native_line"];p=item.prov[0]
            box=p.bbox.to_bottom_left_origin(document.pages[p.page_no].size.height)
            if (not segment.get("display") or segment.get("text")!=item.text or segment.get("separator_before")!=""
                    or segment.get("source_charspan")!=[0,len(item.text)]
                    or record.get("page_no")!=p.page_no or line.get("page_no")!=p.page_no
                    or line.get("source_sha256")!=mapping.get("source_sha256") or not mapping.get("source_sha256")
                    or not line.get("coordinate_space_supported") or line.get("from_ocr")
                    or not line.get("native_character_sequence_verified") or line.get("rendering_mode") in (3,7)
                    or not math.isfinite(line.get("angle",float("nan"))) or abs(line["angle"])>.01
                    or type(line.get("line_index")) is not int or line["line_index"]<0
                    or line.get("line_id")!=f"page-{p.page_no}:line-{line['line_index']}"
                    or not line.get("native_char_indices")
                    or any(type(n) is not int or n<0 for n in line["native_char_indices"])
                    or line["native_char_indices"]!=sorted(set(line["native_char_indices"]))
                    or _compact(line["native_text"])!=_compact(item.text)
                    or not _contains(_bounds(box.model_dump(mode="json")),_bounds(line["bbox"]))):continue
            result.append({"label_ref":item.self_ref,"picture_ref":picture.self_ref,"provenance_index":ordinal,
                "page_no":p.page_no,"source_sha256":mapping["source_sha256"],
                "source_text_sha256":record["source_text_sha256"],"native_line_id":line["line_id"],
                "source_provenance":record["source_provenance"],
                "picture_provenance":[p.model_dump(mode="json") for p in picture.prov],
                "original_parent_ref":item.parent.cref,"source_graph_modified":False,
                "association":"native_line_inside_unique_picture","semantic_relations_inferred":False})
        except (AttributeError,KeyError,TypeError,ValueError):continue
    return result


def validated_picture_labels(document,mapping,*,page_no=None):
    if not mapping:return []
    proposed=mapping.get("spatial_label_associations",[])
    declared={ref:v.get("external_label_refs",[]) for ref,v in mapping.get("pictures",{}).items() if v.get("external_label_refs")}
    if not proposed:
        if declared:raise ValueError("figure_label_association_binding_changed")
        return []
    from .docling_picture_structure import PICTURE_STRUCTURE_POLICY
    if mapping.get("policy")!=PICTURE_STRUCTURE_POLICY or proposed!=associate_picture_labels(document,mapping):
        raise ValueError("figure_label_association_binding_changed")
    expected={}
    for entry in proposed:expected.setdefault(entry["picture_ref"],[]).append(entry["label_ref"])
    if declared!=expected:raise ValueError("figure_label_association_binding_changed")
    return [a for a in proposed if page_no is None or a["page_no"]==page_no]
