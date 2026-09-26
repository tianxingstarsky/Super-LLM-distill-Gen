import subprocess
from types import SimpleNamespace

import pytest

from lib.infrastructure.agent_docker_replay import check_environment

IMAGE = "docker.io/library/python@sha256:" + "a" * 64


@pytest.mark.parametrize("daemon,image,reason", [
    ((1, b""), (0, b"linux/amd64"), "daemon_unavailable"),
    ((0, b"windows"), (0, b"linux/amd64"), "linux_required"),
    ((0, b"linux"), (1, b""), "image_missing"),
    ((0, b"linux"), (0, b"linux/arm64"), "image_platform_mismatch"),
    ((0, b"linux"), (0, b"linux/amd64"), "ready"),
])
def test_inspects_only_local_service_and_pinned_image(monkeypatch, daemon, image, reason):
    commands = []
    def run(command, **options):
        commands.append(command)
        assert options["timeout"] == 5 and options["capture_output"]
        code, output = daemon if len(commands) == 1 else image
        return SimpleNamespace(returncode=code, stdout=output)
    monkeypatch.setattr(subprocess, "run", run)
    assert check_environment(IMAGE) == {"ready": reason == "ready", "reason": reason}
    assert commands[0][:2] == ["docker", "info"]
    if len(commands) == 2:
        assert commands[1][:4] == ["docker", "image", "inspect", IMAGE]


@pytest.mark.parametrize("error,reason", [(FileNotFoundError(), "docker_missing"),
        (subprocess.TimeoutExpired("docker", 5), "check_unavailable")])
def test_missing_cli_and_timeout_have_safe_reasons(monkeypatch, error, reason):
    def run(*args, **kwargs):
        raise error
    monkeypatch.setattr(subprocess, "run", run)
    assert check_environment(IMAGE)["reason"] == reason


def test_invalid_configuration_never_invokes_docker(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Unexpected Docker call"))
    assert check_environment(None)["reason"] == "image_not_configured"
    assert check_environment("untrusted/image:latest")["reason"] == "invalid_image"
