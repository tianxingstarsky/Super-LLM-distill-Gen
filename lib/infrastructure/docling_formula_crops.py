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


def enrich_formulas(document, source_path, artifacts_path, *, inline_config=None):
    import pypdfium2 as pdfium

    formulas = [item for item in document.texts if getattr(item.label, "value", item.label) == "formula"]
    if not formulas and not document.tables and not inline_config:
        return {}
    model = LocalFormulaReader(artifacts_path)
    evidence = {}
    routes = {}
    if inline_config:
        from .docling_inline_routing import build_inline_route_map
        routes = build_inline_route_map(document, source_path)
    pdf = pdfium.PdfDocument(source_path)
    mixed_context = {}
    try:
        for item in formulas:
            original = item.text
            route = routes.get(item.self_ref, {})
            if not route.get("allow_whole_formula_ocr", True):
                evidence[item.self_ref] = {
                    "status": "review_required", "reason": "mixed_formula_requires_inline_probe",
                    "policy": FORMULA_POLICY, "backend_text": original, "routing": route,
                }
                continue
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
                        if inline_config and selection.get("status") == "mixed_prose_math":
                            from .docling_inline_assembly import read_native_character_pages
                            from .docling_inline_lines import read_native_line_pages
                            from .docling_inline_geometry import render_inline_page_evidence
                            from .docling_table_mixed_math import transcribe_mixed_table_math
                            if "native_chars" not in mixed_context:
                                mixed_context["native_chars"] = read_native_character_pages(source_path)
                                mixed_context["native_lines"] = read_native_line_pages(source_path)
                                mixed_context["pages"] = {}
                            if number not in mixed_context["pages"]:
                                # Keep at most one full RGB page. Native character
                                # and physical-line evidence is small and reusable;
                                # page rasters must not grow with document length.
                                for _, previous_image in mixed_context["pages"].values():
                                    if previous_image is not None:
                                        previous_image.close()
                                mixed_context["pages"].clear()
                                mixed_context["pages"][number] = render_inline_page_evidence(source_path, number)
                            mixed_page, page_image = mixed_context["pages"][number]
                            mixed = transcribe_mixed_table_math(
                                pdf, mixed_page, cell.model_dump(mode="json"),
                                mixed_context["native_lines"].get(number, []),
                                mixed_context["native_chars"].get(number, []), page_image, model)
                            if mixed["status"] == "transcribed_unverified" and not mixed["review_required"]:
                                cell.text = mixed["text"]
                                mixed["published"] = True
                            cells.append({**cell_info, "status": mixed["status"],
                                          "reason": mixed.get("reason"), "mixed_math": mixed})
                            continue
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
        for _, image in mixed_context.get("pages", {}).values():
            if image is not None:
                image.close()
        pdf.close()
    if inline_config:
        from lib.infrastructure.docling_inline_formulas import enrich_inline_formulas
        evidence.update(enrich_inline_formulas(document, source_path, inline_config, model, routes=routes))
    return evidence
