"""Isolation, provenance and atomic publication contracts, with no model calls."""
from copy import deepcopy
import sys

import pytest

from lib.domain.document_parser import validate_document_parser
from lib.infrastructure import docling_document as adapter, docling_runtime as runtime, docling_worker_client as client
from test_document_model_parser import text_pdf, PAGE_ONE, PAGE_TWO


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    source = text_pdf(tmp_path)
    config = validate_document_parser({"mode":"docling", "runtime_python":sys.executable,
                                      "artifacts_path":str(tmp_path), "ocr":False})
    signature = {"mode":"docling", "runtime_protocol":1, "packages":runtime.MODERN_PACKAGES}
    monkeypatch.setattr(runtime, "runtime_signature", lambda _, **kwargs: deepcopy(signature))
    digest = adapter._file_hash(source)
    parts = [{"location":f"page:{i}", "text":text, "document_reading":{
                "source_page":i, "source_pdf_sha256":digest, "parser_signature":deepcopy(signature),
                "parsed_text_sha256":adapter._digest(text), "elements":[]}}
             for i,text in enumerate((PAGE_ONE,PAGE_TWO),1)]
    response = {"protocol":1,"signature":signature,"source_sha256":digest,"parts":parts}
    calls=[]
    def execute(python,request,**kwargs):
        calls.append(request)
        return deepcopy(response)
    monkeypatch.setattr(client, "run_docling_worker", execute)
    return source, config, signature, response, calls


def test_dispatch_does_not_require_console_docling_packages(conversion, monkeypatch):
    source,config,signature,response,calls=conversion
    def forbidden(*args):
        raise AssertionError("console Docling must not load")
    monkeypatch.setattr(adapter, "_package_versions", forbidden)
    monkeypatch.setattr(adapter, "_make_converter", forbidden)
    assert adapter.docling_parse_signature(config)==signature
    assert list(adapter.docling_parts(source, config))==response["parts"]
    assert len(calls)==1 and calls[0]["action"]=="parse"


@pytest.mark.parametrize("damage",["missing_page","page_order","hash","signature","text_hash","oversized_text"])
def test_rejects_entire_result_before_first_page_is_published(conversion, damage):
    source,config,signature,response,calls=conversion
    if damage=="missing_page": response["parts"].pop()
    elif damage=="page_order": response["parts"].reverse()
    elif damage=="hash": response["source_sha256"]="changed"
    elif damage=="signature": response["signature"]={"mode":"other"}
    elif damage=="text_hash": response["parts"][1]["text"]="silently changed"
    elif damage=="oversized_text": response["parts"][1]["text"]="x"*(adapter._MAX_PAGE_CHARS+1)
    with pytest.raises(ValueError):
        next(adapter.docling_parts(source, config))


def test_stale_source_signature_prevents_launch(conversion):
    source,config,signature,response,calls=conversion
    with pytest.raises(ValueError,match="configuration_changed"):
        list(adapter.docling_parts(source,config,expected_signature={"old":True}))
    assert not calls


def test_existing_quarantine_is_not_removed(conversion):
    source,config,signature,response,calls=conversion
    response["parts"][0].update(status="quarantined",reason="document_docling_formula_undecoded")
    assert list(adapter.docling_parts(source,config))[0]["status"]=="quarantined"


@pytest.mark.parametrize('code,expected',[
    ('document_docling_page_span_ambiguous','document_docling_page_span_ambiguous'),
    ('private raw exception text','document_docling_invalid_output'),
])
def test_worker_error_is_specific_but_never_raw_text(conversion,code,expected):
    source,config,signature,response,calls=conversion
    response.clear()
    response.update(protocol=1,error=code)
    with pytest.raises(ValueError,match=expected):
        list(adapter.docling_parts(source,config))


def test_runtime_path_validation():
    for invalid in ("relative/python.exe",123,False,[],{}):
        with pytest.raises(ValueError):
            validate_document_parser({"mode":"docling","artifacts_path":str(__import__('pathlib').Path.cwd()),"runtime_python":invalid})


def test_inline_runtime_requires_formula_and_isolated_parser(tmp_path):
    base = {"mode": "docling", "artifacts_path": str(tmp_path),
            "inline_runtime_python": sys.executable}
    for options in ({}, {"runtime_python": sys.executable}, {"formula_enrichment": True}):
        with pytest.raises(ValueError, match="inline_runtime_requires_formula"):
            validate_document_parser({**base, **options})
    valid = {**base, "runtime_python": sys.executable, "formula_enrichment": True}
    assert validate_document_parser(valid)["inline_runtime_python"] == sys.executable
    for invalid in ("relative/python", 123, False, [], {}):
        with pytest.raises(ValueError):
            validate_document_parser({**valid, "inline_runtime_python": invalid})
    for empty in ("", None):
        assert "inline_runtime_python" not in validate_document_parser({**valid, "inline_runtime_python": empty})


def test_inline_signature_inspects_separate_runtime_and_fingerprints_policies(tmp_path, monkeypatch):
    from lib.infrastructure import docling_preflight as preflight
    from lib.infrastructure import docling_inline_formulas as inline
    from lib.infrastructure import docling_inline_geometry as geometry, docling_inline_assembly as assembly
    from lib.infrastructure import docling_inline_candidates as candidates, docling_math_consistency as consistency
    from lib.infrastructure import docling_inline_markdown as markdown
    from lib.infrastructure import docling_inline_routing as routing, docling_inline_lines as lines
    from lib.infrastructure import docling_inline_partitions as partitions
    from test_docling_settings import _modern_assets
    config = _modern_assets(tmp_path / "models")
    config.update(runtime_python=sys.executable, inline_runtime_python=sys.executable)
    model = tmp_path / "models" / inline.INLINE_LAYOUT_DIRECTORY
    model.mkdir()
    for name in preflight._INLINE_LAYOUT_FILES:
        (model / name).write_bytes(b"fixture")
    calls, cancel = [], lambda: None
    def inspect(python, request, **kwargs):
        calls.append((request, kwargs.get("check_cancel")))
        packages = (runtime.INLINE_LAYOUT_PACKAGES if request.get("service") == "inline_layout"
                    else {**runtime.MODERN_PACKAGES, **preflight._FORMULA_PACKAGES, **preflight._INLINE_FORMULA_PACKAGES})
        return {"protocol": 1, "packages": dict(packages)}
    monkeypatch.setattr(client, "run_docling_worker", inspect)
    signature = runtime.runtime_signature(config, check_cancel=cancel)
    detail = signature["inline_formula"]
    assert detail["layout_policy"] == inline.INLINE_LAYOUT_POLICY
    assert detail["geometry_policy"] == geometry.INLINE_GEOMETRY_POLICY
    assert detail["assembly_policy"] == assembly.INLINE_ASSEMBLY_POLICY
    assert detail["candidate_policy"] == candidates.INLINE_CANDIDATE_POLICY
    assert detail["consistency_policy"] == consistency.MATH_CONSISTENCY_POLICY
    assert detail["markdown_policy"] == markdown.INLINE_MARKDOWN_POLICY
    assert detail["routing_policy"] == routing.INLINE_ROUTING_POLICY
    assert detail["line_policy"] == lines.INLINE_LINE_POLICY
    assert detail["partition_policy"] == partitions.INLINE_PARTITION_POLICY
    assert detail["render"] == {"page_dpi": 288, "paragraph_dpi": 480, "formula_dpi": 480}
    assert detail["packages"] == runtime.INLINE_LAYOUT_PACKAGES
    assert calls == [({"action": "inspect"}, cancel),
                     ({"action": "inspect", "service": "inline_layout"}, cancel)]
    from lib.infrastructure import docling_code_classification as code
    from lib.infrastructure.docling_table_mixed_math import TABLE_MIXED_MATH_POLICY
    assert detail["table_mixed_math"]["policy"] == TABLE_MIXED_MATH_POLICY
    assert detail["code_structure"]["policy"] == code.CODE_CLASSIFICATION_POLICY
    assert detail["code_structure"]["available"] is False
    assert detail["code_structure"]["model_sha256"] is None
    code_model = tmp_path / "models" / code.CODE_STRUCTURE_DIRECTORY
    code_model.mkdir()
    for name in code.CODE_STRUCTURE_FILES:
        (code_model / name).write_bytes(b"local optional classifier")
    with_code = runtime.runtime_signature(config)["inline_formula"]["code_structure"]
    assert with_code["available"] is True
    assert with_code["parameters"] == code.CODE_LAYOUT_PARAMETERS
    assert with_code["revision"] == code.CODE_STRUCTURE_REVISION
    (code_model / "model.safetensors").write_bytes(b"changed classifier")
    changed = runtime.runtime_signature(config)["inline_formula"]["code_structure"]
    assert changed["model_sha256"] != with_code["model_sha256"]
    monkeypatch.setitem(code.CODE_LAYOUT_PARAMETERS, "text_threshold", .7)
    assert runtime.runtime_signature(config)["inline_formula"]["code_structure"] != changed
    (model / "inference.onnx").write_bytes(b"changed model")
    assert runtime.runtime_signature(config)["inline_formula"]["layout_sha256"] != detail["layout_sha256"]
    monkeypatch.setattr(geometry, "INLINE_GEOMETRY_POLICY", "changed-policy")
    assert runtime.runtime_signature(config)["inline_formula"]["geometry_policy"] != detail["geometry_policy"]
    config.pop("inline_runtime_python")
    calls.clear()
    assert "inline_formula" not in runtime.runtime_signature(config)
    assert len(calls) == 1 and calls[0][0] == {"action": "inspect"}


@pytest.mark.parametrize("damage", ["protocol", "paddleocr", "paddlex", "onnxruntime"])
def test_inline_signature_rejects_runtime_mismatch_before_loading_assets(tmp_path, monkeypatch, damage):
    config = {"runtime_python": sys.executable, "inline_runtime_python": sys.executable,
              "formula_enrichment": True, "artifacts_path": str(tmp_path)}
    def inspect(python, request, **kwargs):
        if not request.get("service"):
            return {"protocol": 1, "packages": runtime.MODERN_PACKAGES}
        return {"protocol": 2 if damage == "protocol" else 1,
                "packages": {**runtime.INLINE_LAYOUT_PACKAGES, damage: "changed"}}
    monkeypatch.setattr(client, "run_docling_worker", inspect)
    with pytest.raises(ValueError, match="inline_version_mismatch"):
        runtime.runtime_signature(config)
