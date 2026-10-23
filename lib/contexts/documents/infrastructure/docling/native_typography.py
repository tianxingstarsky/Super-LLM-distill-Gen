"""Restore narrowly proven source scripts using PDFium baselines, without OCR.

Only a numeric base or isolated variable followed by a smaller shifted glyph
run is supported. Exact native-character/backend alignment is mandatory. This
does not infer formulas, complete missing characters, or approve other math.
"""
from __future__ import annotations

from .inline_native_preservation import _native_span, _alignment, _same_row
from .math_regions import is_math_font, _box, _box_dict

NATIVE_TYPOGRAPHY_POLICY = "pdfium_exact_native_numeric_and_variable_script_runs_v1"


def script_patches(glyphs, native_chars, text):
    gs = [g for g in glyphs if g["text"].strip() and not g.get("generated")]
    patches, consumed = [], set()
    for i in range(1, len(gs)):
        base, small = gs[i-1], gs[i]
        if (i in consumed or not base["text"].isalnum() or not small["text"].isalnum()
                or not 0 < small["font_size"] < .88*base["font_size"]):
            continue
        offset = small["origin"][1]-base["origin"][1]
        gap = small["bbox"]["l"]-base["bbox"]["r"]
        if not (.1*base["font_size"] < abs(offset) < .95*base["font_size"]
                and -.25*base["font_size"] <= gap <= .55*base["font_size"]):
            continue
        a, b = i-1, i+1
        if base["text"].isdecimal():
            while (a > 0 and gs[a-1]["text"].isdecimal() and _same_row(gs[a-1], base)
                   and 0 <= gs[a]["bbox"]["l"]-gs[a-1]["bbox"]["r"] <= .3*base["font_size"]):
                a -= 1
        elif (not is_math_font(base["font_name"]) and a > 0 and gs[a-1]["text"].isalpha()
              and _same_row(gs[a-1], base)
              and base["bbox"]["l"]-gs[a-1]["bbox"]["r"] < .3*base["font_size"]):
            continue  # A footnote after a prose word is not an algebraic base.
        while (b < len(gs) and gs[b]["text"].isalnum() and _same_row(gs[b], small)
               and -.2*small["font_size"] <= gs[b]["bbox"]["l"]-gs[b-1]["bbox"]["r"] <= .3*small["font_size"]):
            b += 1
        token = gs[a:b]
        if any(g["origin"][0] <= p["origin"][0] for p, g in zip(token, token[1:])):
            continue
        span = _native_span(token, native_chars)
        alignment = _alignment(native_chars, span, text) if span else None
        if not alignment:
            continue
        first = "".join(g["text"] for g in gs[a:i])
        script = "".join(g["text"] for g in gs[i:b])
        if script.isalpha() and not any(is_math_font(g["font_name"]) for g in gs[i:b]):
            script = r"\mathrm{"+script+"}"
        latex = first + ("^{" if offset > 0 else "_{") + script + "}"
        patches.append({"charspan": alignment["backend_charspan"], "backend_text": alignment["backend_text"],
            "replacement": "$"+latex+"$", "native_text": alignment["native_text"],
            "native_char_indices": [c["index"] for c in native_chars[span[0]:span[1]]],
            "glyph_ids": [g["char_index"] for g in token], "baseline_offset": offset,
            "fragment_id": "source-script:"+str(small["char_index"]),
            "basis": "exact_native_alignment_smaller_shifted_source_glyphs"})
        consumed.update(range(a, b))
    return sorted(patches, key=lambda p: p["charspan"])


def _assemble(text, patches):
    output, cursor = [], 0
    for patch in patches:
        a, b = patch["charspan"]
        if not cursor <= a < b <= len(text) or text[a:b] != patch["backend_text"]:
            return None
        output.extend((text[cursor:a], patch["replacement"]))
        cursor = b
    output.append(text[cursor:])
    return {"status": "native_preserved", "backend_text": text, "text": "".join(output),
            "patches": patches, "prose_preserved": True, "review_reasons": []}


def restore_native_typography(document, source, evidence):
    from .inline_assembly import read_native_character_pages, native_paragraph_chars
    from .math_regions import extract_pdf_page_glyphs
    native = read_native_character_pages(source)
    pages, audit = {}, []

    def patch(text, prov):
        number = prov.page_no
        if number not in pages:
            pages[number] = extract_pdf_page_glyphs(source, number)
        page = pages[number]
        if page.get("review_required"):
            return None
        box = _box_dict(_box(prov.bbox, page["height"]))
        glyphs = [g for g in page["glyphs"] if box["l"] <= (g["bbox"]["l"]+g["bbox"]["r"])/2 <= box["r"]
                  and box["b"] <= (g["bbox"]["b"]+g["bbox"]["t"])/2 <= box["t"]]
        patches = script_patches(glyphs, native_paragraph_chars(native.get(number, []), box), text)
        return _assemble(text, patches) if patches else None

    for item in document.texts:
        if (len(item.prov) != 1 or getattr(item.label, "value", item.label) not in
                {"text", "caption", "list_item", "footnote", "section_header"}):
            continue
        old = evidence.get(item.self_ref, {})
        if old.get("published"):
            continue
        assembly = patch(item.text, item.prov[0])
        if assembly is None:
            continue
        original = dict(old)
        item.text = assembly["text"]
        item.prov[0] = item.prov[0].model_copy(update={"charspan": (0, len(item.text))})
        evidence[item.self_ref] = {"kind": "inline_math", "policy": NATIVE_TYPOGRAPHY_POLICY,
            "page_no": item.prov[0].page_no, "status": old.get("status", "native_preserved"),
            "backend_text": assembly["backend_text"],
            "published": True, "assembly": assembly, "previous_evidence": original,
            "source_sha256": pages[item.prov[0].page_no]["source_sha256"],
            "mathematical_correctness_certified": False}
        audit.append({"item_ref": item.self_ref, "page_no": item.prov[0].page_no,
                      "policy": NATIVE_TYPOGRAPHY_POLICY, "patches": assembly["patches"]})
    for table in document.tables:
        if len(table.prov) != 1:
            continue
        for index, cell in enumerate(table.data.table_cells):
            if not cell.bbox or "$" in cell.text:
                continue
            prov = table.prov[0].model_copy(update={"bbox": cell.bbox})
            assembly = patch(cell.text, prov)
            if assembly:
                cell.text = assembly["text"]
                audit.append({"table_ref": table.self_ref, "cell_index": index, "page_no": prov.page_no,
                              "policy": NATIVE_TYPOGRAPHY_POLICY, "patches": assembly["patches"]})
    return audit
