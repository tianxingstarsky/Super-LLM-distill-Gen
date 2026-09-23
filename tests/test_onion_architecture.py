"""Guard inward dependency direction for the migrated workflow slice."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_domain_has_no_application_or_infrastructure_dependencies():
    files = list((ROOT / "lib/domain").glob("*.py"))
    assert files
    for path in files:
        dependencies = imported_modules(path)
        assert not any(name.startswith(("lib.application", "lib.infrastructure", "lib.bootstrap", "lib.presentation"))
                       for name in dependencies), path.name
        assert "streamlit" not in dependencies, path.name


def test_application_depends_only_inward_and_on_ports():
    for path in (ROOT / "lib/application").glob("*.py"):
        dependencies = imported_modules(path)
        assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.presentation"))
                       for name in dependencies), path.name
        assert "streamlit" not in dependencies, path.name


def test_streamlit_workflow_page_uses_application_contract_not_engine_adapter():
    path = ROOT / "lib/presentation/streamlit/workflow_page.py"
    dependencies = imported_modules(path)
    assert "lib.application.workflow_service" in dependencies
    assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.workflow"))
                   for name in dependencies)
    assert "lib.workspace" not in dependencies


def test_streamlit_preference_review_page_uses_application_contract():
    dependencies = imported_modules(ROOT / "lib/presentation/streamlit/preference_review_page.py")
    assert "lib.application.preference_review_service" in dependencies
    assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.workflow"))
                   for name in dependencies)


def test_streamlit_corpus_review_page_uses_application_contract():
    dependencies = imported_modules(ROOT / "lib/presentation/streamlit/corpus_review_page.py")
    assert "lib.application.corpus_review_service" in dependencies
    assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.workflow"))
                   for name in dependencies)


def test_streamlit_sft_review_page_uses_application_contract():
    dependencies = imported_modules(ROOT / "lib/presentation/streamlit/sft_review_page.py")
    assert "lib.application.sft_review_service" in dependencies
    assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.workflow"))
                   for name in dependencies)


def test_streamlit_package_page_uses_application_contract():
    dependencies = imported_modules(ROOT / "lib/presentation/streamlit/package_page.py")
    assert "lib.application.workflow_service" in dependencies
    assert not any(name.startswith(("lib.infrastructure", "lib.bootstrap", "lib.workflow"))
                   for name in dependencies)


def test_workflow_application_limits_preview_and_delegates_to_port():
    from lib.application.workflow_service import WorkflowApplication

    class Driver:
        def artifact_preview(self, run_id, target, limit):
            return [run_id, target, limit]

    app = WorkflowApplication(Driver())
    assert app.artifact_preview("run-1", "sft", 1000) == ["run-1", "sft", 100]
    assert app.artifact_preview("run-1", "sft", -10) == ["run-1", "sft", 0]


def test_quality_and_export_entrypoints_use_release_application():
    for filename in ("lib/cli.py", "lib/webapp.py"):
        dependencies = imported_modules(ROOT / filename)
        assert "lib.bootstrap.releases" in dependencies, filename
        assert "lib.quality" not in dependencies, filename
    quality_imports = imported_modules(ROOT / "lib/domain/release_quality.py")
    assert not any(name.startswith(("lib.application", "lib.infrastructure", "lib.bootstrap",
                                    "lib.presentation", "lib.review")) for name in quality_imports)
