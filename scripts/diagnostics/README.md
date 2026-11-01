# Isolated request diagnostics

These scripts observe prepared requests. They do not generate workflow rules,
alter frozen tasks, repair outputs, or assign training eligibility.

`run_clean_context_probe.py` accepts a directory containing `01-public-dialogue-request.json`
and `02-public-dialogue-request.json`, and a new output directory. Without
`--execute` it only validates and freezes the two controls. An explicit live run
uses the configured DeepSeek endpoint, verifies its advertised V4.1-Flash model,
and makes at most one completion request per case. No SDK retries or model review
are performed. A previously started attempt cannot silently buy another request.

`observe_http_json.py` records the complete final HTTPX JSON entity body and its
byte digest before transmission. Headers and credentials are excluded. The SDK
argument snapshot is labelled separately. This is client-side observation, not
proof of service-side prompts or weights. Preserve raw responses and usage.

Example (the input files must already exist):

```powershell
.venv/Scripts/python.exe scripts/diagnostics/run_clean_context_probe.py --requests <prepared-directory> --output <new-output-directory>
.venv/Scripts/python.exe scripts/diagnostics/run_clean_context_probe.py --requests <prepared-directory> --output <new-output-directory> --execute
```

Runtime inputs and outputs belong in ignored `.dataforge/` directories. Validation
uses the real SDK with an offline HTTPX transport before an authorized live run;
offline serialization success is not evidence of output quality.
