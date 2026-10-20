"""Independent local structure evidence for ambiguous code/math blocks.

Docling's code enrichment takes a preselected ``<code>`` task and therefore
cannot establish that a block was mislabeled. Official Egret D-FINE layout
inference supplies independent positive text regions and a real Code class.
No recognizer's text enters this classification decision or the published text.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from pathlib import Path

from .docling_math_regions import is_math_font


CODE_CLASSIFICATION_POLICY = "egret_pp_native_code_protection_v1"
CODE_STRUCTURE_DIRECTORY = "docling-project--docling-layout-egret-medium"
CODE_STRUCTURE_REVISION = "77ede7cc7bed96d853c58f319734803d6ea2ea5c"
CODE_STRUCTURE_FILES = ("config.json", "model.safetensors", "preprocessor_config.json")
CODE_LAYOUT_PARAMETERS = {"frame": "full_pdf_page", "raw_threshold": .1,
                          "text_threshold": .6, "code_veto_threshold": .3,
                          "pp_text_threshold": .5, "complete_native_glyph_coverage": True}
_CONTEXT_CODE = re.compile(r"\b(?:algorithm|pseudo[\s-]?code|source\s+code|program\s+listing|listing)\b|算法|伪代码|程序清单|代码", re.I)
_FLOW = re.compile(
    r"(?:^|\n)\s*(?:\d+\s*[:.)]\s*)?(?:if|else|elif|for|while|repeat|until|return|yield|function|procedure|begin|end|input|output|require|ensure)\b",
    re.I,
)
_DECLARATION = re.compile(
    r"(?:^|\n)\s*(?:\d+\s*[:.)]\s*)?(?:let|var|const|dim|declare|param(?:eter)?|set|array|matrix|vector|int|float|double|bool|real)\s+\S+\s*(?::=|<-|←|:=|:|=)|"
    r"(?:^|\n)\s*(?:assert|assume|axiom|theorem|lemma|def|class|import|from|SELECT|CREATE|INSERT|UPDATE|DELETE)\b",
    re.I,
)
_CODE_PUNCTUATION = re.compile(r":=|<-|→|←|=>|::|\+\+|--|&&|\|\||[{};`\\#]|/\*|\*/|//|\[\[[^\]]*\]\]")


def _visible(glyphs):
    return [glyph for glyph in glyphs if glyph["text"].strip()]


def _rect(value):
    if not isinstance(value, dict):
        raise ValueError()
    rectangle = tuple(value[key] for key in ("l", "b", "r", "t"))
    if (any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in rectangle)
            or not rectangle[0] < rectangle[2] or not rectangle[1] < rectangle[3]):
        raise ValueError()
    return rectangle


def _center_inside(glyph, rectangle):
    l, b, r, t = _rect(glyph["bbox"])
    return rectangle[0] <= (l + r) / 2 <= rectangle[2] and rectangle[1] <= (b + t) / 2 <= rectangle[3]


def protect_source_code(route, glyphs, backend_text, *, source_context=None):
    """Collect vetoes. Absence of a veto never proves that prose is not code.

    The Python/JSON checks use their real standard-library parsers. Additional
    conservative syntax/context vetoes cover pseudocode and declarative notation
    that need not parse in a programming language. Fonts are a veto only, never
    a positive classifier.
    """
    result = {"protected": True, "reasons": [], "syntax_checks": [],
              "ordinary_prose_runs": [], "ordinary_word_anchors": [], "math_char_indices": []}
    if route.get("original_label") != "code":
        result["reasons"].append("source_is_not_code")
        return result
    language = getattr(route.get("code_language"), "value", route.get("code_language"))
    if language not in (None, "", "unknown"):
        result["reasons"].append("declared_code_language")
    try:
        visible = _visible(glyphs)
        ids = [glyph["char_index"] for glyph in glyphs]
        if (not visible or len(set(ids)) != len(ids) or ids != sorted(ids)
                or any(type(i) is not int or i < 0 for i in ids)):
            raise ValueError()
        for glyph in visible:
            if (type(glyph["font_flags"]) is not int or glyph["font_flags"] < 0
                    or not isinstance(glyph["font_name"], str)
                    or not math.isfinite(glyph["font_size"]) or glyph["font_size"] <= 0):
                raise ValueError()
            _rect(glyph["bbox"])
        if any(glyph["font_flags"] & 1 for glyph in visible):
            result["reasons"].append("fixed_pitch_code")
        # Font descriptor flags may be missing/misreported in some embedded
        # fonts; known monospaced face names provide an additional veto.
        if any(re.search(r"courier|monospace|consolas|dejavusansmono|liberationmono|cmuntt|cmtt|inconsolata|sourcecodepro|firamono|firacode|jetbrainsmono", glyph["font_name"], re.I)
               for glyph in visible):
            result["reasons"].append("monospaced_font_name")
        result["math_char_indices"] = [g["char_index"] for g in visible if is_math_font(g["font_name"])]
        from .docling_inline_routing import _multiword_runs
        result["ordinary_prose_runs"] = _multiword_runs(glyphs)
        ordinary = "".join(g["text"] if not is_math_font(g["font_name"]) else "\0" for g in glyphs)
        result["ordinary_word_anchors"] = re.findall(r"[A-Za-z]{2,}", ordinary)
        if not result["math_char_indices"]:
            result["reasons"].append("no_established_math_font")
        if not result["ordinary_prose_runs"]:
            result["reasons"].append("no_native_multiword_prose")
    except (KeyError, TypeError, ValueError):
        result["reasons"].append("invalid_native_code_evidence")
    if not isinstance(backend_text, str) or not backend_text.strip():
        result["reasons"].append("missing_source_text")
    else:
        text = backend_text.strip()
        try:
            tree = ast.parse(text)
            if tree.body:
                result["syntax_checks"].append("python_ast_parsed")
                result["reasons"].append("valid_python_syntax")
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            pass
        try:
            json.loads(text)
            result["syntax_checks"].append("json_parsed")
            result["reasons"].append("valid_json_syntax")
        except (ValueError, RecursionError):
            pass
        if _FLOW.search(text) or _DECLARATION.search(text):
            result["reasons"].append("code_or_pseudocode_control_syntax")
        if _CODE_PUNCTUATION.search(text):
            result["reasons"].append("code_or_declarative_punctuation")
        if re.search(r"(?:^|\n)\s*\d+\s*[:.)]\s*\S", text):
            result["reasons"].append("numbered_code_lines")
        if re.search(r"\n[ \t]{2,}\S", backend_text):
            result["reasons"].append("indented_source_lines")
    if source_context is not None:
        if not isinstance(source_context, str):
            result["reasons"].append("invalid_source_context")
        elif _CONTEXT_CODE.search(source_context):
            result["reasons"].append("algorithm_or_code_context")
        elif _FLOW.search(source_context) or _DECLARATION.search(source_context) or _CODE_PUNCTUATION.search(source_context):
            result["reasons"].append("surrounding_source_code_syntax")
    else:
        result["reasons"].append("source_block_context_unavailable")
    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    result["protected"] = bool(result["reasons"])
    return result


class LocalCodeLayoutReader:
    """Official Egret object detection, one whole-page call and no generation."""

    def __init__(self, artifacts_path):
        import torch
        from transformers import DFineForObjectDetection, RTDetrImageProcessor
        self.path = Path(artifacts_path) / CODE_STRUCTURE_DIRECTORY
        if any(not (self.path / name).is_file() for name in CODE_STRUCTURE_FILES):
            raise ValueError("document_docling_formula_assets_missing")
        torch.set_num_threads(2)
        digest = hashlib.sha256()
        with (self.path / "model.safetensors").open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        self.model_sha256 = digest.hexdigest()
        self.processor = RTDetrImageProcessor.from_pretrained(
            self.path, local_files_only=True, trust_remote_code=False)
        self.model = DFineForObjectDetection.from_pretrained(
            self.path, local_files_only=True, trust_remote_code=False).eval()

    def read_page(self, image):
        import io
        import torch
        data = io.BytesIO()
        image.save(data, format="PNG")
        inputs = self.processor(images=[image], return_tensors="pt")
        with torch.inference_mode():
            outputs = self.model(**inputs)
        prediction = self.processor.post_process_object_detection(
            outputs, target_sizes=torch.tensor([image.size[::-1]]),
            threshold=CODE_LAYOUT_PARAMETERS["raw_threshold"])[0]
        boxes = [{"label": self.model.config.id2label[label.item()],
                  "label_id": label.item(), "score": score.item(),
                  "coordinate": box.tolist()}
                 for score, label, box in zip(
                     prediction["scores"], prediction["labels"], prediction["boxes"])]
        return {"policy": CODE_CLASSIFICATION_POLICY, "status": "layout_predicted",
                "model": "docling-project/docling-layout-egret-medium",
                "model_revision": CODE_STRUCTURE_REVISION,
                "model_sha256": self.model_sha256,
                "source_image_sha256": hashlib.sha256(data.getvalue()).hexdigest(),
                "image_size": list(image.size), "frame": "full_pdf_page",
                "parameters": dict(CODE_LAYOUT_PARAMETERS), "boxes": boxes,
                "input_shapes": {key: list(value.shape) for key, value in inputs.items()
                                 if hasattr(value, "shape")},
                "local_files_only": True, "device": "cpu",
                "transcription_used_for_publication": False}


def _score(box):
    score = box["score"]
    if (isinstance(score, bool) or not isinstance(score, (float, int))
            or not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError()
    return score


def _map_layout_boxes(prediction, page_proof):
    """Convert original image coordinates to PDF points, without editing boxes."""
    left, bottom, right, top = _rect(page_proof["source_bbox"])
    width, height = page_proof["pixel_size"]
    if not width > 0 or not height > 0:
        raise ValueError()
    mapped = []
    for index, box in enumerate(prediction["boxes"]):
        x1, y1, x2, y2 = box["coordinate"]
        _rect(dict(l=x1, b=y1, r=x2, t=y2))
        _score(box)
        mapped.append(dict(box, source_box_index=index, bbox={
            "l": left + x1 / width * (right - left),
            "r": left + x2 / width * (right - left),
            "t": top - y1 / height * (top - bottom),
            "b": top - y2 / height * (top - bottom), "coord_origin": "BOTTOMLEFT"}))
    return mapped


def assess_code_math_reclassification(route, glyphs, backend_text, *, layout_boxes,
                                     layout_prediction, page_proof,
                                     source_context=None):
    """Propose a label correction with two mature independent layout models.

    Every source glyph must be covered by positive ordinary-text predictions
    from both models. No programming-language/algorithm evidence may conflict.
    This does not bypass the math assembly, raster or object ownership checks.
    """
    protection = protect_source_code(route, glyphs, backend_text, source_context=source_context)
    result = {"policy": CODE_CLASSIFICATION_POLICY, "approved": False, "reasons": [],
              "native_protection": protection, "pp_text_support": [],
              "egret_text_support": [], "layout_prediction": layout_prediction,
              "source_page": page_proof, "transcription_used_for_publication": False}
    if protection["protected"]:
        result["reasons"].extend(protection["reasons"])
        return result
    try:
        frame = _rect(page_proof["source_bbox"])
        target = _rect(route["bbox"])
        if (layout_prediction.get("status") != "layout_predicted"
                or layout_prediction.get("policy") != CODE_CLASSIFICATION_POLICY
                or layout_prediction.get("local_files_only") is not True
                or layout_prediction.get("device") != "cpu"
                or layout_prediction.get("frame") != "full_pdf_page"
                or layout_prediction.get("source_image_sha256") != page_proof.get("image_sha256")
                or layout_prediction.get("image_size") != page_proof.get("pixel_size")
                or layout_prediction.get("parameters") != CODE_LAYOUT_PARAMETERS
                or page_proof.get("page_no") != route.get("page_no")
                or page_proof.get("source_sha256") != route.get("source_sha256")
                or not route.get("source_sha256") or frame[0] != 0 or frame[1] != 0
                or not (frame[0] <= target[0] < target[2] <= frame[2]
                        and frame[1] <= target[1] < target[3] <= frame[3])):
            raise ValueError()
        egret = _map_layout_boxes(layout_prediction, page_proof)
    except (KeyError, TypeError, ValueError):
        result["reasons"].append("unbound_or_invalid_independent_layout_prediction")
        return result
    visible = _visible(glyphs)
    for name, boxes in (("egret", egret), ("pp", layout_boxes)):
        support = []
        try:
            threshold = CODE_LAYOUT_PARAMETERS["text_threshold" if name == "egret" else "pp_text_threshold"]
            for box in boxes:
                score = _score(box)
                rectangle = _rect(box["bbox"])
                if not any(_center_inside(glyph, rectangle) for glyph in visible):
                    continue
                if box["label"] in {"algorithm", "code"} and score >= CODE_LAYOUT_PARAMETERS["code_veto_threshold"]:
                    result["reasons"].append(f"{name}_layout_code_or_algorithm")
                if box["label"] == "text" and score >= threshold:
                    support.append(box)
            missing = [glyph["char_index"] for glyph in visible if not any(
                _center_inside(glyph, _rect(box["bbox"])) for box in support)]
            result[name + "_text_support"] = support
            result[name + "_missing_source_char_indices"] = missing
            if missing:
                result["reasons"].append(f"{name}_incomplete_positive_text_coverage")
        except (KeyError, TypeError, ValueError):
            result["reasons"].append(f"invalid_{name}_layout_evidence")
    result["approved"] = not result["reasons"]
    return result
