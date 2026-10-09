"""The workflow CLI must forward the multi-turn generation setting."""
from __future__ import annotations

import json


def test_workflow_cli_forwards_conversation_turns(monkeypatch, capsys):
    from lib import cli
    from lib.bootstrap import workflows

    captured = {}

    class Application:
        def create_run(self, **recipe):
            captured.update(recipe)
            return "run-id"

        def execute(self, run_id):
            assert run_id == "run-id"
            return {"status": "completed"}

    monkeypatch.setattr(workflows, "workflow_application", lambda *_: Application())
    args = cli.build_parser().parse_args([
        "workflow", "--brief", "连续追问设备故障", "--targets", "multiturn", "--conversation-turns", "5",
    ])

    assert args.func(args) == 0
    assert captured["targets"] == ["multiturn"]
    assert captured["conversation_turns"] == 5
    assert "运行 ID: run-id" in capsys.readouterr().out


def test_workflow_cli_forwards_generation_and_trim_files(monkeypatch, tmp_path):
    from lib import cli
    from lib.bootstrap import workflows

    captured = {}

    class Application:
        def create_run(self, **recipe):
            captured.update(recipe)
            return 'run-id'

        def execute(self, run_id):
            return {'status': 'completed'}

    monkeypatch.setattr(workflows, 'workflow_application', lambda *_: Application())
    generation = {'cot': {'enabled': True, 'style': 'structured', 'instruction': '保留必要依据。'}}
    trim = {'enabled': True, 'template': 'leakage'}
    generation_file = tmp_path / 'generation.json'
    trim_file = tmp_path / 'trim.json'
    generation_file.write_text(json.dumps(generation, ensure_ascii=False), encoding='utf-8')
    trim_file.write_text(json.dumps(trim), encoding='utf-8')
    args = cli.build_parser().parse_args([
        'workflow', '--brief', 'Generate sourced examples', '--targets', 'cot',
        '--node-generation', str(generation_file), '--reasoning-trim', str(trim_file),
    ])
    assert args.func(args) == 0
    assert captured['node_generation'] == generation
    assert captured['reasoning_trim'] == trim
