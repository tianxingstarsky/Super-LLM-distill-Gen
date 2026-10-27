"""Enforce context ownership, dependency direction and read/execution isolation."""
from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTEXTS = ROOT / "lib/contexts"
FRAMEWORKS = {"streamlit", "openai", "anthropic", "requests", "httpx", "sqlite3", "subprocess", "docling", "langfuse", "tenacity"}


def dependencies(text: str, module: str) -> set[str]:
    """Inspect nested/relative imports and literal compatibility-module redirects."""
    result = set()
    package = module.rsplit(".", 1)[0]
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            result.add(base)
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                if (ROOT / (candidate.replace(".", "/") + ".py")).exists():
                    result.add(candidate)
        elif (isinstance(node, ast.Call) and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
              and ((isinstance(node.func, ast.Name) and node.func.id in {"_import_module", "import_module", "__import__"})
                   or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"))):
            result.add(node.args[0].value)
    return result


def module_name(path):
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def canonical_dependency(module):
    """An old import alias must not hide a cross-context private dependency."""
    visited = set()
    while module not in visited:
        visited.add(module)
        path = ROOT / (module.replace(".", "/") + ".py")
        if not path.is_file():
            break
        tree = ast.parse(path.read_text(encoding="utf-8"))
        redirects = []
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if (isinstance(call.func, ast.Name) and call.func.id == "_import_module"
                    and call.args and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and any(isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Attribute)
                            and target.value.attr == "modules" for target in node.targets)):
                redirects.append(call.args[0].value)
        if len(redirects) != 1:
            break
        module = redirects[0]
    return module


def layer(module):
    parts = module.split(".")
    if parts[:2] == ["lib", "contexts"] and len(parts) > 3:
        return parts[3]
    return parts[1] if parts and parts[0] == "lib" and len(parts) > 1 else None


def inward_errors(owner_layer, deps):
    forbidden = {"infrastructure", "bootstrap", "presentation"}
    if owner_layer in {"domain", "contracts"}:
        forbidden |= {"application", "api"}
    errors = {dep for dep in deps if layer(dep) in forbidden or dep.split(".")[0] in FRAMEWORKS}
    if owner_layer in {"domain", "contracts"}:
        errors |= {dep for dep in deps if dep.startswith("lib.") and
                   not dep.startswith(("lib.domain", "lib.contexts", "lib.prompts"))}
    return errors


@pytest.mark.parametrize("text,module,expected", [
    ("from ..infrastructure import driver", "lib.contexts.workflows.domain.rule", "lib.contexts.workflows.infrastructure.driver"),
    ("from lib import llm_client", "lib.domain.rule", "lib.llm_client"),
    ('import_module("lib.infrastructure.training_workflow")', "lib.domain.rule", "lib.infrastructure.training_workflow"),
    ("def run():\n import streamlit", "lib.application.rule", "streamlit"),
])
def test_boundary_guard_recognizes_hidden_outward_imports(text, module, expected):
    deps = dependencies(text, module)
    assert expected in deps
    assert expected in inward_errors("domain", deps)


def test_all_domain_and_application_modules_depend_inward():
    files = [*(ROOT / "lib/domain").rglob("*.py"), *(ROOT / "lib/application").rglob("*.py")]
    files += [p for p in CONTEXTS.rglob("*.py") if layer(module_name(p)) in {"domain", "contracts", "application", "api"}]
    violations = {}
    for path in files:
        module = module_name(path)
        errors = inward_errors(layer(module), dependencies(path.read_text(encoding="utf-8"), module))
        if errors:
            violations[str(path.relative_to(ROOT))] = sorted(errors)
    assert not violations, violations


def test_contexts_use_published_contracts_to_cross_business_boundaries():
    violations = []
    for path in CONTEXTS.rglob("*.py"):
        owner = path.relative_to(CONTEXTS).parts[0]
        for dep in dependencies(path.read_text(encoding="utf-8"), module_name(path)):
            parts = canonical_dependency(dep).split(".")
            if parts[:2] == ["lib", "contexts"] and len(parts) > 3 and parts[2] != owner:
                if parts[3] not in {"api", "contracts"}:
                    violations.append((str(path.relative_to(ROOT)), dep))
    assert not violations, violations


def test_boundary_guard_follows_legacy_aliases_to_their_actual_context():
    assert canonical_dependency("lib.domain.backend_config") == "lib.contexts.models.domain.configuration"
    assert canonical_dependency("lib.contexts.models.contracts") == "lib.contexts.models.contracts"


def test_context_relative_module_imports_resolve_after_moves():
    """A package move must include nested relative imports, even for optional SDK paths."""
    missing = []
    for path in CONTEXTS.rglob("*.py"):
        package = module_name(path).rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or not node.level or not node.module:
                continue
            module = importlib.util.resolve_name("." * node.level + node.module, package)
            target = ROOT / module.replace(".", "/")
            if not target.with_suffix(".py").is_file() and not (target / "__init__.py").is_file():
                missing.append((str(path.relative_to(ROOT)), module))
    assert not missing, missing


def test_composition_uses_separate_adapters_and_narrow_use_cases(tmp_path):
    from lib.bootstrap.workflows import workflow_application
    from lib.contexts.workflows.api import WorkflowRunApplication
    from lib.contexts.datasets.api import DatasetApplication
    from lib.contexts.review.api import AgentReviewApplication

    app = workflow_application(tmp_path, tmp_path)
    assert isinstance(app, WorkflowRunApplication)
    assert isinstance(app.datasets, DatasetApplication)
    assert isinstance(app.review, AgentReviewApplication)
    assert not hasattr(app._driver, "artifact_file")
    assert not hasattr(app._driver, "agent_review_decide")
    assert not hasattr(app.datasets._driver, "execute")
    assert not hasattr(app.datasets, "create_run")
    assert not hasattr(app.review, "start_bundle")
    assert len({id(app._driver), id(app.datasets._driver), id(app.review._driver)}) == 3
    for path in (ROOT / "lib/bootstrap").glob("*.py"):
        assert "lib.infrastructure.workflow_driver" not in dependencies(path.read_text(encoding="utf-8"), module_name(path))


@pytest.mark.parametrize("old,new", [
    ("lib.domain.dataset_assets", "lib.contexts.datasets.domain.assets"),
    ("lib.application.asset_catalog_service", "lib.contexts.datasets.application.asset_catalog"),
    ("lib.application.asset_catalog_ports", "lib.contexts.datasets.application.asset_catalog_ports"),
    ("lib.infrastructure.asset_catalog_driver", "lib.contexts.datasets.infrastructure.asset_catalog"),
    ("lib.domain.sample_distribution", "lib.contexts.datasets.domain.distribution"),
    ("lib.domain.workflow_targets", "lib.contexts.workflows.domain.targets"),
    ("lib.domain.sft_review", "lib.contexts.review.domain.sft_review"),
    ("lib.application.backend_service", "lib.contexts.models.application.service"),
    ("lib.application.sft_review_service", "lib.contexts.review.application.sft_review_service"),
    ("lib.infrastructure.workflow_archive", "lib.contexts.datasets.infrastructure.archive"),
    ("lib.infrastructure.agent_review_driver", "lib.contexts.review.infrastructure.agent_review_driver"),
    ("lib.infrastructure.latex_document", "lib.contexts.documents.domain.latex"),
    ("lib.infrastructure.docling_runtime", "lib.contexts.documents.infrastructure.docling.runtime"),
    ("lib.infrastructure.docling_inline_formulas", "lib.contexts.documents.infrastructure.docling.inline_formulas"),
    ("lib.infrastructure.workflow_production", "lib.contexts.workflows.infrastructure.production_runtime"),
    ("lib.infrastructure.workflow_review_repair", "lib.contexts.review.infrastructure.automatic_runtime"),
    ("lib.domain.review_repair", "lib.contexts.review.domain.repair"),
    ("lib.llm_client", "lib.contexts.models.infrastructure.request_runtime"),
    ("lib.model_tool_calls", "lib.contexts.models.infrastructure.tool_transport"),
    ("lib.infrastructure.reasoning_prefill_history", "lib.contexts.review.domain.reasoning_history"),
])
def test_compatibility_paths_resolve_to_one_implementation(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


def test_dataset_read_path_works_when_generation_engine_and_sdks_are_unavailable(tmp_path):
    # A fresh interpreter detects indirect imports that an already-warm pytest
    # process would hide. The real filesystem adapter reads a verified fixture.
    script = r'''
import hashlib, importlib.abc, json, pathlib, sys
class BlockExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"lib.infrastructure.training_workflow", "lib.llm_client", "openai", "anthropic", "docling", "streamlit"}:
            raise AssertionError("read path imported execution dependency: " + fullname)
sys.meta_path.insert(0, BlockExecution())
from lib.bootstrap.datasets import dataset_application
root = pathlib.Path(sys.argv[1])
run_id = "a" * 32
folder = root / "workflows" / run_id / "artifacts"
folder.mkdir(parents=True)
values = {"sft.jsonl": '{"messages":[{"role":"user","content":"Hi"},{"role":"assistant","content":"Hello","reasoning_content":"Preserved rationale"}]}\n'.replace('\\n', '\n'),
          "quality.json": '{"targets":{"sft":{"eligible":1}}}'}
for name, value in values.items():
    (folder / name).write_text(value, encoding="utf-8")
manifest = {"status":"complete", "counts":{"sft":1}, "sha256":{name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in values}}
(folder / "manifest.json").write_text(json.dumps(manifest))
app = dataset_application(root, root)
assert app.package_contents(run_id)["manifest"] == manifest
assert app.artifact_preview(run_id, "sft")[0]["messages"][1]["reasoning_content"] == "Preserved rationale"
from lib.contexts.datasets.infrastructure.statistics import build_statistics
assert build_statistics(folder.parent)["total"]["rows"] == 1
print("read, preview and statistics require no generation engine")
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_legacy_workflow_facade_uses_injected_dataset_and_review_ports():
    from lib.application.workflow_service import WorkflowApplication

    class Runtime:
        def state(self, run_id):
            return {"id": run_id, "owner": "runtime"}

    class Datasets:
        def artifact_preview(self, run_id, target, limit=3, offset=0):
            return [run_id, target, limit, offset]

    class Review:
        def agent_review_decide(self, run_id, candidate_id, **decision):
            return {"run": run_id, "candidate": candidate_id, **decision}

    app = WorkflowApplication(Runtime(), datasets=Datasets(), review=Review())
    assert app.state("run")["owner"] == "runtime"
    assert app.artifact_preview("run", "sft", 3, 2) == ["run", "sft", 3, 2]
    assert app.agent_review_decide("run", "candidate", decision="approved")["decision"] == "approved"


def test_execution_and_corpus_use_cases_need_no_concrete_runtime(tmp_path):
    script = r'''
import importlib.abc, sys
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(("lib.infrastructure", "lib.bootstrap", "lib.presentation"))
                or ".infrastructure" in fullname
                or fullname.split(".")[0] in {"streamlit", "docling", "pypdf", "docx", "openai", "anthropic"}):
            raise AssertionError("use case loaded an adapter: " + fullname)
sys.meta_path.insert(0, NoAdapters())
from lib.contexts.workflows.application.execute_run import ExecuteWorkflowRun
from lib.contexts.workflows.application.generate_batch import GenerateDatasetBatch
from lib.contexts.workflows.domain.execution import BatchOutcome, TargetOutcome
from lib.contexts.documents.api import PrepareDocumentCorpus, DocumentSource
class Reader:
    def read_text(self, source_path, **kwargs):
        return "# Knowledge\n\nA table keeps its header."
result = PrepareDocumentCorpus(Reader()).execute(DocumentSource("memory", "guide.md", ".md"))
assert result.entries and result.source_chars > 0
assert BatchOutcome((TargetOutcome("sft", 1),), frozenset(), 0, 0, 0, False).status == "completed"
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_dialogue_production_and_review_execute_without_adapters_or_model_sdks():
    script = r'''
import importlib.abc, sys
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(("lib.infrastructure", "lib.bootstrap", "lib.presentation", "lib.llm_client"))
                or ".infrastructure" in fullname
                or fullname.split(".")[0] in {"streamlit", "docling", "openai", "anthropic", "httpx", "requests"}):
            raise AssertionError("application loaded an external adapter: " + fullname)
sys.meta_path.insert(0, NoAdapters())
from tests.test_pipeline_use_cases import (
    test_dialogue_port_keeps_separate_actors_and_every_turn_reasoning,
    test_million_row_production_use_case_only_handles_bounded_windows_and_receipts,
    test_review_repairs_and_rescores_exact_body_without_changing_task,
)
test_dialogue_port_keeps_separate_actors_and_every_turn_reasoning()
test_million_row_production_use_case_only_handles_bounded_windows_and_receipts()
test_review_repairs_and_rescores_exact_body_without_changing_task()
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr



def test_generation_and_request_use_cases_execute_without_adapters_or_model_sdks():
    script = r'''
import importlib.abc, sys
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(("lib.infrastructure", "lib.bootstrap", "lib.presentation", "lib.llm_client"))
                or ".infrastructure" in fullname
                or fullname in {"lib.model_tool_calls", "lib.model_request_scheduler", "lib.model_streaming"}
                or fullname.split(".")[0] in {"streamlit", "docling", "openai", "anthropic", "httpx", "requests"}):
            raise AssertionError("application loaded an external adapter: " + fullname)
sys.meta_path.insert(0, NoAdapters())
from tests.test_generation_node_use_cases import (
    test_sft_retries_schema_without_spending_on_review_and_keeps_request_keys,
    test_directed_sft_keeps_exact_assigned_question_and_rejects_foreign_evidence,
    test_native_cot_retains_provider_bytes_including_whitespace,
    test_styled_cot_keeps_unassessed_answer_out_of_request_without_erasing_input,
)
from tests.test_node_request_application import (
    test_node_request_reuses_captured_checkpoint_without_new_model_call,
    test_node_request_routes_generation_check_but_keeps_review_role,
)
# Exercise request policy with a scripted port, independently of the external
# retry engine. The real Tenacity path is tested in model/runtime regression.
import tests.test_model_request_use_cases as request_tests
from lib.contexts.models.application.retry_port import RetryDirective
class TwoAttempts:
    def run(self, action, *, attempts, wait):
        first = action(0)
        return action(int(first.counted)) if isinstance(first, RetryDirective) else first
request_tests.TenacityRetry = TwoAttempts
from tests.test_model_request_use_cases import (
    test_rate_limit_defers_before_releasing_slot_and_retries_with_clean_reservation,
    test_json_capability_rejection_does_not_consume_retry_or_reuse_live_reservation,
    test_json_retries_only_invalid_body_and_publishes_only_successful_attempt_details,
    test_tool_decision_bills_invalid_response_and_never_executes_returned_tools,
)
test_sft_retries_schema_without_spending_on_review_and_keeps_request_keys()
test_directed_sft_keeps_exact_assigned_question_and_rejects_foreign_evidence()
test_native_cot_retains_provider_bytes_including_whitespace()
test_styled_cot_keeps_unassessed_answer_out_of_request_without_erasing_input()
test_node_request_reuses_captured_checkpoint_without_new_model_call()
test_node_request_routes_generation_check_but_keeps_review_role()
test_rate_limit_defers_before_releasing_slot_and_retries_with_clean_reservation()
test_json_capability_rejection_does_not_consume_retry_or_reuse_live_reservation()
test_json_retries_only_invalid_body_and_publishes_only_successful_attempt_details()
test_tool_decision_bills_invalid_response_and_never_executes_returned_tools()
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
