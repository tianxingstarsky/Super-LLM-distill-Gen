"""Quality presentation runs with supplied applications, without console globals."""
import ast
import json
import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from lib.presentation.streamlit.i18n import translate

ROOT=Path(__file__).resolve().parents[1]


def test_quality_page_has_no_storage_or_composition_imports():
    tree=ast.parse((ROOT/'lib/presentation/streamlit/quality_page.py').read_text(encoding='utf-8'))
    modules=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(module.startswith(('lib.bootstrap','lib.infrastructure','lib.workspace')) for module in modules)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_quality_page_reports_raw_issue_and_filters_with_injected_services(tmp_path, language):
    path=tmp_path/'samples.jsonl'
    rows=[{'id':'broken','messages':'invalid original'},
          {'id':'incomplete','messages':[{'role':'user','content':'question'}]}]
    source='\n'.join(json.dumps(row) for row in rows)+'\n'
    path.write_text(source,encoding='utf-8')
    script=f'''
from pathlib import Path
import streamlit as st
st.session_state['ui_language']={language!r}
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
from lib.application.release_service import ReleaseApplication
from lib.infrastructure.sample_preview import RawSamplePreview
from lib.presentation.streamlit.quality_page import render_quality_page
class Workflow:
    def list_runs(self): return []
class Driver:
    def raw_preview_samples(self,path): return RawSamplePreview(path)
    def review_decisions(self,dataset):
        assert dataset == "injected-dataset"
        return []
render_quality_page(Workflow(),ReleaseApplication(Driver()),"isolated-ui",Path({str(tmp_path)!r}),
                    "injected-dataset",lambda:[Path({str(path)!r})])
'''
    ui=AppTest.from_string(script,default_timeout=15).run()
    assert not ui.exception
    assert ui.code[0].value.find('invalid original')>=0
    filters=[item for item in ui.selectbox if item.label==translate('问题类型', language)]
    filters[0].select('incomplete_answer').run()
    assert not ui.exception
    matches=[item for item in ui.selectbox if item.label==translate('定位问题样本', language)]
    assert matches[0].value==0
    assert matches[0].format_func(0)==("Answer is incomplete · incomplete" if language=="en" else "回答未完成 · incomplete")
    assert path.read_text(encoding='utf-8')==source
