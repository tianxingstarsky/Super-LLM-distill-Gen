"""Behavior checks for independent installation and protocol transport only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


installer = module("shujian_skill_installer", PACKAGE / "install.py")
control = module("shujian_skill_control", PACKAGE / "skills/shujian-cube/scripts/control.py")


class InstallationTests(unittest.TestCase):
    def test_destination_cannot_recurse_into_the_packaged_skill(self):
        with self.assertRaisesRegex(ValueError, "outside the packaged skill"):
            installer.install(PACKAGE / "skills/shujian-cube/nested-skills")

    def test_install_is_complete_and_repeat_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "带空格 skills"
            first = installer.install(parent)
            installed = Path(first["skill_dir"])
            self.assertEqual(first["status"], "installed")
            self.assertTrue((installed / "scripts/control.py").is_file())
            self.assertTrue((installed / "references/control.md").is_file())
            before = (installed / "SKILL.md").stat().st_mtime_ns
            self.assertEqual(installer.install(parent)["status"], "unchanged")
            self.assertEqual((installed / "SKILL.md").stat().st_mtime_ns, before)

    def test_conflict_is_preserved_until_explicit_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            installed = Path(installer.install(parent)["skill_dir"])
            custom = installed / "personal-rule.txt"
            custom.write_text("Keep my own rule", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different skill"):
                installer.install(parent)
            self.assertEqual(custom.read_text(encoding="utf-8"), "Keep my own rule")
            replaced = installer.install(parent, replace=True)
            self.assertEqual(replaced["status"], "replaced")
            self.assertEqual((Path(replaced["backup_dir"]) / custom.name).read_text(encoding="utf-8"),
                             "Keep my own rule")
            self.assertFalse(custom.exists())


class ProtocolTests(unittest.TestCase):
    def fixture(self, root: Path, *, wrong_identity=False, refusal=False):
        package = root / "lib"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        # A separate controlled subprocess exercises real UTF-8 argv/stdin/stdout.
        identity = "request['request_id']='another-request'\n" if wrong_identity else ""
        (package / "control.py").write_text(
            "import json,sys\n"
            "request=json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
            + identity +
            f"request['ok']={not refusal!r}\n"
            "request['data']={'echo':request['params']}\n"
            "sys.stdout.buffer.write(json.dumps(request,ensure_ascii=False).encode('utf-8'))\n"
            f"raise SystemExit({2 if refusal else 0})\n", encoding="utf-8")

    def test_utf8_identity_and_params_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "安装 空间"
            root.mkdir()
            self.fixture(root)
            params = {"run_id": "a" * 32, "note": "一行\nwith quotes \" and \\ paths"}
            reply, code = control.call(root, "runs.inspect", params, python=Path(sys.executable), request_id="read-1")
            self.assertEqual(code, 0)
            self.assertEqual(reply["data"]["echo"], params)

    def test_mismatched_identity_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root, wrong_identity=True)
            with self.assertRaisesRegex(ValueError, "identity_mismatch"):
                control.call(root, "capabilities", {}, python=Path(sys.executable))

    def test_refusal_keeps_nonzero_exit_and_create_requires_stable_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root, refusal=True)
            reply, code = control.call(root, "runs.cancel", {"run_id": "a" * 32}, python=Path(sys.executable))
            self.assertFalse(reply["ok"])
            self.assertEqual(code, 2)
            with self.assertRaisesRegex(ValueError, "stable_request_id"):
                control.call(root, "runs.create", {"recipe": {}}, python=Path(sys.executable))


if __name__ == "__main__":
    unittest.main()
