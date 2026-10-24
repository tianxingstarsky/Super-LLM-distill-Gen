"""The actual node controls persist both designs and numeric limits."""
from streamlit.testing.v1 import AppTest

SCRIPT='''
import streamlit as st
from lib.presentation.streamlit.workflow_preference_settings import render_preference_settings, preference_design_snapshot
def save(workspace,key):
    from lib.domain.creation_draft import validate_creation_draft
    draft={**st.session_state.get('workflow-form-draft:'+workspace,{}),key:st.session_state[key]}
    st.session_state['workflow-form-draft:'+workspace]=validate_creation_draft(draft)
if st.checkbox('Show node', value=True):
    render_preference_settings('fixture',save_field=save)
st.json(preference_design_snapshot('fixture'))
'''


def test_default_drift_and_failure_reuse_survive_node_close_and_reopen():
    app=AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.radio(key='workflow-preference-mode:fixture').value=='associative_drift_short'
    assert app.checkbox(key='workflow-preference-reuse-failures:fixture').value is True
    app.checkbox(key='workflow-preference-reuse-failures:fixture').uncheck().run()
    app.radio(key='workflow-preference-mode:fixture').set_value('opposite_short').run()
    app.number_input(key='workflow-preference-negative-chars:fixture').set_value(100).run()
    app.number_input(key='workflow-preference-negative-ratio:fixture').set_value(15).run()
    app.checkbox[0].uncheck().run()
    app.checkbox[0].check().run()
    assert not app.exception
    assert app.radio(key='workflow-preference-mode:fixture').value=='opposite_short'
    assert app.checkbox(key='workflow-preference-reuse-failures:fixture').value is False
    assert app.number_input(key='workflow-preference-negative-chars:fixture').value==100
    assert app.number_input(key='workflow-preference-negative-ratio:fixture').value==15


def test_new_translations_replace_hot_reload_wrappers_without_stacking_them():
    from lib.presentation.streamlit import i18n
    from streamlit.delta_generator import DeltaGenerator
    import streamlit as st
    previous=i18n.ZH_EN
    try:
        i18n.install_streamlit_localization()
        underlying=st.caption.__wrapped__
        i18n.ZH_EN={**previous,'临时重载标签':'Fresh reload label'}
        i18n.install_streamlit_localization()
        assert st.caption.__wrapped__ is underlying
        assert not getattr(underlying,'_shujian_i18n_wrapper',False)
        assert DeltaGenerator._shujian_translation_scope is i18n.ZH_EN
        app=AppTest.from_string('''
import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language']='en'
install_streamlit_localization()
st.caption('临时重载标签')
''').run()
        assert not app.exception and app.caption[0].value=='Fresh reload label'
    finally:
        i18n.ZH_EN=previous
        i18n.install_streamlit_localization()
