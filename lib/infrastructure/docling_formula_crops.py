"""Local formula crops using the official Pix2Text MFR ONNX inference path.

No paper-specific substitutions or expected answers enter transcription.
The crop and raw prediction remain evidence, never a correctness certificate.
"""
from pathlib import Path
import hashlib
import io
from collections import Counter
import unicodedata
FORMULA_DIRECTORY = "breezedeus--pix2text-mfr-1.5"
FORMULA_REVISION = "1cef9f0bdcd6a4c63df7de1311fb0894593340cc"
FORMULA_POLICY = "pix2text-mfr-1.5-crop-v2"


def numeric_glyphs_match(source, transcription):
    """A rejection check for changed digits, not mathematical validation.

    PDF text order can interleave superscripts, so compare the digit multiset.
    This cannot establish order, exponent attachment or logical equivalence.
    """
    def digits(text):
        return Counter(unicodedata.digit(char) for char in text if char.isdigit())
    return digits(source) == digits(transcription)


def prediction_record(text, raw, token_ids, *, eos_token_id, unk_token_id=None):
    """Preserve incomplete predictions as evidence; never publish their LaTeX."""
    result = {"raw_output": raw, "transcribed_text": text, "tokens": len(token_ids),
              "status": "transcribed_unverified"}
    eos = eos_token_id if isinstance(eos_token_id, (list, tuple)) else [eos_token_id]
    if not token_ids or token_ids[-1] not in eos:
        result.update(status="review_required", reason="document_docling_formula_truncated")
    elif not isinstance(text, str) or not text.strip() or (unk_token_id is not None and unk_token_id in token_ids):
        result.update(status="review_required", reason="document_docling_formula_undecoded")
    return result


class LocalFormulaReader:
    """Official Pix2Text pure-formula route, strictly local ONNX assets."""

    def __init__(self, artifacts_path):
        import torch
        import onnxruntime as ort
        from transformers import TrOCRProcessor
        from optimum.onnxruntime import ORTModelForVision2Seq
        torch.set_num_threads(2)
        model_path = Path(artifacts_path) / FORMULA_DIRECTORY
        self.processor = TrOCRProcessor.from_pretrained(model_path, local_files_only=True,
                                                        trust_remote_code=False, use_fast=False)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.model = ORTModelForVision2Seq.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=False, use_cache=False,
            provider="CPUExecutionProvider", session_options=options,
        )

    def read(self, image):
        import torch
        inputs = self.processor(images=image, return_tensors="pt")
        with torch.inference_mode():
            output = self.model.generate(inputs.pixel_values, max_new_tokens=768, max_time=60, do_sample=False)
        raw = self.processor.batch_decode(output, skip_special_tokens=False)[0]
        text = self.processor.batch_decode(output, skip_special_tokens=True)[0]
        return prediction_record(text, raw, output[0].tolist(), eos_token_id=self.model.generation_config.eos_token_id,
                                 unk_token_id=self.processor.tokenizer.unk_token_id)


def render_formula_crop(pdf, page_number, bbox, *, dpi=240, padding_points=2, clip_bbox=None):
    from docling.backend.pypdfium2_backend import PyPdfiumPageBackend
    from docling_core.types.doc import BoundingBox, CoordOrigin
    if type(page_number) is not int or not 1 <= page_number <= len(pdf):
        raise ValueError("document_docling_invalid_provenance")
    backend = PyPdfiumPageBackend(pdf, "local-formula-crop", page_number - 1)
    try:
        size = backend.get_size()
        source = BoundingBox.model_validate(bbox).to_bottom_left_origin(size.height)
        if source.width <= 0 or source.height <= 0:
            raise ValueError("document_docling_invalid_provenance")
        crop = BoundingBox(l=max(0, source.l-padding_points), b=max(0, source.b-padding_points),
                           r=min(size.width, source.r+padding_points), t=min(size.height, source.t+padding_points),
                           coord_origin=CoordOrigin.BOTTOMLEFT)
        if clip_bbox is not None:
            limit = BoundingBox.model_validate(clip_bbox).to_bottom_left_origin(size.height)
            crop = BoundingBox(l=max(crop.l,limit.l), b=max(crop.b,limit.b),
                               r=min(crop.r,limit.r), t=min(crop.t,limit.t), coord_origin=CoordOrigin.BOTTOMLEFT)
        if crop.width <= 0 or crop.height <= 0 or crop.width * crop.height * (dpi/72)**2 > 20_000_000:
            raise ValueError("document_docling_invalid_provenance")
        image = backend.get_page_image(scale=dpi/72, cropbox=crop).convert("RGB")
    finally:
        backend.unload()
    data = io.BytesIO()
    image.save(data, format="PNG")
    return image, {"page_no": page_number, "source_bbox": bbox, "crop_bbox": crop.model_dump(mode="json"),
                   "dpi": dpi, "padding_points": padding_points, "pixel_size": list(image.size),
                   "image_sha256": hashlib.sha256(data.getvalue()).hexdigest(), "clip_bbox": clip_bbox}


def enrich_formulas(document, source_path, artifacts_path):
    import pypdfium2 as pdfium

    formulas = [item for item in document.texts if getattr(item.label, "value", item.label) == "formula"]
    if not formulas and not document.tables:
        return {}
    model = LocalFormulaReader(artifacts_path)
    evidence = {}
    pdf = pdfium.PdfDocument(source_path)
    try:
        for item in formulas:
            original = item.text
            if not item.prov:
                # No page can own this review evidence. Reject the conversion
                # rather than silently dropping an orphan formula on export.
                raise ValueError("document_docling_invalid_provenance")
            if len(item.prov) != 1:
                evidence[item.self_ref] = {
                    "status": "review_required", "reason": "document_docling_invalid_provenance",
                    "policy": FORMULA_POLICY, "backend_text": original,
                }
                continue
            prov = item.prov[0]
            try:
                image, crop = render_formula_crop(pdf, prov.page_no, prov.bbox.model_dump(mode="json"))
            except (ValueError, RuntimeError, OSError):
                # A local rendering failure must not discard unaffected pages.
                # Model initialization/inference failures remain fatal.
                evidence[item.self_ref] = {
                    "status": "review_required", "reason": "document_docling_formula_crop_failed",
                    "page_no": prov.page_no, "source_bbox": prov.bbox.model_dump(mode="json"),
                    "policy": FORMULA_POLICY, "backend_text": original,
                }
                continue
            prediction = model.read(image)
            if prediction["status"] == "transcribed_unverified":
                item.text = prediction["transcribed_text"]
            evidence[item.self_ref] = {**crop, **prediction, "model": "Pix2Text MFR 1.5",
                                      "policy": FORMULA_POLICY, "backend_text": original}
        from lib.infrastructure.docling_math_regions import extract_pdf_page_glyphs, inspect_math_region
        from lib.infrastructure.docling_math_cell_crops import render_math_cell_crop
        pages = {}
        for table in document.tables:
            if not table.prov:
                raise ValueError("document_docling_invalid_provenance")
            if len(table.prov) != 1:
                evidence[table.self_ref] = {
                    "kind": "table_math", "status": "review_required",
                    "reason": "document_docling_invalid_provenance", "policy": FORMULA_POLICY,
                }
                continue
            number = table.prov[0].page_no
            if number not in pages:
                pages[number] = extract_pdf_page_glyphs(pdf, number)
            cells = []
            for cell in table.data.table_cells:
                if not cell.text.strip() or cell.bbox is None:
                    continue
                selection = inspect_math_region(pages[number], cell.bbox)
                original = cell.text
                cell_info = {"backend_text": original, "selection": selection, "page_no": number,
                             "row": cell.start_row_offset_idx, "column": cell.start_col_offset_idx}
                if not selection["contains_math"]:
                    if selection.get("review_required"):
                        cells.append({**cell_info, "status": "review_required",
                                      "reason": selection.get("reason", "ambiguous_math_region")})
                    continue
                try:
                    image, crop = render_math_cell_crop(pdf, table, cell)
                except (ValueError, RuntimeError, OSError):
                    cells.append({**cell_info, "status": "review_required",
                                  "reason": "document_docling_formula_crop_failed"})
                    continue
                prediction = model.read(image)
                if (prediction["status"] == "transcribed_unverified"
                        and not numeric_glyphs_match(original, prediction["transcribed_text"])):
                    prediction.update(status="review_required", reason="numeric_glyphs_changed")
                if prediction["status"] == "transcribed_unverified":
                    cell.text = "$" + prediction["transcribed_text"] + "$"
                cells.append({**crop, **prediction, **cell_info})
            if cells:
                evidence[table.self_ref] = {"kind":"table_math", "model":"Pix2Text MFR 1.5",
                    "policy":FORMULA_POLICY, "cells":cells,
                    "status":"review_required" if any(c['status']=='review_required' for c in cells)
                             else "transcribed_unverified"}
    finally:
        pdf.close()
    return evidence
