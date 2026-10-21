"""Narrow, source-bound proofs of text hidden by PDF clipping.

PDFium's object bounds and clip paths are in the containing Form's coordinates.
Only ancestor Form matrices transform them to page coordinates. This follows
PDFium RenderSingleObject/ProcessForm; applying an object's own matrix to its
already transformed bounds a second time would be incorrect.

This is not a visibility oracle. Only a complete text object's bounds strictly
outside a simple rectangular clip establish ``fully_clipped``. Partial clips,
complex paths, transparency, paint order and normal uncut text remain unknown.
The original text, object path and every clipping proof remain available.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
from pathlib import Path


PDF_TEXT_VISIBILITY_POLICY = "pdfium_inherited_rect_clip_text_visibility_v1"
PDF_TEXT_VISIBILITY_BINDING_POLICY = "whole_native_line_unique_pdfium_object_v1"
_IDENTITY = (1., 0., 0., 1., 0., 0.)
_MAX_DEPTH = 32
_MAX_OBJECTS = 100_000
_MAX_PATHS = 256
_MAX_SEGMENTS = 4096
_MAX_TEXT_BYTES = 1_048_576
_MAX_PAGE_TEXT_BYTES = 16_777_216
_MATCH_TOLERANCE = .25
_SEPARATION_EPSILON = .01


def _matrix(value):
    value = tuple(float(v) for v in value)
    if (len(value) != 6 or not all(math.isfinite(v) for v in value)
            or abs(value[0] * value[3] - value[1] * value[2]) < 1e-12):
        raise ValueError("pdf_text_visibility_invalid_matrix")
    return value


def _compose(outer, inner):
    """Return outer(inner(point)), using PDF's [a b c d e f] convention."""
    a, b, c, d, e, f = _matrix(outer)
    g, h, i, j, k, l = _matrix(inner)
    return _matrix((a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j,
                    a*k+c*l+e, b*k+d*l+f))


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a*x+c*y+e, b*x+d*y+f)


def _box(value):
    if isinstance(value, dict):
        if value.get("coord_origin", "BOTTOMLEFT") != "BOTTOMLEFT":
            raise ValueError("pdf_text_visibility_coordinate_space")
        value = [value[k] for k in ("l", "b", "r", "t")]
    result = tuple(float(v) for v in value)
    if (len(result) != 4 or not all(math.isfinite(v) for v in result)
            or result[0] >= result[2] or result[1] >= result[3]):
        raise ValueError("pdf_text_visibility_invalid_bbox")
    return result


def _box_dict(value):
    return dict(zip(("l", "b", "r", "t"), value), coord_origin="BOTTOMLEFT")


def _transformed_box(box, matrix):
    l, b, r, t = _box(box)
    points = [_point(matrix, p) for p in ((l, b), (l, t), (r, t), (r, b))]
    return _box((min(p[0] for p in points), min(p[1] for p in points),
                 max(p[0] for p in points), max(p[1] for p in points)))


def _rectangle(segments):
    """Accept one exact axis-aligned rectangle, never a path's bounding box."""
    if len(segments) not in (4, 5):
        return None
    if segments[0][2:] != [2, False] or not segments[-1][3]:
        return None
    if any(s[2] != 0 or s[3] for s in segments[1:-1]) or segments[-1][2] != 0:
        return None
    points = [(float(s[0]), float(s[1])) for s in segments]
    if not all(math.isfinite(v) for point in points for v in point):
        return None
    if len(points) == 5:
        if points[-1] != points[0]:
            return None
        points.pop()
    if len(set(points)) != 4:
        return None
    xs, ys = sorted(set(p[0] for p in points)), sorted(set(p[1] for p in points))
    if len(xs) != 2 or len(ys) != 2:
        return None
    if any((a[0] != b[0] and a[1] != b[1]) for a, b in zip(points, points[1:]+points[:1])):
        return None
    return _box((xs[0], ys[0], xs[1], ys[1]))


def _classify(bounds, clips):
    """AABB separation is conservative even under a rotated/sheared Form CTM."""
    if any(clip["kind"] == "complex_or_unavailable" for clip in clips):
        return "unknown", None
    l, b, r, t = _box(bounds)
    for clip in clips:
        if clip["kind"] != "rectangle":
            continue
        cl, cb, cr, ct = _box(clip["page_bbox"])
        if (l > cr + _SEPARATION_EPSILON or r < cl - _SEPARATION_EPSILON
                or b > ct + _SEPARATION_EPSILON or t < cb - _SEPARATION_EPSILON):
            return "fully_clipped", clip
    return "unknown", None


def _object_matrix(raw, obj):
    matrix = raw.FS_MATRIX()
    if not raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix)):
        raise ValueError("pdf_text_visibility_matrix_unavailable")
    return _matrix(getattr(matrix, name) for name in ("a", "b", "c", "d", "e", "f"))


def _object_bounds(raw, obj):
    values = [ctypes.c_float() for _ in range(4)]
    if not raw.FPDFPageObj_GetBounds(obj, *(ctypes.byref(v) for v in values)):
        raise ValueError("pdf_text_visibility_bounds_unavailable")
    return _box(v.value for v in values)


def _clips(raw, obj, parent_matrix, object_path):
    handle = raw.FPDFPageObj_GetClipPath(obj)
    count = raw.FPDFClipPath_CountPaths(handle) if handle else -1
    # PDFium returns -1 for a default clip with no ref, as well as failure.
    # Neither supplies a positive proof. Ancestor rectangular clips still apply.
    if count <= 0:
        return []
    if count > _MAX_PATHS:
        raise ValueError("pdf_text_visibility_clip_limit")
    result = []
    for path_index in range(count):
        size = raw.FPDFClipPath_CountPathSegments(handle, path_index)
        if size < 0 or size > _MAX_SEGMENTS:
            raise ValueError("pdf_text_visibility_segment_limit")
        segments = []
        for index in range(size):
            segment = raw.FPDFClipPath_GetPathSegment(handle, path_index, index)
            x, y = ctypes.c_float(), ctypes.c_float()
            if not segment or not raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
                raise ValueError("pdf_text_visibility_clip_unavailable")
            segments.append([x.value, y.value, int(raw.FPDFPathSegment_GetType(segment)),
                             bool(raw.FPDFPathSegment_GetClose(segment))])
        rect = _rectangle(segments)
        result.append({"owner_object_path": list(object_path), "path_index": path_index,
                       "kind": "rectangle" if rect else "complex_or_unavailable",
                       "segments": segments, "parent_to_page_matrix": list(parent_matrix),
                       "local_bbox": _box_dict(rect) if rect else None,
                       "page_bbox": _box_dict(_transformed_box(rect, parent_matrix)) if rect else None})
    return result


def _object_text(raw, obj, textpage):
    size = raw.FPDFTextObj_GetText(obj, textpage, None, 0)
    if size < 2 or size % 2 or size > _MAX_TEXT_BYTES:
        raise ValueError("pdf_text_visibility_text_unavailable_or_limit")
    buffer = (ctypes.c_ushort * (size // 2))()
    if raw.FPDFTextObj_GetText(obj, textpage, buffer, size) != size:
        raise ValueError("pdf_text_visibility_text_changed")
    text = bytes(buffer).decode("utf-16-le", errors="strict")
    if not text.endswith("\0") or "\0" in text[:-1] or "\ufffd" in text:
        raise ValueError("pdf_text_visibility_unmapped_unicode")
    return text[:-1], size


def _read_page(raw, page, textpage, page_no, digest):
    result = {"policy": PDF_TEXT_VISIBILITY_POLICY, "page_no": page_no,
              "source_sha256": digest, "status": "complete", "review_required": False,
              "coordinate_space": "unrotated_pdf_user_space_bottom_left", "records": []}
    crop, visible = page.get_cropbox(), page.get_bbox()
    if page.get_rotation() != 0 or any(abs(v) > .01 for v in (*crop[:2], *visible[:2])):
        result.update(status="unsupported_coordinate_space", review_required=True)
        return result
    state = {"objects": 0, "text_bytes": 0}

    def walk(container, path=(), matrix=_IDENTITY, ancestors=(), clips=(), active=frozenset()):
        count = (raw.FPDFFormObj_CountObjects(container) if path
                 else raw.FPDFPage_CountObjects(container))
        if count < 0 or count > _MAX_OBJECTS or len(path) > _MAX_DEPTH:
            raise ValueError("pdf_text_visibility_tree_limit")
        for index in range(count):
            state["objects"] += 1
            if state["objects"] > _MAX_OBJECTS:
                raise ValueError("pdf_text_visibility_object_limit")
            obj = (raw.FPDFFormObj_GetObject(container, index) if path
                   else raw.FPDFPage_GetObject(container, index))
            address = ctypes.cast(obj, ctypes.c_void_p).value
            if not address or address in active:
                raise ValueError("pdf_text_visibility_object_cycle_or_unavailable")
            kind = raw.FPDFPageObj_GetType(obj)
            if kind not in (raw.FPDF_PAGEOBJ_FORM, raw.FPDF_PAGEOBJ_TEXT):
                continue
            object_path = (*path, index)
            own_matrix = _object_matrix(raw, obj)
            local_bounds = _object_bounds(raw, obj)
            page_bounds = _transformed_box(local_bounds, matrix)
            effective_clips = (*clips, *_clips(raw, obj, matrix, object_path))
            if kind == raw.FPDF_PAGEOBJ_FORM:
                ancestor = {"object_path": list(object_path), "matrix": list(own_matrix),
                            "parent_to_page_matrix": list(matrix), "local_bbox": _box_dict(local_bounds)}
                walk(obj, object_path, _compose(matrix, own_matrix), (*ancestors, ancestor),
                     effective_clips, active | {address})
                continue
            text, size = _object_text(raw, obj, textpage)
            state["text_bytes"] += size
            if state["text_bytes"] > _MAX_PAGE_TEXT_BYTES:
                raise ValueError("pdf_text_visibility_page_text_limit")
            if not text.strip():
                continue
            visibility, exclusion = _classify(page_bounds, effective_clips)
            render_mode = int(raw.FPDFTextObj_GetTextRenderMode(obj))
            # Stroke expansion is not separately certified by this helper.
            if render_mode not in (0, 4):
                visibility, exclusion = "unknown", None
            result["records"].append({
                "record_id": f"{digest}:page-{page_no}:object-" + "/".join(map(str, object_path)),
                "source_sha256": digest, "page_no": page_no, "object_path": list(object_path),
                "text": text, "bbox": _box_dict(page_bounds), "visibility": visibility,
                "fully_clipped": visibility == "fully_clipped",
                "proof": {"local_bbox": _box_dict(local_bounds), "object_matrix": list(own_matrix),
                          "parent_to_page_matrix": list(matrix), "ancestor_forms": list(ancestors),
                          "effective_clips": list(effective_clips), "excluding_clip": exclusion,
                          "separation_epsilon_pt": _SEPARATION_EPSILON,
                          "rendering_mode": render_mode,
                          "text_binding": "complete_pdfium_text_object_utf16_no_rewriting"},
            })

    try:
        walk(page)
    except (ValueError, OverflowError, UnicodeError) as error:
        result.update(status="incomplete", review_required=True, reason=str(error))
        # A missed object could be a duplicate text/bbox. Never hide text using
        # an incomplete object inventory, even when an earlier proof was valid.
        for record in result["records"]:
            record.update(visibility="unknown", fully_clipped=False)
    result["visited_objects"] = state["objects"]
    return result


def read_pdf_text_visibility(source, *, page_numbers=None):
    """Read bounded pages from immutable bytes/path, with one-based page IDs.

    No OCR, page rendering, model, network request, PDF mutation or file write.
    Experimental PDFium APIs are capability checked; old runtimes fail explicitly.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    required = ("FPDFPage_CountObjects", "FPDFPage_GetObject", "FPDFFormObj_CountObjects",
                "FPDFFormObj_GetObject", "FPDFPageObj_GetType", "FPDFPageObj_GetMatrix",
                "FPDFPageObj_GetBounds", "FPDFPageObj_GetClipPath", "FPDFClipPath_CountPaths",
                "FPDFClipPath_CountPathSegments", "FPDFClipPath_GetPathSegment",
                "FPDFPathSegment_GetPoint", "FPDFPathSegment_GetType", "FPDFPathSegment_GetClose",
                "FPDFTextObj_GetText", "FPDFTextObj_GetTextRenderMode")
    if any(not callable(getattr(raw, name, None)) for name in required):
        raise ValueError("document_pdfium_visibility_api_unsupported")
    content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    result = {}
    pdf = pdfium.PdfDocument(content)
    try:
        pages = list(range(1, len(pdf)+1)) if page_numbers is None else list(page_numbers)
        if any(type(n) is not int or not 1 <= n <= len(pdf) for n in pages) or len(set(pages)) != len(pages):
            raise ValueError("pdf_text_visibility_invalid_pages")
        for number in pages:
            page = pdf[number-1]
            textpage = None
            try:
                textpage = page.get_textpage()
                result[number] = _read_page(raw, page, textpage, number, digest)
            finally:
                if textpage is not None:
                    textpage.close()
                page.close()
    finally:
        pdf.close()
    return result


def match_text_visibility(page_record, text, bbox, *, source_sha256, page_no):
    """Bind one native line to exactly one whole PDFium object; never substrings.

    Only whitespace differs between the parsers. Unicode normalization, ligature
    expansion, case folding and matching pieces of longer strings are forbidden.
    Native line rectangles include font leading; the ink bbox must be contained
    by that line, with an explicit .25pt parser tolerance. Duplicate matching
    objects (including visible/invisible overlays) always remain unknown.
    """
    unknown = {"visibility": "unknown", "fully_clipped": False, "record_ids": [],
               "reason": "no_unique_complete_text_object_binding"}
    if (page_record.get("source_sha256") != source_sha256 or not source_sha256
            or page_record.get("page_no") != page_no or page_record.get("status") != "complete"
            or page_record.get("policy") != PDF_TEXT_VISIBILITY_POLICY):
        return {**unknown, "reason": "source_or_page_evidence_not_bound"}
    try:
        line = _box(bbox)
    except (ValueError, TypeError, KeyError):
        return {**unknown, "reason": "invalid_native_line_bbox"}
    signature = "".join(c for c in text if not c.isspace())
    if not signature:
        return unknown
    matches = []
    for record in page_record.get("records", []):
        if (record.get("source_sha256") != source_sha256 or record.get("page_no") != page_no
                or "".join(c for c in record["text"] if not c.isspace()) != signature):
            continue
        box = _box(record["bbox"])
        if (line[0]-_MATCH_TOLERANCE <= box[0] and line[1]-_MATCH_TOLERANCE <= box[1]
                and line[2]+_MATCH_TOLERANCE >= box[2] and line[3]+_MATCH_TOLERANCE >= box[3]):
            matches.append(record)
    if len(matches) != 1:
        return {**unknown, "record_ids": [record["record_id"] for record in matches]}
    record = matches[0]
    clipped = record["visibility"] == "fully_clipped" and record["fully_clipped"]
    return {"visibility": "fully_clipped" if clipped else "unknown", "fully_clipped": clipped,
            "record_ids": [record["record_id"]], "record": record,
            "reason": "unique_complete_text_object_rect_clip" if clipped else "visibility_not_proven",
            "match_tolerance_pt": _MATCH_TOLERANCE, "native_text": text, "native_bbox": dict(bbox),
            "binding_policy": PDF_TEXT_VISIBILITY_BINDING_POLICY,
            "visibility_policy": PDF_TEXT_VISIBILITY_POLICY,
            "page_inventory_status": page_record["status"], "matching_object_count": len(matches)}


def validate_clipped_text_binding(proof, native_line, *, source_sha256, page_no):
    """Revalidate a complete unique line/object proof at the export boundary.

    The caller must additionally bind ``native_line`` to its current item/ref
    and source character span. No PDF object is fetched here: all coordinates
    and transforms are recomputed from the source-bound reader evidence. This
    rejects accidentally swapped same-page/same-string proofs and stale or
    truncated proof dictionaries. It does not authenticate arbitrary fabricated
    evidence; source evidence must originate in ``read_pdf_text_visibility``.
    """
    def same_numbers(left, right):
        return len(left) == len(right) and all(abs(a-b) <= 1e-7 for a, b in zip(left, right))

    def compact(text):
        return "".join(c for c in text if not c.isspace())

    try:
        if (type(page_no) is not int or page_no < 1 or not isinstance(source_sha256, str)
                or len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256)
                or proof["visibility"] != "fully_clipped" or proof["fully_clipped"] is not True
                or proof["reason"] != "unique_complete_text_object_rect_clip"
                or proof["binding_policy"] != PDF_TEXT_VISIBILITY_BINDING_POLICY
                or proof["visibility_policy"] != PDF_TEXT_VISIBILITY_POLICY
                or proof["page_inventory_status"] != "complete"
                or type(proof["matching_object_count"]) is not int or proof["matching_object_count"] != 1
                or proof["match_tolerance_pt"] != _MATCH_TOLERANCE):
            return False
        index = native_line["line_index"]
        if (native_line["source_sha256"] != source_sha256 or native_line["page_no"] != page_no
                or type(index) is not int or index < 0
                or native_line["line_id"] != f"page-{page_no}:line-{index}"
                or native_line["coordinate_space_supported"] is not True
                or native_line["from_ocr"] is not False or native_line["rendering_mode"] in (3, 7)
                or proof["native_text"] != native_line["native_text"]
                or not compact(native_line["native_text"])):
            return False
        line_box = _box(native_line["bbox"])
        if not same_numbers(_box(proof["native_bbox"]), line_box):
            return False
        record = proof["record"]
        path = record["object_path"]
        if (not isinstance(path, list) or not 1 <= len(path) <= _MAX_DEPTH+1
                or any(type(i) is not int or i < 0 for i in path)
                or record["source_sha256"] != source_sha256 or record["page_no"] != page_no
                or record["visibility"] != "fully_clipped" or record["fully_clipped"] is not True
                or compact(record["text"]) != compact(native_line["native_text"])):
            return False
        expected_id = f"{source_sha256}:page-{page_no}:object-" + "/".join(map(str, path))
        if record["record_id"] != expected_id or proof["record_ids"] != [expected_id]:
            return False
        box = _box(record["bbox"])
        if not (line_box[0]-_MATCH_TOLERANCE <= box[0] and line_box[1]-_MATCH_TOLERANCE <= box[1]
                and line_box[2]+_MATCH_TOLERANCE >= box[2] and line_box[3]+_MATCH_TOLERANCE >= box[3]):
            return False
        evidence = record["proof"]
        if (evidence["rendering_mode"] not in (0, 4)
                or evidence["text_binding"] != "complete_pdfium_text_object_utf16_no_rewriting"
                or evidence["separation_epsilon_pt"] != _SEPARATION_EPSILON
                or len(evidence["ancestor_forms"]) != len(path)-1):
            return False
        matrix, parent_frames = _IDENTITY, {}
        for depth, ancestor in enumerate(evidence["ancestor_forms"], 1):
            if (ancestor["object_path"] != path[:depth]
                    or not same_numbers(_matrix(ancestor["parent_to_page_matrix"]), matrix)):
                return False
            _box(ancestor["local_bbox"])
            parent_frames[tuple(path[:depth])] = matrix
            matrix = _compose(matrix, ancestor["matrix"])
        parent_frames[tuple(path)] = matrix
        _matrix(evidence["object_matrix"])
        if (not same_numbers(_matrix(evidence["parent_to_page_matrix"]), matrix)
                or not same_numbers(_transformed_box(evidence["local_bbox"], matrix), box)):
            return False
        clips, seen = evidence["effective_clips"], set()
        for clip in clips:
            owner = tuple(clip["owner_object_path"])
            path_index = clip["path_index"]
            if (owner not in parent_frames or type(path_index) is not int or path_index < 0
                    or (owner, path_index) in seen or clip["kind"] != "rectangle"):
                return False
            seen.add((owner, path_index))
            rect = _rectangle(clip["segments"])
            parent = parent_frames[owner]
            if (not rect or not same_numbers(_box(clip["local_bbox"]), rect)
                    or not same_numbers(_matrix(clip["parent_to_page_matrix"]), parent)
                    or not same_numbers(_transformed_box(rect, parent), _box(clip["page_bbox"]))):
                return False
        classification, exclusion = _classify(box, clips)
        return classification == "fully_clipped" and exclusion == evidence["excluding_clip"]
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
