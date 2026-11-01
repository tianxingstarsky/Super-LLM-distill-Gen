"""Run Google's original memory-induction entrypoints with local I/O and model ports.

The upstream main loops, trajectory formatting, prompts, and JSONL output are
executed from the pinned checkout. This module supplies actual recorded episodes
and our admitted model gateway; it does not generate trajectories or judge them.
"""
import builtins
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from filelock import FileLock

from lib.io_utils import atomic_json
from ..domain.experience_evolution import digest
from .reasoning_bank_source import load_upstream_constants, load_upstream_functions


class _SingleResultDirectory(str):
    """Adapt the script's space-separated CLI argument to one exact local path."""

    def split(self, *args, **kwargs):
        return [str(self)]


def _trajectory(episode):
    """Check the external trajectory boundary without inventing a success label."""
    value = deepcopy(episode)
    if not isinstance(value, dict) or not isinstance(value.get("query"), str) or not value["query"].strip():
        raise ValueError("reasoning_bank_query_required")
    if value.get("status") not in {"success", "fail"} or not value.get("assessment_source"):
        raise ValueError("reasoning_bank_observed_outcome_required")
    thoughts, actions = value.get("think_list"), value.get("action_list")
    if (not isinstance(thoughts, list) or not isinstance(actions, list) or not actions
            or len(thoughts) != len(actions) or any(not isinstance(item, str) for item in thoughts + actions)):
        raise ValueError("reasoning_bank_recorded_trajectory_required")
    value.setdefault("template_id", value.get("id") or digest(value))
    value.setdefault("feedback", "")
    if not isinstance(value["feedback"], str):
        raise ValueError("reasoning_bank_feedback_must_be_text")
    return value


class _GatewayClient:
    def __init__(self, binding, client_factory, directory, *, source_receipts, scaling=False):
        self.binding, self.client_factory = binding, client_factory
        self.directory, self.source_receipts = directory, source_receipts
        self.scaling, self.calls = scaling, []

    def __call__(self, *, model_name):
        return self

    def one_step_chat(self, prompt, *, system_msg, temperature):
        messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
        model_settings = {key: self.binding[key] for key in
                          ("backend", "model", "max_output_tokens", "context_window_tokens", "reasoning")
                          if key in self.binding}
        request = {"binding": model_settings, "messages": messages, "temperature": temperature,
                   "upstream": self.source_receipts}
        identity = digest(request)
        path = self.directory / (identity + ".json")
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock"):
            reused = path.exists()
            if reused:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("request") != request or saved.get("response_sha256") != digest(saved.get("response")):
                    raise ValueError("reasoning_bank_model_receipt_changed")
            else:
                client, actual_model = self.client_factory(self.binding)
                details, started = {}, time.time()
                try:
                    response = client.chat(messages, temperature=temperature,
                        max_tokens=self.binding.get("max_output_tokens") or 32768,
                        retries=1, response_details=details, allow_reasoning_fallback=False)
                    if isinstance(getattr(client, "usage", None), dict):
                        details["usage"] = deepcopy(client.usage)
                    saved = {"request": request, "response": response, "response_sha256": digest(response),
                             "response_details": details, "model": actual_model, "seconds": time.time() - started}
                    atomic_json(path, saved)
                finally:
                    client.client.close()
        self.calls.append({"request_id": identity, "reused": reused, "model": saved["model"],
                           "seconds": saved["seconds"], "response_details": deepcopy(saved["response_details"])})
        # Official single main unpacks a tuple; scaling main directly serializes
        # the return. This is the explicit provider-contract compatibility fix.
        return saved["response"] if self.scaling else (saved["response"], self.calls[-1])


class OfficialReasoningBankInductor:
    """Persist resumable upstream induction; no automatic sampling or adoption."""

    def __init__(self, binding, client_factory, root, *, upstream_root=None):
        self.binding, self.client_factory = deepcopy(binding), client_factory
        self.root, self.upstream_root = Path(root), upstream_root

    def induce(self, episode):
        return self._execute([_trajectory(episode)], scaling=False)

    def contrast(self, episodes):
        """Compare existing alternatives through original MaTTS induction only."""
        candidates = [_trajectory(episode) for episode in episodes]
        if len(candidates) < 2 or len({row["query"] for row in candidates}) != 1:
            raise ValueError("reasoning_bank_contrast_requires_same_task_alternatives")
        return self._execute(candidates, scaling=True)

    def _execute(self, episodes, *, scaling):
        source_file = "WebArena/induce_scaling.py" if scaling else "WebArena/induce_memory.py"
        constant_names = ["PARALLEL_SI"] if scaling else ["SUCCESSFUL_SI", "FAILED_SI"]
        constants, prompt_receipt = load_upstream_constants("WebArena/prompts/memory_instruction.py",
            constant_names, upstream_root=self.upstream_root)
        # Only load definitions actually used by these entrypoints. No cloud SDK
        # module is fabricated and no cloud initialization is executed.
        isolated_builtins = dict(vars(builtins))
        module, algorithm_receipt = load_upstream_functions(source_file, names=["format_trajectory", "main"],
            namespace={"os": os, "json": json, "__builtins__": isolated_builtins, **constants},
            upstream_root=self.upstream_root)
        source_receipts = [algorithm_receipt, prompt_receipt]
        payload = {"adapter_version": 1, "mode": "contrast" if scaling else "single",
                   "episodes": episodes, "upstream": source_receipts,
                   "binding": {key: self.binding[key] for key in
                               ("backend", "model", "max_output_tokens", "context_window_tokens", "reasoning")
                               if key in self.binding}}
        identifier = digest(payload)
        directory = self.root / "official-induction" / identifier
        directory.mkdir(parents=True, exist_ok=True)
        receipt_path, output_path = directory / "receipt.json", directory / "memory.jsonl"
        with FileLock(str(directory / "execution.lock")):
            if receipt_path.exists():
                saved = json.loads(receipt_path.read_text(encoding="utf-8"))
                if saved.get("input_sha256") != digest(payload) or digest(output_path.read_text(encoding="utf-8")) != saved.get("output_sha256"):
                    raise ValueError("reasoning_bank_induction_receipt_changed")
                return {**saved, "reused": True, "new_model_calls": 0}
            atomic_json(directory / "input.json", payload)
            task = "episode." + identifier
            reward_path = directory / "results" / task / "gateway_autoeval.json"
            reward_path.parent.mkdir(parents=True, exist_ok=True)
            episode = episodes[0]
            atomic_json(reward_path, [{"rm": 1 if episode["status"] == "success" else 0,
                                      "thoughts": episode["feedback"]}])
            gateway = _GatewayClient(self.binding, self.client_factory, self.root / "official-requests",
                                     source_receipts=source_receipts, scaling=scaling)
            provider_module = SimpleNamespace(CLIENT_DICT={"gateway": gateway})
            real_import = builtins.__import__

            def provider_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "utils.clients" and level == 0:
                    return provider_module
                return real_import(name, globals, locals, fromlist, level)

            def utf8_open(file, mode="r", *args, **kwargs):
                if "b" not in mode and "encoding" not in kwargs:
                    kwargs["encoding"] = "utf-8"
                return builtins.open(file, mode, *args, **kwargs)

            # The scaling script imports CLIENT_DICT inside main. Keep that
            # statement intact while resolving this provider boundary locally.
            isolated_builtins.update(__import__=provider_import, open=utf8_open)
            module.CLIENT_DICT = provider_module.CLIENT_DICT
            module.args = SimpleNamespace(result_dir=_SingleResultDirectory((directory / "results").as_posix()),
                task=task, output_path=str(output_path), criteria="autoeval", model="gateway",
                memory_mode="reasoningbank", num_samples=len(episodes))
            iterator = iter(episodes)

            def recorded_info(_path, _script_status):
                original = next(iterator)
                item = deepcopy(original)
                if scaling:
                    # The original script reopens the same path each iteration
                    # and reverses its reward flag. Actual per-alternative status
                    # and evidence come from recorded inputs, never that flag.
                    item["steps"] = [module.format_trajectory(item["think_list"], item["action_list"]),
                        "Recorded outcome: " + item["status"] + "\nAssessment source: " +
                        str(item["assessment_source"]) + "\nObservation: " + item["feedback"]]
                return item

            module.get_info = recorded_info
            # Original main appends; an interrupted attempt may already have
            # written its row. Reconstruct only this derived output using cached
            # responses, keeping the original input and paid request unchanged.
            output_path.write_text("", encoding="utf-8")
            module.main()
            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(rows) != 1:
                raise ValueError("reasoning_bank_induction_output_invalid")
            saved = {"id": identifier, "status": "candidate", "quality": "model_proposed_not_verified",
                "mode": payload["mode"], "upstream": source_receipts, "input_sha256": digest(payload),
                "output_sha256": digest(output_path.read_text(encoding="utf-8")), "memory": rows[0],
                "model_calls": gateway.calls, "new_model_calls": sum(not call["reused"] for call in gateway.calls),
                "additional_adaptations": ["recorded_trajectory_io_without_browsergym_pickle",
                    "provider_gateway_and_resumable_response_receipts", "single_result_directory_with_spaces",
                    "explicit_utf8_text_io_on_windows"] +
                    (["scaling_provider_instance_and_text_return", "actual_alternatives_instead_of_repeated_path",
                      "recorded_status_instead_of_inverted_script_reward"] if scaling else [])}
            atomic_json(receipt_path, saved)
            return {**saved, "reused": False}
