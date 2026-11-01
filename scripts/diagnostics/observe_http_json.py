"""Local experiment capture at HTTPX's request hook, before the transport.

Only POST JSON bodies are saved. Authentication headers, cookies, URL query
strings and credentials are never inspected or stored. These bytes are the
HTTP entity body, not TLS packets or SDK call arguments.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4


@contextmanager
def observe_http_json(http_client, output_directory, *, sdk_kwargs=None):
    """Observe this sync HTTPX client for the duration of a bounded experiment.

    The caller owns the client and serializes its calls. Other hooks are kept.
    Optional sdk_kwargs is a caller-supplied snapshot, separately labelled; it
    is never mistaken for the transmitted body. Capture failures abort before
    a model request can be sent without its body record.
    """
    directory=Path(output_directory)
    directory.mkdir(parents=True,exist_ok=True)
    paths=[]
    # The optional SDK snapshot must not reintroduce authentication transport
    # data that the HTTP body observer deliberately does not collect.
    transport_options={'headers','extra_headers','cookies','extra_query'}
    omitted=sorted(transport_options.intersection(sdk_kwargs or {}))
    provided_kwargs=(None if sdk_kwargs is None else deepcopy({
        key:value for key,value in sdk_kwargs.items() if key not in transport_options}))

    def capture(request):
        if request.method!='POST':
            return
        raw=request.read()
        text=raw.decode('utf-8')
        payload=json.loads(text)
        record={
            'schema_version':1,
            'observation_point':'httpx_request_hook_before_transport',
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'method':request.method,
            'destination':{'scheme':request.url.scheme,'host':request.url.host,
                           'port':request.url.port,'path':request.url.path},
            'body_utf8':text,
            'body_sha256':hashlib.sha256(raw).hexdigest(),
            'body_byte_length':len(raw),
            'json_body':payload,
            'byte_scope':'HTTP request entity body before transport; not TLS or full HTTP packet bytes.',
            'headers_recorded':False,
            'sdk_kwargs_provenance':'not_captured' if provided_kwargs is None else 'caller_supplied_separate_snapshot',
        }
        if provided_kwargs is not None:
            record['sdk_kwargs']=provided_kwargs
            record['omitted_sdk_transport_options']=omitted
        target=directory/(uuid4().hex+'.json')
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(target)
        paths.append(target)

    http_client.event_hooks.setdefault('request',[]).append(capture)
    try:
        yield paths
    finally:
        hooks=http_client.event_hooks.get('request',[])
        if capture in hooks:
            hooks.remove(capture)
