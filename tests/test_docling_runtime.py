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
