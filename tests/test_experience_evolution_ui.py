"""A node adopts a durable edition without changing frozen or source records."""
from streamlit.testing.v1 import AppTest
import pytest


def editor(tmp_path, *, english=False, model=True, many=False, feedback=False):
    script = '''from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application as real_google
from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.infrastructure.experience_evolution_model import ExperienceEvolutionModelPort
from lib.contexts.workflows.contracts import initial_experience_text
from lib.presentation.streamlit.experience_evolution_controls import render_experience_evolution
from tests.test_experience_evolution import Port, EPISODES
ROOT = Path(ROOT_VALUE)
st.session_state['ui_language'] = LANGUAGE_VALUE
key='workflow-agent-experience-text:fixture:reasoning'
enabled='workflow-agent-experience-enabled:fixture:reasoning'
class EditorPort(Port, ExperienceEvolutionModelPort):
    def __init__(self, root):
        Port.__init__(self, root)
        ExperienceEvolutionModelPort.__init__(self, self.repo, root)
if 'port' not in st.session_state:
    st.session_state['port']=EditorPort(ROOT/'experience-evolution'/sha256(b'fixture').hexdigest())
    st.session_state['bindings_seen']=[]
    st.session_state['google_calls']=0
    st.session_state[key]='保持任务相关的依据、反例与反思。'
    st.session_state[enabled]=True
    st.session_state['workflow-node-bindings:fixture']=BINDINGS_VALUE
    st.session_state['conversation-import:fixture:preview']={'import_id':'staged'}
    if FEEDBACK_VALUE:
        EvolveExperience(st.session_state['port']).record_observation('reasoning', {
            'id':'feedback','messages':[{'role':'user','content':'Continue.'},
                {'role':'assistant','content':'Done.'}],
            'review_observation':{'verdict':{'keep':True,'reason':'No new transferable evidence.'}}},
            run_id='a'*32)
class Client:
    client=SimpleNamespace(close=lambda:None)
    def chat(self, messages, **options):
        st.session_state['google_calls']+=1
        return '<no-new-memory/>'
def google_factory(binding=None):
    return real_google(binding,root=ROOT,user_key='fixture',client_factory=lambda selected:(Client(),'fixture'))
class Importer:
    def evolution_sources(self, identity, **options):
        assert identity=='staged' and options['max_records']==8 and options['max_chars']==60000
        records=deepcopy(EPISODES)
        if MANY_VALUE:
            records += [{'id':'extra-'+str(i),'messages':[{'role':'user','content':'Task '+str(i)},
                {'role':'assistant','content':'Observation '+str(i)}]} for i in range(8)]
        cursor=options.get('after_id')
        start=next(i+1 for i,r in enumerate(records) if r['id']==cursor) if cursor else 0
        selected=records[start:start+8]
        return {'purpose':'experience_evolution_not_training','import_id':identity,
            'source_name':'session.jsonl.zstd','source_sha256':'abc','records':selected,
            'after_id':cursor,'next_after_id':selected[-1]['id'] if start+len(selected)<len(records) else None,
            'position':{'after_index':start,'first_selected_index':start+1,'last_selected_index':start+len(selected)},
            'selected_ids':[e['id'] for e in selected],
            'counts':{'total_records':len(records),'selected_records':len(selected),'skipped_records':len(records)-start-len(selected)},
            'limits':options,'skipped':[]}
def factory(binding=None, **options):
    if binding is not None: st.session_state['bindings_seen'].append(deepcopy(binding))
    return EvolveExperience(st.session_state['port'])
def save_field(workspace, field):
    form=st.session_state.get('workflow-form-draft:'+workspace,{})
    st.session_state['workflow-form-draft:'+workspace]={**form,field:st.session_state[field]}
with patch('lib.bootstrap.experience_evolution.experience_evolution_application',factory), \\
     patch('lib.bootstrap.conversation_import.conversation_import_application',lambda:Importer()), \\
     patch('lib.bootstrap.google_reasoning_bank.google_reasoning_bank_application',google_factory):
    render_experience_evolution('fixture','reasoning','workflow.cot_native_joint',
        text_key=key,enabled_key=enabled,save_field=save_field)
st.text_area('Current experience',key=key)
from lib.presentation.streamlit.workflow_experience_controls import _restore_experience
st.button('Reset default',key='reset-default',on_click=_restore_experience,args=('fixture','reasoning',save_field))
'''
    binding = {"cot": {"generation": {"backend": "deepseek", "model": "deepseek-flash",
        "context_window_tokens": 131072, "max_output_tokens": 32768}}} if model else {}
    script = script.replace("ROOT_VALUE", repr(tmp_path.as_posix())).replace(
        "LANGUAGE_VALUE", repr("en" if english else "zh")).replace("BINDINGS_VALUE", repr(binding)).replace(
        "MANY_VALUE", repr(many)).replace("FEEDBACK_VALUE", repr(feedback))
    return AppTest.from_string(script).run(timeout=15)


def test_proposal_is_one_call_and_adoption_saves_an_editable_child_version(tmp_path):
    from tests.test_experience_evolution import EPISODES
    ui = editor(tmp_path)
    prefix = "experience-evolution:fixture:reasoning"
    text_key = "workflow-agent-experience-text:fixture:reasoning"
    assert not ui.exception
    ui.button(key=prefix + ":propose").click().run(timeout=15)
    assert not ui.exception and len(ui.session_state["port"].calls) == 1
    assert ui.session_state["bindings_seen"][0]["model"] == "deepseek-flash"
    candidate = ui.session_state[prefix + ":candidate"]
    assert candidate["episodes"] == EPISODES
    custom = "先保留关键入口。视觉结论用真实页面核对，失败原因只在相关条件下复用。"
    ui.text_area(key=prefix + ":text").set_value(custom).run()
    ui.button(key=prefix + ":adopt").click().run(timeout=15)
    assert not ui.exception
    assert ui.text_area(key=text_key).value == custom
    assert ui.session_state["workflow-form-draft:fixture"][text_key] == custom
    repo = ui.session_state["port"].repo
    editions = repo.list_versions("reasoning")
    assert len(editions) == 2
    adopted = repo.get_version(editions[0]["id"])
    assert adopted["status"] == "personal" and adopted["parent_id"] == candidate["id"]
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-agent-experience-parent:fixture:reasoning"] == adopted["id"]
    assert repo.get_version(candidate["id"])["episodes"] == EPISODES
    ui.selectbox(key=prefix + ":version").set_value(candidate["id"]).run()
    ui.button(key=prefix + ":load").click().run()
    assert not ui.exception
    assert ui.text_area(key=prefix + ":text").value == candidate["text"]
    assert ui.text_area(key=text_key).value == custom  # preview alone does not adopt
    assert len(ui.session_state["port"].calls) == 1


@pytest.mark.parametrize("english, label", [(True, "Thorough reasoning · initial v2"),
                                          (False, "充分推敲 · 初始 v2")])
def test_initial_style_and_manual_save_need_no_generation_model(tmp_path, english, label):
    from lib.contexts.workflows.contracts import initial_experience_text
    from lib.domain.agent_experience import INITIAL_STYLE_V2, default_experience_text
    ui = editor(tmp_path, english=english, model=False)
    prefix = "experience-evolution:fixture:reasoning"
    text_key = "workflow-agent-experience-text:fixture:reasoning"
    assert not ui.exception
    assert ui.button(key=prefix + ":propose").disabled
    if english:
        assert ui.button(key=prefix + ":initial").label == "Use initial style"
    ui.button(key=prefix + ":initial").click().run()
    assert not ui.exception
    assert ui.text_area(key=text_key).value == initial_experience_text("reasoning") == INITIAL_STYLE_V2
    ui.text_area(key=text_key).set_value("Keep only decisive evidence and counterexamples.").run()
    ui.button(key=prefix + ":save").click().run()
    assert not ui.exception and not ui.session_state["port"].calls
    versions = ui.session_state["port"].repo.list_versions("reasoning")
    assert [record["status"] for record in versions] == ["personal", "initial"]
    assert versions[1]["label"] == label
    del ui.session_state[prefix + ":parent"]
    ui.run()
    assert ui.session_state[prefix + ":parent"] == versions[0]["id"]
    ui.button(key="reset-default").click().run()
    assert not ui.exception
    assert ui.session_state[prefix + ":parent"] is None
    assert ui.text_area(key=text_key).value == default_experience_text("reasoning")
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-agent-experience-parent:fixture:reasoning"] is None


def test_next_group_proposes_from_that_cursor_without_replaying_first_group(tmp_path):
    ui = editor(tmp_path, many=True)
    prefix = "experience-evolution:fixture:reasoning"
    first = ui.session_state[prefix + ":sources"]
    assert first["selected_ids"][:2] == ["failed", "repaired"]
    ui.button(key=prefix + ":next-group").click().run()
    assert not ui.exception
    second = ui.session_state[prefix + ":sources"]
    assert second["after_id"] == first["next_after_id"]
    assert set(first["selected_ids"]).isdisjoint(second["selected_ids"])
    assert ui.button(key=prefix + ":next-group").disabled
    episode = second["records"][0]
    ui.session_state["port"].response = {"summary": "Use observed outcomes.", "rules": [
        {"principle": "Check the observed outcome.", "when": "The next task has a corresponding observation.",
         "avoid": "Do not infer success solely from a plan.", "evidence": [
             {"episode_id": episode["id"], "quote": episode["messages"][-1]["content"]}]}]}
    ui.button(key=prefix + ":propose").click().run()
    assert not ui.exception
    calls = ui.session_state["port"].calls
    assert len(calls) == 1 and calls[0]["episodes"] == second["records"]
    assert calls[0]["source_receipt"]["after_id"] == second["after_id"]
    ui.button(key=prefix + ":first-group").click().run()
    assert ui.session_state[prefix + ":sources"]["selected_ids"] == first["selected_ids"]


def test_google_no_update_preserves_current_edition_and_does_not_reclaim_feedback(tmp_path):
    ui = editor(tmp_path, feedback=True)
    prefix = "experience-evolution:fixture:reasoning"
    text_key = "workflow-agent-experience-text:fixture:reasoning"
    assert not ui.exception
    ui.button(key=prefix + ":save").click().run()
    previous = ui.session_state[prefix + ":candidate"]
    text = ui.text_area(key=text_key).value
    ui.button(key=prefix + ":google-learn").click().run(timeout=15)
    assert not ui.exception
    assert any("没有新增可泛化经验" in notice.value for notice in ui.info)
    assert ui.session_state[prefix + ":candidate"] == previous
    assert ui.text_area(key=text_key).value == text
    assert ui.session_state["google_calls"] == 1
    repository = ui.session_state["port"].repo
    assert [version["id"] for version in repository.list_versions("reasoning")] == [previous["id"]]
    assert repository.observation_progress("reasoning")["groups"][0]["state"] == "no_update"
    ui.button(key=prefix + ":google-learn").click().run()
    assert not ui.exception and ui.session_state["google_calls"] == 1
    assert any("暂无待学习反馈" in notice.value for notice in ui.info)


def test_personal_template_default_restore_detaches_the_saved_evolution_parent(tmp_path):
    from lib.domain.agent_experience import default_experience_text
    from tests.test_workflow_prompt_library import workbench, library_key
    ui = workbench(tmp_path)
    # The workbench now defaults to native generation, whose editor owns the
    # reasoning experience rather than the ordinary answer experience.
    scope = "experience:reasoning"
    parent_key = "workflow-agent-experience-parent:fixture:reasoning"
    text_key = "workflow-agent-experience-text:fixture:reasoning"
    parent = "a" * 32
    ui.session_state[parent_key] = parent
    form = ui.session_state["workflow-form-draft:fixture"]
    ui.session_state["workflow-form-draft:fixture"] = {**form, parent_key: parent}
    ui.text_area(key=text_key).set_value("My edited experience.").run()
    ui.button(key=library_key(scope, "restore")).click().run()
    assert not ui.exception
    assert ui.session_state[parent_key] is None
    assert ui.session_state["workflow-form-draft:fixture"][parent_key] is None
    assert ui.session_state["experience-evolution:fixture:reasoning:parent"] is None
    assert ui.text_area(key=text_key).value == default_experience_text("reasoning")
