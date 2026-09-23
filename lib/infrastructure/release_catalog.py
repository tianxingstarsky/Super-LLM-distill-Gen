"""Read local release directories without treating a manifest as proof by itself."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from lib import workspace as WS


_RUN_ID = re.compile(r"[a-f0-9]{32}")
_HASH = re.compile(r"[a-f0-9]{64}")
_REVIEW_VERSIONS = {
    "sft": "sft",
    "cpt": "cpt",
    "preference": "dpo",
    "orpo-preference": "orpo",
}
_VERSION = re.compile(r"(sft|cpt|preference|orpo-preference)-v(\d{4})")


def _release_dirs(output: Path):
    exports = output / "export"
    if exports.is_dir() and not WS.is_linked(exports):
        for folder in sorted(exports.iterdir()):
            if folder.is_dir() and not WS.is_linked(folder) and (folder / "manifest.json").exists():
                yield folder, "export", None

    workflows = output / "workflows"
    if workflows.is_dir() and not WS.is_linked(workflows):
        for run in sorted(workflows.iterdir()):
            if not _RUN_ID.fullmatch(run.name) or not run.is_dir() or WS.is_linked(run):
                continue
            releases = run / "releases"
            if not releases.is_dir() or WS.is_linked(releases):
                continue
            for folder in sorted(releases.iterdir()):
                if (_VERSION.fullmatch(folder.name) and folder.is_dir()
                        and not WS.is_linked(folder) and (folder / "manifest.json").exists()):
                    yield folder, "review", run.name


def _inspect(output: Path, folder: Path, kind: str, run_id: str | None) -> dict:
    manifest_path = folder / "manifest.json"
    result = {
        "id": folder.relative_to(output).as_posix(),
        "name": folder.name,
        "kind": kind,
        "path": str(folder.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "run_id": run_id,
        "verified": False,
        "files": [],
        "unverified_files": [],
    }
    try:
        if WS.is_linked(manifest_path) or not manifest_path.is_file():
            raise ValueError("清单文件不可用")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("清单格式错误")
        result.update(
            created_at=manifest.get("created_at"),
            target=manifest.get("target") if kind == "review" else manifest.get("format"),
            counts=manifest.get("counts", {}),
            status=manifest.get("status"),
        )
        hashes = manifest.get("sha256")
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("清单没有可校验文件")
        if kind == "export":
            if manifest.get("status") != "complete" or not any(
                    isinstance(name, str) and name.endswith(".jsonl") for name in hashes):
                raise ValueError("历史导出尚未完成")
        else:
            match = _VERSION.fullmatch(folder.name)
            if (manifest.get("status") != "human_reviewed" or match is None
                    or manifest.get("target") != _REVIEW_VERSIONS[match.group(1)]
                    or manifest.get("run_id") != run_id
                    or manifest.get("version") != int(match.group(2))):
                raise ValueError("人工审核版本清单不匹配")
        actual = set()
        for item in folder.iterdir():
            if WS.is_linked(item) or not item.is_file():
                raise ValueError("版本目录含不可校验的文件或子目录")
            actual.add(item.name)
        expected = set(hashes) | {"manifest.json"}
        if kind == "export" and "quality.json" in actual and "quality.json" not in hashes:
            expected.add("quality.json")  # Older exports did not hash this report.
            result["unverified_files"] = [{"name": "quality.json",
                                           "path": str((folder / "quality.json").resolve())}]
        if actual != expected:
            raise ValueError("版本目录与清单文件列表不一致")
        files = []
        for name, digest in sorted(hashes.items()):
            if (not isinstance(name, str) or not name or Path(name).name != name
                    or "/" in name or "\\" in name
                    or name == "manifest.json" or not isinstance(digest, str)
                    or not _HASH.fullmatch(digest)):
                raise ValueError("清单中的文件名或 SHA-256 无效")
            path = folder / name
            with path.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual_hash != digest:
                raise ValueError(f"文件校验失败：{name}")
            files.append({"name": name, "path": str(path.resolve()),
                          "bytes": path.stat().st_size, "sha256": digest})
        files.append({"name": "manifest.json", "path": str(manifest_path.resolve()),
                      "bytes": len(manifest_bytes), "sha256": None})
        result.update(verified=True, files=files,
                      manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        result["error"] = str(error)
    return result


def list_releases(output: Path) -> list[dict]:
    """List releases in this output workspace, including damaged entries for diagnosis."""
    output = Path(output)
    rows = [_inspect(output, folder, kind, run_id)
            for folder, kind, run_id in _release_dirs(output)]
    return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)


def release_file(output: Path, release_id: str, filename: str) -> bytes:
    """Recheck the selected release and payload immediately before downloading."""
    output = Path(output)
    for folder, kind, run_id in _release_dirs(output):
        if folder.relative_to(output).as_posix() != release_id:
            continue
        release = _inspect(output, folder, kind, run_id)
        if not release["verified"]:
            raise ValueError(release.get("error", "发布版本校验失败"))
        item = next((file for file in release["files"] if file["name"] == filename), None)
        if item is None:
            raise ValueError("此文件未列入可下载清单")
        data = (folder / filename).read_bytes()
        digest = item["sha256"] or release["manifest_sha256"]
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("文件在校验后发生变化，请刷新页面")
        return data
    raise ValueError("发布版本已移动或不属于当前工作区")
