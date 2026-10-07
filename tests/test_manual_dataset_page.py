"""Manual authoring keeps drafts safe and uses only application-resolved media."""
from io import BytesIO
import json
import re
from zipfile import ZipFile

from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.manual_dataset_page import _attachment_payload, _error


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.i18n import install_streamlit_localization
from lib.presentation.streamlit.manual_dataset_page import render_manual_datasets
install_streamlit_localization()
class Application:
    def list_datasets(self):
        if st.session_state.get('read-fails'):
            raise OSError('C:/SECRET/private.json')
        return st.session_state.get('datasets', [])
    def create_dataset(self, name):
        if st.session_state.get('create-fails'):
            raise OSError('C:/SECRET/private.json')
        dataset_id = 'a' * 32
        st.session_state['datasets'] = [{'id':dataset_id,'name':name,'count':0,'updated_at':'2026-10-07'}]
        return dataset_id
    def append_sample(self, dataset_id, **kwargs):
        st.session_state['append-calls'] = st.session_state.get('append-calls',0)+1
        if st.session_state.get('save-fails'):
            raise OSError('C:/SECRET/private.json')
        st.session_state['saved'] = {'dataset_id':dataset_id, **kwargs}
        row = {'id':'b'*32, **kwargs, 'media':[], 'metadata':{'review_status':'unreviewed'}}
        st.session_state['rows'] = [row]
        st.session_state['datasets'] = [{**st.session_state['datasets'][0], 'count':1}]
        return row
    def list_samples(self, dataset_id, *, limit, offset):
        st.session_state['sample-read'] = (dataset_id,limit,offset)
        return st.session_state.get('rows',[])
    def read_media(self, dataset_id, media_id):
        st.session_state['media-read'] = (dataset_id, media_id)
        raise ValueError('manual_media_integrity_failed')
    def export_dataset(self, dataset_id):
        st.session_state['export-calls'] = st.session_state.get('export-calls',0)+1
        if st.session_state.get('export-fails'):
            raise OSError('C:/SECRET/private.json')
        return b'actual zip bytes'
if not st.session_state.get('other-mode'):
    render_manual_datasets(Application(), 'cache', show_title=False)
'''


def author_ui(*, language='zh', count=0):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['ui_language'] = language
    ui.session_state['datasets'] = [{'id':'a'*32,'name':'开始生成','count':count,'updated_at':'2026-10-07'}]
    ui.session_state['rows'] = [{'id':'b'*32,'question':'开始生成','answer':'<script>literal</script>',
                                'system':'', 'media':[]}]
    ui.run()
    assert not ui.exception
    return ui


def button(ui, label):
    return next(item for item in ui.button if item.label == label)


def test_empty_library_create_dataset_then_author_in_same_page():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert button(ui, '创建数据集').disabled
    ui.text_input[0].set_value('My image examples').run()
    button(ui, '创建数据集').click().run()
    assert not ui.exception
    assert len(ui.text_area) == 3
    assert ui.selectbox[0].value == 'a'*32
    assert ui.text_input[0].value == ''
    assert button(ui, '创建数据集').disabled
    assert 'append-calls' not in ui.session_state


def test_save_clears_successful_draft_and_never_marks_it_reviewed():
    ui = author_ui()
    assert button(ui, '保存并继续下一条').disabled
    ui.text_area[0].set_value('What is in this picture?').run()
    ui.text_area[1].set_value('A red cube.').run()
    ui.text_area[2].set_value('Describe visible objects.').run()
    button(ui, '保存并继续下一条').click().run()
    assert not ui.exception
    assert ui.session_state['append-calls'] == 1
    assert ui.session_state['saved']['question'] == 'What is in this picture?'
    assert ui.session_state['saved']['system'] == 'Describe visible objects.'
    assert all(item.value == '' for item in ui.text_area)
    assert button(ui, '保存并继续下一条').disabled
    assert '待复核' in ui.success[0].value
    ui.run()
    assert ui.session_state['append-calls'] == 1


def test_failure_retains_draft_and_hides_storage_paths():
    ui = author_ui()
    ui.text_area[0].set_value('Keep this question.').run()
    ui.text_area[1].set_value('Keep this answer.').run()
    ui.session_state['save-fails'] = True
    button(ui, '保存并继续下一条').click().run()
    assert not ui.exception
    assert ui.text_area[0].value == 'Keep this question.'
    assert ui.text_area[1].value == 'Keep this answer.'
    assert '输入仍保留' in ui.error[0].value
    assert 'SECRET' not in ui.error[0].value
    ui.session_state['save-fails'] = False
    button(ui, '保存并继续下一条').click().run()
    assert not ui.exception and ui.session_state['saved']['answer'] == 'Keep this answer.'


def test_switching_mode_and_dataset_keeps_each_unfinished_text_draft():
    ui = author_ui()
    ui.text_area[0].set_value('First question').run()
    ui.text_area[1].set_value('First answer').run()
    ui.session_state['other-mode'] = True
    ui.run()
    assert not ui.text_area
    ui.session_state['other-mode'] = False
    ui.run()
    assert not ui.exception and ui.text_area[0].value == 'First question'
    ui.session_state['datasets'] = ui.session_state['datasets'] + [
        {'id':'d'*32,'name':'Second','count':0,'updated_at':'2026-10-07'}]
    ui.run()
    ui.selectbox[0].select('d'*32).run()
    ui.text_area[0].set_value('Second question').run()
    ui.selectbox[0].select('a'*32).run()
    assert not ui.exception and ui.text_area[0].value == 'First question'
    assert ui.text_area[1].value == 'First answer'
    ui.selectbox[0].select('d'*32).run()
    assert ui.text_area[0].value == 'Second question'


def test_user_text_and_names_stay_literal_in_english():
    ui = author_ui(language='en', count=1)
    assert ui.selectbox[0].options == ['开始生成']
    assert any(item.value == '开始生成' for item in ui.text)
    assert any(item.value == '<script>literal</script>' for item in ui.text)
    assert not any('<script>literal</script>' in item.value for item in ui.get('html'))
    labels = [item.label for kind in ('button', 'selectbox', 'text_input', 'text_area', 'expander')
              for item in ui.get(kind)]
    assert not re.search(r'[\u4e00-\u9fff]', ' '.join(labels))
    button(ui, 'Prepare export').click().run()
    assert ui.get('download_button')[0].label == 'Download images and examples'


def test_saved_media_uses_ids_and_failure_does_not_expose_paths():
    ui = author_ui(count=1)
    ui.session_state['rows'] = [{'id':'b'*32,'question':'Image?','answer':'Cube','system':'',
                                'media':[{'id':'c'*32,'kind':'image','name':'cube.png','path':'C:/SECRET/key'}]}]
    ui.run()
    assert not ui.exception
    assert ui.session_state['media-read'] == ('a'*32,'c'*32)
    assert any('完整性校验失败' in item.value for item in ui.warning)
    assert 'SECRET' not in ' '.join(item.value for item in ui.warning)


def test_samples_are_bounded_and_page_buttons_use_real_offsets():
    ui = author_ui(count=20)
    assert ui.session_state['sample-read'] == ('a'*32,8,0)
    button(ui, '下一页样本').click().run()
    assert not ui.exception
    assert ui.session_state['sample-read'] == ('a'*32,8,8)
    button(ui, '下一页样本').click().run()
    assert ui.session_state['sample-read'][-1] == 16
    assert button(ui, '下一页样本').disabled
    button(ui, '上一页样本').click().run()
    assert ui.session_state['sample-read'][-1] == 8


def test_export_is_explicit_and_reuses_ready_bytes_until_dataset_changes():
    ui = author_ui(count=1)
    assert 'export-calls' not in ui.session_state
    button(ui, '准备导出').click().run()
    assert not ui.exception and ui.session_state['export-calls'] == 1
    assert len(ui.get('download_button')) == 1
    ui.run()
    assert ui.session_state['export-calls'] == 1
    ui.session_state['datasets'] = [{**ui.session_state['datasets'][0], 'count':2}]
    ui.run()
    assert len(ui.get('download_button')) == 0
    assert not button(ui, '准备导出').disabled


def test_library_and_export_errors_are_safe_and_do_not_clear_editor():
    ui = author_ui(count=1)
    ui.text_area[0].set_value('Retain draft').run()
    ui.session_state['export-fails'] = True
    button(ui, '准备导出').click().run()
    assert not ui.exception and ui.text_area[0].value == 'Retain draft'
    assert 'SECRET' not in ' '.join(item.value for item in ui.error)
    ui.session_state['read-fails'] = True
    ui.run()
    assert not ui.exception and 'SECRET' not in ui.error[0].value


def test_upload_front_end_limits_and_safe_error_mapping():
    class Uploaded:
        def __init__(self, name, data):
            self.name, self.data = name, data
        def getvalue(self):
            return self.data
    assert _attachment_payload([Uploaded('cube.PNG', b'image')]) == [('cube.PNG',b'image')]
    assert _attachment_payload([]) == []
    for files in ([Uploaded('run.exe',b'image')], [Uploaded('cube.png',b'')],
                  [Uploaded('cube.png',b'x')]*9,
                  [Uploaded('cube.png',b'x'*(20*1024*1024+1))]):
        with pytest.raises(ValueError):
            _attachment_payload(files)
    assert _error(OSError('C:/secret/private.json'), 'safe fallback') == 'safe fallback'


def test_image_save_and_ui_export_use_real_persistent_application(tmp_path):
    script = '''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from lib.bootstrap.manual_datasets import manual_dataset_application
from lib.presentation.streamlit.manual_dataset_page import render_manual_datasets
app = manual_dataset_application(Path(st.session_state['output']))
if not app.list_datasets():
    app.create_dataset('Image Q&A')
dataset = app.list_datasets()[0]['id']
class Uploaded:
    name = 'blue-cube.png'
    def getvalue(self):
        return st.session_state['image-bytes']
revision = st.session_state.get('manual-datasets:cache:revision:'+dataset, 0)
with patch('lib.presentation.streamlit.manual_dataset_page.st.file_uploader',
           return_value=[] if revision else [Uploaded()]):
    render_manual_datasets(app, 'cache', show_title=False)
'''
    buffer = BytesIO()
    Image.new('RGB', (20, 16), 'blue').save(buffer, format='PNG')
    payload = buffer.getvalue()
    ui = AppTest.from_string(script)
    ui.session_state['output'] = str(tmp_path)
    ui.session_state['image-bytes'] = payload
    ui.run()
    assert not ui.exception
    ui.text_area[0].set_value('What color is the cube?').run()
    ui.text_area[1].set_value('Blue.').run()
    button(ui, '保存并继续下一条').click().run()
    assert not ui.exception
    button(ui, '准备导出').click().run()
    assert not ui.exception and len(ui.get('download_button')) == 1
    from lib.bootstrap.manual_datasets import manual_dataset_application
    reopened = manual_dataset_application(tmp_path)
    dataset = reopened.list_datasets()[0]['id']
    saved = reopened.list_samples(dataset)[0]
    assert saved['question'] == 'What color is the cube?'
    assert saved['metadata']['review_status'] == 'unreviewed'
    data = ui.session_state[f'manual-datasets:cache:export:{dataset}']['data']
    with ZipFile(BytesIO(data)) as archive:
        assert archive.read(saved['media'][0]['path']) == payload
        assert json.loads(archive.read('samples.jsonl')) == saved


def test_image_preview_decode_limit_does_not_crash_or_expose_error_detail():
    script = '''
from unittest.mock import patch
from PIL import Image
from lib.presentation.streamlit.manual_dataset_page import _image_grid
with patch('lib.presentation.streamlit.manual_dataset_page.st.image',
           side_effect=Image.DecompressionBombError('SECRET image details')):
    _image_grid([('large.png', b'uploaded-image')])
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert any('图片无法预览' in item.value for item in ui.warning)
    assert 'SECRET' not in ' '.join(item.value for item in ui.warning)
