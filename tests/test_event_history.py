"""Command-event inventory remains bounded while preserving global counts."""
import json
from pathlib import Path
import tracemalloc
from lib.application.monitor_service import MonitorApplication
from lib.infrastructure.event_history import FilesystemEventHistory


def test_large_history_filters_before_bounding_and_keeps_global_counts(tmp_path):
    path=tmp_path/'runs.jsonl'
    with path.open('w',encoding='utf-8') as handle:
        for i in range(50000):
            handle.write(json.dumps({'kind':'rare' if i%500==0 else 'frequent','at':str(i),'payload':'x'*1000})+'\n')
        handle.write('\n[]\nmalformed\n')
    tracemalloc.start()
    result=MonitorApplication(FilesystemEventHistory(path)).snapshot('rare')
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result['count']==50000 and result['invalid']==2
    assert result['kinds']==['frequent','rare'] and result['latest_at']=='49999'
    assert len(result['events'])==100
    assert [row['at'] for row in result['events']]==[str(i) for i in range(49500,-1,-500)]
    assert peak<3*1024*1024


def test_missing_file_is_empty_and_non_json_objects_are_invalid(tmp_path):
    path=tmp_path/'runs.jsonl'
    app=MonitorApplication(FilesystemEventHistory(path))
    assert app.snapshot()['count']==0
    path.write_text('\n{}\nnull\n[1]\n',encoding='utf-8')
    result=app.snapshot()
    assert result['count']==1 and result['invalid']==2
    assert result['kinds']==[''] and result['events']==[{}]


def test_application_bounds_port_request():
    class Port:
        def snapshot(self,kind,limit): return kind,limit
    app=MonitorApplication(Port())
    assert app.snapshot('specific',10000)==('specific',100)
    assert app.snapshot(None,0)==(None,1)
