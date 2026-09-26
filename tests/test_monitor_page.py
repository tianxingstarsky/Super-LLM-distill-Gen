"""Independent monitor UI preserves filtering and survives history replacement."""
import ast
import json
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]


def test_monitor_page_has_no_storage_or_composition_imports():
    tree=ast.parse((ROOT/'lib/presentation/streamlit/monitor_page.py').read_text(encoding='utf-8'))
    imports=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(module.startswith(('lib.bootstrap','lib.infrastructure','lib.workspace')) for module in imports)


def test_monitor_filters_and_reconciles_shortened_history(tmp_path):
    path=tmp_path/'runs.jsonl'
    rows=[{'kind':'rare' if i%2==0 else 'normal','at':str(i),'status':'raw-status'} for i in range(10)]
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
    script=f'''
from lib.bootstrap.monitor import monitor_application
from lib.presentation.streamlit.monitor_page import render_monitor_page
import streamlit as st
render_monitor_page(monitor_application({str(path)!r}),"fixture",lambda:st.caption("job-status-rendered"))
'''
    ui=AppTest.from_string(script,default_timeout=15).run()
    assert not ui.exception and len(ui.radio[0].options)==10
    ui.selectbox[0].select('rare').run()
    assert not ui.exception and len(ui.radio[0].options)==5
    ui.radio[0].set_value(4).run()
    path.write_text(json.dumps(rows[0])+'\n',encoding='utf-8')
    ui.run()
    assert not ui.exception and len(ui.radio[0].options)==1 and ui.radio[0].value==0
    assert any(item.value=='job-status-rendered' for item in ui.caption)
    path.write_text(json.dumps({'kind':'replacement','at':'now'})+'\n',encoding='utf-8')
    ui.run()
    assert not ui.exception and ui.selectbox[0].value is None
