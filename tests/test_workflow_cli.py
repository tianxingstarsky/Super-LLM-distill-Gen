"""The workflow CLI must forward the multi-turn generation setting."""
from __future__ import annotations


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
