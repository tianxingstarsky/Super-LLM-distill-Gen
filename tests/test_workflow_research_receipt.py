"""Public search feedback stays on the workflow run page."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_page import render_run, GRAPH_LABELS

st.session_state['ws'] = 'fixture'
class Application:
    def state(self, run_id):
        return {'name': 'Public research fixture', 'status': 'running',
                'targets': ['sft'], 'attempt': 1,
                'stages': {key: {'label': label, 'status': 'running' if key == 'ingest' else 'pending',
                                 'done': 0, 'total': 1} for key, label in GRAPH_LABELS.items()},
                'events': [{'kind': 'web_search_completed', 'stage': 'ingest', 'at': '2026-01-01T00:00:00Z'}],
                'web_research': {'status': 'completed', 'results': 1}}
    def recipe(self, run_id):
        return {'targets': ['sft'], 'sources': [], 'brief': 'Create maintenance tasks',
                'web_research': {'provider': 'brave', 'query': 'equipment safety', 'count': 3}}
    def web_research_results(self, run_id):
        return {'query': 'equipment safety',
                'results': [{'title': 'Public maintenance guide',
                             'url': 'https://example.org/guide',
                             'snippet': 'Check power before service.'}]}
    def is_active(self, run_id): return True

render_run(Application(), 'fixture', lambda args: None)
'''
SCRIPT = SCRIPT.encode('ascii', 'backslashreplace').decode('ascii')


def test_run_shows_search_receipt_and_planning_lead_without_page_change():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert any(item.value == 'equipment safety' for item in ui.code)
    assert any(item.value == 'Check power before service.' for item in ui.caption)
    assert any(item.label == '公开线索' and item.value == '1' for item in ui.metric)
