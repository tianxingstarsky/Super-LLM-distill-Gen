"""The workbench graph must match data dependencies in the execution engine."""

from lib.domain.workflow_graph import execution_graph


def test_cpt_only_route_has_no_unselected_or_implicit_stages():
    nodes, edges = execution_graph(["cpt"])
    assert nodes == ("ingest", "cpt", "package")
    assert edges == (("ingest", "cpt"), ("cpt", "package"))


def test_preference_and_cot_depend_on_internal_sft_candidates():
    nodes, edges = execution_graph(["dpo", "orpo", "rlaif", "cot"])
    assert nodes == ("ingest", "sft", "preference", "cot", "package")
    assert ("ingest", "sft") in edges
    assert ("sft", "preference") in edges
    assert ("sft", "cot") in edges
    assert ("sft", "package") not in edges  # SFT was not requested for export.
    assert ("preference", "package") in edges
    assert ("cot", "package") in edges


def test_requested_sft_and_independent_branches_feed_package():
    nodes, edges = execution_graph(["agent", "multiturn", "sft", "gsm8k"])
    assert nodes == ("ingest", "sft", "multiturn", "agent", "gsm8k", "package")
    assert all(("ingest", key) in edges for key in ("sft", "multiturn", "agent", "gsm8k"))
    assert all((key, "package") in edges for key in ("sft", "multiturn", "agent", "gsm8k"))
