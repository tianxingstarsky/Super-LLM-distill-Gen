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


def test_setup_canvas_only_asks_for_models_when_the_source_uses_them():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    labels = {key: key for key in execution_graph(['cpt'])[0]}
    document = canvas_spec(['cpt'], {}, 'cpt', labels, labels, language='en',
                           source_mode='文档资料')
    assert all(node['subtitle'] == 'No model needed · inspect step' for node in document['nodes'])
    open_brief = canvas_spec(['cpt'], {}, 'cpt', labels, labels, language='en',
                             source_mode='开放需求')
    subtitles = {node['id']: node['subtitle'] for node in open_brief['nodes']}
    assert subtitles['ingest'] == 'Select to configure'
    assert subtitles['cpt'] == 'Select to configure'
    assert subtitles['package'] == 'No model needed · inspect step'


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


def test_optional_trim_processes_only_requested_reasoning_exports():
    nodes, edges = execution_graph(["sft", "cot", "dpo", "cpt"], reasoning_trim=True)
    assert nodes[-2:] == ("trim", "package")
    assert ("sft", "trim") not in edges and ("cot", "trim") in edges
    assert ("trim", "package") in edges
    assert ("sft", "package") not in edges and ("cot", "package") not in edges
    assert ("sft", "cot") in edges and ("sft", "preference") in edges
    assert ("preference", "package") in edges and ("cpt", "package") in edges
    assert "trim" not in execution_graph(["dpo"], reasoning_trim=True)[0]


def test_combined_sft_cot_route_waits_for_cot_but_preserves_historical_graphs():
    for trim in (False, True):
        destination = "trim" if trim else "package"
        _, edges = execution_graph(["sft", "cot"], reasoning_trim=trim)
        assert ("sft", "cot") in edges
        assert ("cot", destination) in edges
        assert ("sft", destination) not in edges
        for version in (0, 14):
            _, legacy_edges = execution_graph(["sft", "cot"], reasoning_trim=trim,
                                               recipe_version=version)
            assert ("sft", destination) in legacy_edges
            assert ("cot", destination) in legacy_edges


def test_canvas_marks_combined_sft_candidates_and_names_the_actual_cot_operation():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    targets = ["sft", "cot"]
    labels = {key: key for key in execution_graph(targets)[0]}
    for enabled, label in ((False, "CoT 推理核验"), (True, "CoT 推理生成")):
        generation = {"cot": {"enabled": enabled}}
        for live in (False, True):
            current = canvas_spec(targets, {}, "cot", labels, labels,
                                  node_generation=generation, live=live)
            by_id = {node["id"]: node for node in current["nodes"]}
            assert by_id["sft"]["intermediate"] is True
            assert by_id["cot"]["label"] == label
            assert ("sft", "package") not in current["edges"]
            legacy = canvas_spec(targets, {}, "cot", labels, labels,
                                 node_generation=generation, live=live, recipe_version=14)
            assert not next(node for node in legacy["nodes"] if node["id"] == "sft")["intermediate"]
            assert ("sft", "package") in legacy["edges"]


def test_node_route_description_only_promises_cot_finalization_for_new_combined_runs():
    from lib.presentation.streamlit.workflow_generation_settings import reasoning_route_description
    for node in ("sft", "cot"):
        assert reasoning_route_description(node, ["sft", "cot"])
        assert not reasoning_route_description(node, ["sft", "cot"], recipe_version=14)
        assert not reasoning_route_description(node, ["sft"])
        assert not reasoning_route_description(node, ["cot"])
    assert not reasoning_route_description("package", ["sft", "cot"])


def test_trim_canvas_has_readable_non_overlapping_nodes_and_correct_links():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    targets = ["sft", "cot", "dpo"]
    labels = {key: key for key in execution_graph(targets, reasoning_trim=True)[0]}
    spec = canvas_spec(targets, {}, "trim", labels, labels, reasoning_trim=True)
    by_id = {node["id"]: node for node in spec["nodes"]}
    assert by_id["cot"]["x"] + 218 < by_id["trim"]["x"]
    assert by_id["trim"]["x"] + 218 < by_id["package"]["x"]
    assert all(0 <= node["x"] <= spec["width"] - 218 for node in spec["nodes"])
    assert spec["edges"] == execution_graph(targets, reasoning_trim=True)[1]
