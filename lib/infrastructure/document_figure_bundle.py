"""Stream source images into a portable image/text corpus, without invented QA.

Images with a unique original caption are paired pretraining data. Missing or
ambiguous captions remain unpaired/review records. This is not SFT validation.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile,ZIP_DEFLATED,ZIP_STORED

from PIL import Image

POLICY="source_figure_pixels_and_original_caption_v1"


def _image(asset):
    value=asset.get("image","")
    if asset.get("status")!="exported" or not isinstance(value,str) or not value.startswith("data:image/png;base64,"):
        raise ValueError("missing_source_image")
    if len(value)>3_000_000:raise ValueError("source_image_size_limit")
    try:raw=base64.b64decode(value.split(",",1)[1],validate=True)
    except ValueError as error:raise ValueError("invalid_source_image") from error
    if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=asset.get("image_sha256"):
        raise ValueError("source_image_integrity_error")
    try:
        with Image.open(BytesIO(raw)) as im:
            if (im.format!="PNG" or im.size!=(asset.get("width"),asset.get("height"))
                    or im.width*im.height>6_000_000):raise ValueError("source_image_integrity_error")
            im.verify()
    except (OSError,SyntaxError) as error:raise ValueError("invalid_source_image") from error
    return raw


def write_document_figure_bundle(records,destination,*,check_cancel=lambda:None):
    """Bounded image reads, on-disk JSONL spools, dedup across text chunks."""
    destination=Path(destination);pending=destination.with_name("."+destination.name+".pending")
    counts={"figures":0,"image_text_pairs":0,"unpaired_images":0,"review_required":0,"image_files":0}
    seen={};images=set()
    with TemporaryDirectory(prefix="figure-bundle-",dir=destination.parent) as directory:
        work=Path(directory)
        try:
            with ZipFile(pending,"w",compression=ZIP_DEFLATED) as archive, \
                    (work/"records.jsonl").open("w",encoding="utf8") as metadata, \
                    (work/"image_text.jsonl").open("w",encoding="utf8") as paired, \
                    (work/"unpaired.jsonl").open("w",encoding="utf8") as unpaired:
                def emit(handle,value):handle.write(json.dumps(value,ensure_ascii=False,separators=(",",":"))+"\n")
                for unit in records:
                    check_cancel();reading=unit.get("document_reading") or {}
                    source=reading.get("source_pdf_sha256")
                    assets=reading.get("picture_assets") or {}
                    if not assets.get("items"):continue
                    if (reading.get("mode")!="docling" or not isinstance(source,str) or not re.fullmatch(r"[0-9a-f]{64}",source)
                            or assets.get("source_pdf_sha256")!=source or assets.get("page_no")!=reading.get("source_page")):
                        raise ValueError("figure_bundle_source_binding_changed")
                    for asset in assets["items"]:
                        check_cancel()
                        identity=[source,asset.get("source_page"),asset.get("picture_ref"),asset.get("provenance_index")]
                        if (type(identity[1]) is not int or identity[1]!=reading["source_page"] or type(identity[3]) is not int or identity[3]<0
                                or not isinstance(identity[2],str) or not re.fullmatch(r"#/pictures/\d+",identity[2])):
                            raise ValueError("figure_bundle_source_binding_changed")
                        key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                        fingerprint=hashlib.sha256(json.dumps(asset,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                        if key in seen:
                            if seen[key]!=fingerprint:raise ValueError("figure_bundle_conflicting_source_versions")
                            continue
                        seen[key]=fingerprint;counts["figures"]+=1
                        row={"id":key,"type":"source_figure","source_pdf_sha256":source,"source_page":identity[1],
                             "picture_ref":identity[2],"provenance_index":identity[3],"source_bbox":asset.get("source_bbox"),
                             "source_name":unit.get("source_name"),"image":None,"status":"review_required",
                             "sft_validated":False,"generated":False}
                        # A failed expanded crop cannot fall back to a clipped
                        # image and silently claim complete visual content.
                        visual=asset.get("content_coverage",asset)
                        caption=asset.get("source_caption",{})
                        row["source_caption"]=caption
                        try:
                            raw=_image(visual)
                            if visual.get("source_region_is_blank_white"):raise ValueError("blank_source_image")
                            row.update(image="images/"+visual["image_sha256"]+".png",image_sha256=visual["image_sha256"],
                                       crop_bbox=visual.get("crop_bbox",asset.get("source_bbox")))
                            if row["image"] not in images:
                                archive.writestr(row["image"],raw,compress_type=ZIP_STORED);images.add(row["image"]);counts["image_files"]+=1
                            items=caption.get("items",[])
                            if (caption.get("status")=="source_linked" and caption.get("generated") is False
                                    and isinstance(caption.get("text"),str) and caption["text"].strip()
                                    and items and all(i.get("binding_valid") is True and isinstance(i.get("text"),str)
                                        and hashlib.sha256(i["text"].encode()).hexdigest()==i.get("text_sha256") for i in items)
                                    and caption.get("text")==" ".join(i["text"] for i in items)):
                                row.update(status="image_text_pair",text=caption["text"])
                                emit(paired,{k:row[k] for k in ("id","image","text","source_pdf_sha256","source_page","picture_ref")})
                                counts["image_text_pairs"]+=1
                            elif caption.get("status") in {None,"missing"}:
                                row["status"]="unpaired_image";emit(unpaired,row);counts["unpaired_images"]+=1
                            else:row["reason"]="source_caption_binding_requires_review"
                        except ValueError as error:row["reason"]=str(error)
                        if row["status"]=="review_required":counts["review_required"]+=1
                        emit(metadata,row)
                check_cancel()
                for handle in (metadata,paired,unpaired):handle.flush()
                for name in ("records.jsonl","image_text.jsonl","unpaired.jsonl"):archive.write(work/name,name)
                archive.writestr("manifest.json",json.dumps({"policy":POLICY,**counts,
                    "kind":"image_text_pretraining_corpus","questions_generated":False,"sft_validated":False},indent=2))
            if counts["figures"]:pending.replace(destination)
            else:pending.unlink(missing_ok=True)
        except BaseException:
            pending.unlink(missing_ok=True);raise
    return {"policy":POLICY,**counts,**({"file":destination.name} if counts["figures"] else {})}
