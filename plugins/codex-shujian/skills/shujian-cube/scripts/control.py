"""Transport one ShuJian Cube control request with portable UTF-8 serialization."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

PROTOCOL = "dataforge.control"
VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def call(root: Path, action: str, params: dict, *, python: Path | None = None,
         workspace: str = "default", request_id: str | None = None,
         allow_mutations: bool = False) -> tuple[dict, int]:
    root = root.expanduser().resolve()
    if not (root / "lib" / "control.py").is_file():
        raise ValueError("product_root_missing_control_entrypoint")
    if action == "runs.create" and not request_id:
        raise ValueError("create_requires_stable_request_id")
    if not isinstance(params, dict):
        raise ValueError("params_must_be_an_object")
    python = python or root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = python.expanduser().resolve()
    if not python.is_file():
        raise ValueError("product_python_missing_use_explicit_python")
    identity = request_id or uuid.uuid4().hex
    payload = json.dumps({"protocol": PROTOCOL, "version": VERSION, "request_id": identity,
                          "action": action, "params": params}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("control_request_too_large")
    command = [str(python), "-X", "utf8", "-m", "lib.control", "--root", str(root), "--workspace", workspace]
    if allow_mutations:
        command.append("--allow-mutations")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(command, cwd=root, input=payload, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, check=False)
    if len(process.stdout) > MAX_RESPONSE_BYTES:
        raise ValueError("control_response_too_large")
    try:
        reply = json.loads(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("control_invalid_response") from None
    if (not isinstance(reply, dict) or reply.get("protocol") != PROTOCOL
            or type(reply.get("version")) is not int or reply["version"] != VERSION
            or reply.get("request_id") != identity or type(reply.get("ok")) is not bool):
        raise ValueError("control_response_identity_mismatch")
    if reply["ok"] and process.returncode != 0:
        raise ValueError("control_process_failed_after_success_response")
    if not reply["ok"] and process.returncode == 0:
        raise ValueError("control_refusal_returned_success_exit")
    return reply, process.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Product installation root")
    parser.add_argument("--python", type=Path, help="Explicit product Python interpreter")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--action", required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--params-file", type=Path, help="UTF-8 file containing the action params object")
    inputs.add_argument("--params", help="Inline JSON params object; files avoid shell-quoting problems")
    parser.add_argument("--request-id", help="Stable identity; required for runs.create")
    parser.add_argument("--allow-mutations", action="store_true")
    args = parser.parse_args()
    try:
        params = json.loads(args.params_file.read_text(encoding="utf-8-sig") if args.params_file
                            else args.params or "{}")
        reply, code = call(args.root, args.action, params, python=args.python,
                           workspace=args.workspace, request_id=args.request_id,
                           allow_mutations=args.allow_mutations)
    except (OSError, ValueError) as error:
        print(json.dumps({"client_error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    sys.stdout.buffer.write((json.dumps(reply, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
