"""Atomic state writes survive short Windows holds and preserve old state on failure."""
import json
import pytest
from lib import io_utils


def windows_hold(code):
    error=PermissionError('temporary Windows hold')
    error.winerror=code
    return error


@pytest.mark.parametrize('code',[5,32,33])
def test_temporary_windows_hold_retries_complete_state(tmp_path,monkeypatch,code):
    path=tmp_path/'state.json'
    path.write_text('{"status":"old"}',encoding='utf-8')
    replace=io_utils.os.replace
    attempts=[]
    sleeps=[]
    def held(source,destination):
        attempts.append(source)
        if len(attempts)<3:
            assert json.loads(path.read_text())=={'status':'old'}
            raise windows_hold(code)
        replace(source,destination)
    monkeypatch.setattr(io_utils.os,'replace',held)
    monkeypatch.setattr(io_utils.time,'sleep',sleeps.append)
    io_utils.atomic_json(path,{'status':'completed','rows':50000})
    assert json.loads(path.read_text())=={'status':'completed','rows':50000}
    assert len(set(attempts))==1 and sleeps==[0.02,0.04]
    assert list(tmp_path.glob('.pending-*'))==[]


def test_persistent_windows_hold_is_bounded_and_preserves_old_state(tmp_path,monkeypatch):
    path=tmp_path/'state.json'
    path.write_text('{"status":"old"}',encoding='utf-8')
    attempts=[]
    sleeps=[]
    error=windows_hold(5)
    def held(*args):
        attempts.append(args)
        raise error
    monkeypatch.setattr(io_utils.os,'replace',held)
    monkeypatch.setattr(io_utils.time,'sleep',sleeps.append)
    with pytest.raises(PermissionError) as caught:
        io_utils.atomic_json(path,{'status':'new'})
    assert caught.value is error
    assert len(attempts)==6 and sum(sleeps)==pytest.approx(0.62)
    assert json.loads(path.read_text())=={'status':'old'}
    assert list(tmp_path.glob('.pending-*'))==[]


def test_unrelated_write_errors_are_not_retried(tmp_path,monkeypatch):
    attempts=[]
    def fail(*args):
        attempts.append(args)
        raise OSError('disk failure')
    monkeypatch.setattr(io_utils.os,'replace',fail)
    monkeypatch.setattr(io_utils.time,'sleep',lambda _:pytest.fail('unrelated error was retried'))
    with pytest.raises(OSError,match='disk failure'):
        io_utils.atomic_json(tmp_path/'state.json',{})
    assert len(attempts)==1
