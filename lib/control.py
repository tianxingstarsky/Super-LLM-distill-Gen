"""One bounded JSON request on stdin, one versioned JSON response on stdout.

External controllers own their SDKs and job lifecycles. This interface knows
only persistent workflows and shared model admission, and never launches one.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import re
import sys

from lib.domain.control_protocol import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MUTATIONS,
                                         PROTOCOL, VERSION, validate_request)


def response(request, *, data=None, error=None):
    identity = request.get("request_id") if isinstance(request, dict) else None
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity):
        identity = None
    value = {"protocol": PROTOCOL, "version": VERSION,
             "request_id": identity,
             "ok": error is None}
    value["error" if error else "data"] = error if error else data
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return response(request, error={"code": "control_response_too_large"})
    return encoded


def main(argv=None):
    parser = argparse.ArgumentParser(description="DataForge external control protocol v1")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--allow-mutations", action="store_true")
    args = parser.parse_args(argv)
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("control_request_too_large")
        request = validate_request(json.loads(raw.decode("utf-8")))
        if request["action"] in MUTATIONS and not args.allow_mutations:
            raise ValueError("control_mutations_disabled")
        # Keep old pipeline progress and provider diagnostics off the protocol
        # channel. The durable event/stream journals own incremental monitoring.
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
            from lib.bootstrap.workspaces import workspace_application
            from lib.bootstrap.workflows import workflow_application
            from lib.bootstrap.backends import backend_application
            from lib.infrastructure.workflow_control import WorkflowControl
            root = args.root.resolve()
            output = workspace_application(root).output(args.workspace)
            controller = WorkflowControl(workflow_application(root, output), backend_application(root), output)
            data = controller.handle(request)
            if request["action"] != "capabilities":
                from lib.infrastructure.export_credentials import ExportCredentialGuard
                ExportCredentialGuard.from_root(root).check_value(data)
        payload, code = response(request, data=data), 0
    except Exception as error:
        detail = str(error)
        safe = detail if isinstance(error, ValueError) and re.fullmatch(r"[a-z][a-z0-9_]{2,90}", detail) else "control_operation_failed"
        payload, code = response(request, error={"code": safe}), 2
    sys.stdout.buffer.write((payload + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
