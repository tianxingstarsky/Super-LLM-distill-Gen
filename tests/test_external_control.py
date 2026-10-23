"""The product control protocol is stable without any harness or Node runtime."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lib.domain.control_protocol import ACTIONS, PROTOCOL, VERSION, validate_request
from lib.infrastructure.workflow_control import WorkflowControl, read_events
from lib.infrastructure.training_workflow import atomic_json, digest, run_path

ROOT = Path(__file__).resolve().parent.parent
RUN = "a" * 32


def request(action="capabilities", params=None, request_id="test-1"):
    return {"protocol": PROTOCOL, "version": VERSION, "request_id": request_id,
            "action": action, "params": params or {}}


def cli(value, root, *, mutations=False):
    command = [sys.executable, "-m", "lib.control", "--root", str(root)]
    if mutations:
        command.append("--allow-mutations")
    result = subprocess.run(command, cwd=ROOT, input=json.dumps(value), capture_output=True,
                            encoding="utf-8", timeout=30)
    # Exactly one JSON value, never old CLI banners or provider debug output.
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("patch", [
    {"version": True}, {"version": 2}, {"protocol": "dsh"}, {"action": "gate.approve"},
    {"action": "backend.read_keys"}, {"request_id": "../key"}, {"request_id": "x" * 129},
    {"request_id": {"secret": "value"}}, {"params": []}, {"params": {"shell": "pwd"}}, {"extra": 1},
])
def test_unknown_or_ambiguous_protocol_rejected(patch):
    with pytest.raises(ValueError):
        validate_request({**request(), **patch})


@pytest.mark.parametrize("action,params", [
    ("runs.inspect", {"run_id": "../private"}), ("runs.preview", {"run_id": RUN}),
    ("runs.events", {"run_id": RUN, "offset": True}), ("runs.list", {"limit": 101}),
    ("runs.list", {"limit": 0}), ("runs.stream_delta", {"run_id": RUN}),
    ("runs.stream_delta", {"run_id": RUN, "request_id": "a", "limit_bytes": 65537}),
    ("models.limit", {"backend": "b", "model": "m", "max_concurrency": True}),
    ("models.limit", {"backend": "b", "model": "m", "max_concurrency": 257}),
    ("runs.create", {"recipe": {"api_key": "do-not-pass-keys"}}),
    ("runs.create", {"recipe": {"run_id": RUN}}),
])
def test_action_parameters_fail_before_dispatch(action, params):
    with pytest.raises(ValueError):
        validate_request(request(action, params))


def test_capabilities_and_rejected_mutations_need_no_harness_or_credentials(tmp_path):
    result, reply = cli(request(), tmp_path)
    assert result.returncode == 0 and reply["ok"] and reply["data"]["version"] == 1
    assert set(reply["data"]["actions"]) == set(ACTIONS)
    assert not (tmp_path / "data/output").exists()
    result, reply = cli(request("runs.create", {"recipe": {"targets": ["cpt"], "brief": "example"}}), tmp_path)
    assert result.returncode == 2 and reply["error"]["code"] == "control_mutations_disabled"
    assert not (tmp_path / "data/output").exists()


def test_oversized_request_identity_cannot_recurse_or_echo_unvalidated_input(tmp_path):
    result, reply = cli(request(request_id="private" * 100_000), tmp_path)
    assert result.returncode == 2 and not reply["ok"]
    assert reply["request_id"] is None and len(result.stdout) < 300
    assert "private" not in result.stdout


def test_native_cpt_runs_through_actual_protocol_and_keeps_reasoning_exports_untouched(tmp_path):
    import shutil
    shutil.copytree(ROOT / "configs", tmp_path / "configs", ignore=shutil.ignore_patterns("*.local.*", "review_remote*"))
    source = tmp_path / "source.md"
    source.write_text("Disconnect equipment power before inspecting cable connections. Record the maintenance result.", encoding="utf-8")
    create = request("runs.create", {"recipe": {"targets": ["cpt"], "sources": [str(source)],
                                                  "max_units": 2, "sample_count": 1}}, "native-cpt")
    result, reply = cli(create, tmp_path, mutations=True)
    assert result.returncode == 0, reply
    run_id = reply["data"]["run_id"]
    assert cli(create, tmp_path, mutations=True)[1]["data"] == {"run_id": run_id, "created": True, "replayed": True}
    result, reply = cli(request("runs.execute", {"run_id": run_id}), tmp_path, mutations=True)
    assert result.returncode == 0 and reply["data"]["state"]["status"] == "completed", reply
    output = tmp_path / "data/output"
    native = run_path(output, run_id) / "artifacts/cpt.jsonl"
    assert source.read_text() in native.read_text(encoding="utf-8")
    _, quality = cli(request("runs.quality", {"run_id": run_id}), tmp_path)
    assert quality["ok"]
    _, preview = cli(request("runs.preview", {"run_id": run_id, "target": "cpt", "limit": 1}), tmp_path)
    assert preview["ok"] and len(preview["data"]["items"]) == 1
    assert cli(request("runs.list"), tmp_path)[1]["data"]["total"] == 1


def test_concurrent_create_and_payload_conflicts_are_not_repeated(tmp_path):
    class App:
        calls = 0
        def create_run(self, **recipe):
            self.calls += 1
            return recipe["run_id"]
    app = App()
    controller = WorkflowControl(app, None, tmp_path)
    item = request("runs.create", {"recipe": {"brief": "same"}})
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(controller.handle, [item] * 8))
    assert app.calls == 1 and len({r["run_id"] for r in rows}) == 1
    assert sum(not r["replayed"] for r in rows) == 1
    with pytest.raises(ValueError, match="control_request_id_conflict"):
        controller.handle(request("runs.create", {"recipe": {"brief": "different"}}))


@pytest.mark.parametrize("complete", [False, True])
def test_interrupted_create_recovers_only_a_complete_frozen_run(tmp_path, complete):
    def create_run(**recipe):
        run = run_path(tmp_path, recipe["run_id"])
        run.mkdir(parents=True)
        frozen = {"brief": "original"}
        atomic_json(run / "recipe.json", frozen)
        if complete:
            atomic_json(run / "state.json", {"id": recipe["run_id"], "recipe_hash": digest(frozen)})
        raise RuntimeError("simulated interrupted acknowledgement")
    controller = WorkflowControl(SimpleNamespace(create_run=create_run), None, tmp_path)
    item = request("runs.create", {"recipe": {"brief": "original"}})
    with pytest.raises(RuntimeError):
        controller.handle(item)
    if complete:
        result = controller.handle(item)
        assert result["created"] and result["replayed"]
    else:
        with pytest.raises(ValueError, match="control_create_outcome_unconfirmed"):
            controller.handle(item)
    assert len(list((tmp_path / "workflows").iterdir())) == 1


def test_event_cursors_preserve_utf8_complete_lines_and_partial_writes(tmp_path):
    run = run_path(tmp_path, RUN)
    run.mkdir(parents=True)
    atomic_json(run / "state.json", {})
    path = run / "events.jsonl"
    rows = [{"text": "中文 🐋"}, {"text": "next"}]
    first = (json.dumps(rows[0], ensure_ascii=False) + "\n").encode()
    second = (json.dumps(rows[1]) + "\n").encode()
    path.write_bytes(first + second[:-1])
    page = read_events(tmp_path, RUN, limit=1)
    assert page["items"] == rows[:1] and page["next_offset"] == len(first)
    partial = read_events(tmp_path, RUN, offset=page["next_offset"])
    assert partial["items"] == [] and partial["incomplete"] and partial["next_offset"] == len(first)
    with path.open("ab") as handle:
        handle.write(b"\n")
    final = read_events(tmp_path, RUN, offset=partial["next_offset"])
    assert final["items"] == rows[1:] and not final["has_more"]
    with pytest.raises(ValueError, match="invalid_event_cursor"):
        read_events(tmp_path, RUN, offset=2)


def test_events_limit_bytes_and_never_skip_oversized_or_corrupt_rows(tmp_path):
    run = run_path(tmp_path, RUN)
    run.mkdir(parents=True)
    atomic_json(run / "state.json", {})
    path = run / "events.jsonl"
    line = json.dumps({"text": "x" * 40000}).encode() + b"\n"
    path.write_bytes(line * 3)
    page = read_events(tmp_path, RUN)
    assert len(page["items"]) == 1 and page["next_offset"] == len(line) and page["has_more"]
    path.write_bytes(b"x" * 65537 + b"\n")
    with pytest.raises(ValueError, match="control_event_too_large"):
        read_events(tmp_path, RUN)
    path.write_bytes(b"{broken}\n")
    with pytest.raises(ValueError):
        read_events(tmp_path, RUN)


def test_model_limit_uses_same_existing_application_without_loading_any_provider_sdk(tmp_path):
    seen = []
    backend = SimpleNamespace(save_model_scheduling=lambda **kwargs: seen.append(kwargs) or {"active": 2, "queued": 4})
    controller = WorkflowControl(None, backend, tmp_path)
    params = {"backend": "shared", "model": "same", "max_concurrency": 3, "request_queue_timeout_seconds": 30}
    assert controller.handle(request("models.limit", params)) == {"active": 2, "queued": 4}
    assert seen == [params]


def test_product_cli_and_diagnostics_do_not_embed_harness_lifecycle():
    from lib.cli import build_parser
    commands = next(action for action in build_parser()._actions if action.dest == "cmd").choices
    assert "dsh" not in commands
    assert not (ROOT / "lib/dsh_env.py").exists()
    assert "deepseek-harness" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "DSH_HOME" not in (ROOT / "lib/doctor.py").read_text(encoding="utf-8")
