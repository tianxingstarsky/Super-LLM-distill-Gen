"""Open existing folders; retain legacy workspaces without moving their data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from filelock import FileLock
from lib.io_utils import atomic_json

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = "default"
SEEDS_DIR = ROOT / "data/seeds"
REGISTRY_PATH = ROOT / "data/workspaces.json"
WORKSPACES_DIR = ROOT / "data/workspaces"
CURRENT_PATH = WORKSPACES_DIR / "current.json"
OUTPUT_SUBDIR = ".dataforge/output"
_ID = re.compile(r"[A-Za-z0-9_-]{1,48}")
_EXCLUDE = {".dataforge", ".git", ".venv", "node_modules", "__pycache__"}


def _registry_path(root=None):
    return REGISTRY_PATH if root is None else Path(root) / "data/workspaces.json"


def _read_registry(root=None):
    path = _registry_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("workspaces", {}), dict):
            raise ValueError
        for name, conf in data.get("workspaces", {}).items():
            validate(name)
            if (name == DEFAULT or not isinstance(conf, dict)
                    or not isinstance(conf.get("folder"), str)
                    or not Path(conf["folder"]).is_absolute()
                    or ("label" in conf and not isinstance(conf["label"], str))):
                raise ValueError
        if data.get("current") is not None:
            validate(data["current"])
    except (ValueError, TypeError) as error:
        raise ValueError(f"文件夹索引格式错误，原文件未改动：{path}") from error
    return data


def _canonical(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def is_linked(path):
    """符号链接 / junction / 其他重解析点 → True（目录与文件通用）。

    Python 3.11 没有 Path.is_junction，且 os.path.islink 对 Windows junction 返回 False，
    因此直接看 lstat：符号链接位 + st_reparse_tag（Windows 上 3.8+ 提供；非 Windows 缺省 0）。
    无法读取/已消失的路径按“已链接”处理：扫描器失败关闭，宁可漏列也不越界或成环。
    """
    try:
        info = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) != 0


def _legacy(root=None):
    directory = WORKSPACES_DIR if root is None else Path(root) / "data/workspaces"
    if not directory.is_dir():
        return {}
    return {p.name: p for p in directory.iterdir() if p.is_dir() and _ID.fullmatch(p.name)}


def validate(name):
    if not isinstance(name, str) or not _ID.fullmatch(name):
        raise ValueError("无效文件夹标识；请使用打开后的标识或 workspace list 查询")
    return name


def list_all(root=None):
    registered = _read_registry(root).get("workspaces", {})
    return [DEFAULT, *sorted((set(registered) | set(_legacy(root))) - {DEFAULT})]


def current(root=None):
    data = _read_registry(root)
    value = data.get("current")
    legacy_current = CURRENT_PATH if root is None else Path(root) / "data/workspaces/current.json"
    if not value and legacy_current.exists():
        try:
            legacy = json.loads(legacy_current.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            legacy = None
        value = legacy.get("workspace") if isinstance(legacy, dict) else None
    return value if value in list_all(root) else DEFAULT


def resolve(ws=None, root=None):
    name = validate(ws if ws is not None else (os.environ.get("DF_WORKSPACE") or current(root)))
    if name not in list_all(root):
        raise ValueError(f"尚未打开此文件夹：{name}。使用 workspace add <已有目录>，不会创建源文件夹。")
    return name


def add_folder(folder, name=None, root=None):
    if not isinstance(folder, (str, os.PathLike)) or not str(folder).strip():
        raise ValueError("请提供已有文件夹的完整路径")
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"文件夹不存在：{path}。请打开已经准备好的文件夹。")
    identity = _canonical(path)
    if name == DEFAULT:
        raise ValueError("default 为历史数据保留标识")
    if identity == _canonical(SEEDS_DIR if root is None else Path(root) / "data/seeds"):
        return DEFAULT
    for identifier, legacy in _legacy(root).items():
        if identity == _canonical(legacy):
            return identifier
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", path.name).strip("-")[:22] or "folder"
    picked = validate(name) if name is not None else f"{slug}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"
    registry = _registry_path(root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(registry) + ".lock", timeout=10):
        data = _read_registry(root)
        spaces = data.setdefault("workspaces", {})
        for identifier, conf in spaces.items():
            if _canonical(conf["folder"]) == identity:
                return identifier
        if picked in spaces or picked in _legacy(root):
            raise ValueError(f"标识已被其他目录占用：{picked}")
        spaces[picked] = {"folder": str(path), "label": path.name}
        atomic_json(registry, data)
    return picked


def set_current(name, root=None):
    resolve(name, root)
    folder(name, root)  # A moved or disconnected source must never be recreated.
    registry = _registry_path(root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(registry) + ".lock", timeout=10):
        data = _read_registry(root)
        data["current"] = name
        atomic_json(registry, data)
    return name


def folder(ws=None, root=None):
    name = resolve(ws, root)
    if name == DEFAULT:
        return SEEDS_DIR if root is None else Path(root) / "data/seeds"
    record = _read_registry(root).get("workspaces", {}).get(name)
    path = Path(record["folder"]) if record else _legacy(root)[name]
    if not path.is_dir():
        raise FileNotFoundError(f"已打开的文件夹不可用：{path}。请恢复原目录或重新打开，不会自动创建。")
    return path


def output_at(root, ws=None):
    name = resolve(ws, root)
    if name == DEFAULT:
        return Path(root or ROOT) / "data/output"
    if name in _read_registry(root).get("workspaces", {}):
        return folder(name, root) / OUTPUT_SUBDIR
    return folder(name, root) / "output"


def out(ws=None, root=None):
    return output_at(root, ws)


out_dir = out


def dataset_name(ws=None, root=None):
    name = resolve(ws, root)
    return "rollout_review" if name == DEFAULT else "rollout_review_" + name


def label(ws, root=None):
    if ws == DEFAULT:
        return "历史数据（default）"
    conf = _read_registry(root).get("workspaces", {}).get(ws)
    return conf.get("label", Path(conf["folder"]).name) if conf else f"历史工作区 · {ws}"


def _wanted_suffixes(suffixes):
    """suffixes=None → 不筛选；否则归一化为 {'.jsonl', ...}（可带点或不带点）。"""
    if suffixes is None:
        return None
    if isinstance(suffixes, str):
        raise ValueError("suffixes 需要可迭代的扩展名集合，例如 ('.jsonl',)")
    wanted = set()
    for suffix in suffixes:
        if not isinstance(suffix, str) or not suffix.strip():
            raise ValueError("后缀必须是非空字符串，例如 '.jsonl'")
        suffix = suffix.strip().lower()
        wanted.add(suffix if suffix.startswith(".") else "." + suffix)
    if not wanted:
        raise ValueError("后缀筛选不能为空；不做筛选请传 None")
    return wanted


def source_files(ws=None, limit=500, root=None, suffixes=None):
    """Bounded folder inventory; outputs, dependencies and linked trees are not inputs.

    ``suffixes``（可选，扩展名集合）：先按后缀筛选再计入 ``limit``，避免 500 个非目标
    文件把真正的数据集挤出窗口；目录/文件两侧都排除符号链接与 junction（``is_linked``）。
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("源文件统计上限必须是正整数")
    wanted = _wanted_suffixes(suffixes)
    name = resolve(ws, root)
    base = folder(name, root)
    if not base.is_dir():
        return []
    destination = out(name, root)
    result = []
    for directory, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUDE
                         and not is_linked(Path(directory) / d)
                         and (Path(directory) / d) != destination)
        for filename in sorted(files):
            path = Path(directory) / filename
            if filename == ".gitkeep" or is_linked(path):
                continue
            if wanted is not None and path.suffix.lower() not in wanted:
                continue
            result.append(path)
            if len(result) >= limit:
                return result
    return result


def status(ws=None, root=None):
    name = resolve(ws, root)
    source = folder(name, root)
    destination = out(name, root)
    state = destination / "gates_state.json"
    gates, warning = {}, ""
    if state.exists():
        try:
            loaded = json.loads(state.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                gates = loaded
            else:
                warning = f"闸门状态不是 JSON 对象，已按未通过处理：{state}"
        except (OSError, ValueError) as error:
            warning = f"闸门状态无法解析，已按未通过处理：{state}（{error}）"
    info = {"workspace": name, "label": label(name, root), "folder": str(source), "out_dir": str(destination),
            "dataset": dataset_name(name, root), "gates": gates}
    if warning:
        info["warning"] = warning
    return info
