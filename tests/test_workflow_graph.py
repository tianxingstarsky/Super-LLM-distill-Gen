"""The workbench graph must match data dependencies in the execution engine."""

from lib.domain.workflow_graph import execution_graph


def test_canvas_shows_both_node_model_roles_and_keeps_live_progress():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    labels = {key: key for key in execution_graph(['sft'])[0]}
    bindings = {'sft': {'generation': {'backend': 'a', 'model': 'writer'},
                        'jev': {'backend': 'b', 'model': 'critic'}}}
    spec = canvas_spec(['sft'], {}, 'sft', labels, labels, bindings, language='en')
    node = next(row for row in spec['nodes'] if row['id'] == 'sft')
    assert node['models'] == ['Generate: a · writer', 'Review: b · critic']
    live = canvas_spec(['sft'], {'sft': {'status': 'running', 'done': 12000, 'total': 50000}},
                       'sft', labels, labels, bindings, language='en', live=True)
    node = next(row for row in live['nodes'] if row['id'] == 'sft')
    assert node['models'] == []
    assert node['subtitle'] == 'Running · 12,000 / 50,000'


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
