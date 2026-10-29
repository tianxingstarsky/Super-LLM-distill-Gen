"""Copy this package's self-contained skill without changing a host configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

PACKAGE = Path(__file__).resolve().parent
SKILL_NAME = "shujian-cube"


def inventory(folder: Path) -> dict[str, str]:
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Skill directories must not contain symbolic links")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            result[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def install(destination: Path, *, replace: bool = False) -> dict:
    source = PACKAGE / "skills" / SKILL_NAME
    expected = inventory(source)
    if "SKILL.md" not in expected:
        raise ValueError("The package has no skill entrypoint")
    parent = destination.expanduser().resolve()
    if parent.is_relative_to(source.resolve()):
        raise ValueError("The destination must be outside the packaged skill")
    target = parent / SKILL_NAME
    if target.is_symlink():
        raise ValueError("The destination skill is a symbolic link; choose another destination")
    if target.exists():
        if not target.is_dir():
            raise ValueError("The destination skill path is not a directory")
        if inventory(target) == expected:
            return {"status": "unchanged", "skill_dir": str(target)}
        if not replace:
            raise ValueError("A different skill already exists; leave it intact or use --replace to back it up")
    parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".shujian-install-", dir=parent) as temporary:
        staged = Path(temporary) / SKILL_NAME
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if inventory(staged) != expected:
            raise ValueError("The copied skill does not match its source")
        if target.exists():
            backup = parent / ".shujian-cube-backups" / uuid.uuid4().hex
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup is not None and not target.exists():
                backup.rename(target)
            raise
    result = {"status": "replaced" if backup else "installed", "skill_dir": str(target)}
    if backup:
        result["backup_dir"] = str(backup)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path.home() / ".agents" / "skills",
                        help="Parent directory containing installed skills")
    parser.add_argument("--replace", action="store_true", help="Back up a different existing skill before replacement")
    args = parser.parse_args()
    try:
        result = install(args.destination, replace=args.replace)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Skill installation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
