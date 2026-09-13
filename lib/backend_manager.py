"""模型后端与密钥管理：列出（掩码）、新增/编辑（本地覆盖+备份）、连接测试。

密钥纪律：
- 配置文件只写 gitignored 的 configs/backends.local.yaml（与既有约定一致）；
- 列表接口从不返回完整密钥：env 来源只显示变量名与存在性；配置来源只显示掩码尾部；
- 新密钥默认建议走环境变量（api_key_env），界面提供两种来源选择。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import time
from typing import Any, Dict, List

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(base: str = "backends.yaml") -> dict:
    cfg: Dict[str, Any] = {}
    p = ROOT / "configs" / base
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def _key_display(backend: Dict[str, Any]) -> Dict[str, str]:
    env_name = str(backend.get("api_key_env") or "")
    env_val = os.environ.get(env_name, "") if env_name else ""
    cfg_val = backend.get("api_key") or ""
    if env_val:
        return {"source": f"env:{env_name}", "status": "存在"}
    if cfg_val:
        return {"source": "configs/backends.local.yaml", "status": mask_key(str(cfg_val))}
    # 本地端点允许无 key（Ollama/vLLM 等）；云端无 key 则明确标出
    return {"source": "未配置", "status": "缺失"}


def list_backends() -> Dict[str, Any]:
    """合并 base+local：名称、url、models、密钥来源（掩码）、被哪些角色使用、是否默认。"""
    base = _read()
    local = _read("backends.local.yaml")
    merged_backends = dict(base.get("backends", {}))
    for name, value in (local.get("backends") or {}).items():
        merged_backends[name] = {**merged_backends.get(name, {}), **value}
    roles: Dict[str, dict] = {}
    merged_roles = {**(base.get("model_roles") or {}), **(local.get("model_roles") or {})}
    for role, slot in merged_roles.items():
        roles[role] = slot
    rows = []
    for name, backend in merged_backends.items():
        users = sorted(r for r, s in roles.items() if (s.get("backend") or "") == name)
        rows.append({
            "name": name,
            "base_url": backend.get("base_url", ""),
            "models": backend.get("models") or [],
            "api_key": _key_display(backend),
            "roles": users,
            "is_default": name == (local.get("default_backend") or base.get("default_backend")),
            "prices": backend.get("prices") or {},
        })
    return {
        "backends": rows,
        "default_backend": local.get("default_backend") or base.get("default_backend", "deepseek"),
        "default_model": local.get("default_model") or base.get("default_model", ""),
        "roles": roles,
        "budget": local.get("budget") or base.get("budget") or {},
        "spent": _spent(),
    }


def _spent() -> float:
    p = ROOT / "data" / "output" / "budget.json"
    if p.exists():
        try:
            return float(json.loads(p.read_text(encoding="utf-8")).get("spent_usd", 0.0))
        except (json.JSONDecodeError, OSError):
            pass
    return 0.0


def reset_budget(caller: str) -> float:
    """清零预算（人工确认后的操作；保留审计痕迹）。"""
    from lib.io_utils import atomic_json
    from lib.monitor import trace_run
    p = ROOT / "data" / "output" / "budget.json"
    spent = _spent()
    atomic_json(p, {"spent_usd": 0.0, "limit_usd": _limit(), "reset_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "reset_by": caller})
    trace_run(ROOT, "budget_reset", {"from_usd": spent, "by": caller})
    return spent


def _limit() -> float:
    return float((list_backends().get("budget") or {}).get("max_total_usd", 0.0))


def test_backend(name: str, overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """连接测试：models.list（不产生 token 费用）。失败抛错带信息。"""
    from openai import OpenAI
    backend = dict(_build_merged().get(name) or {})
    if overrides:
        backend = {**backend, **overrides}
    if not backend.get("base_url"):
        raise ValueError("缺少 base_url")
    env_name = backend.get("api_key_env") or ""
    api_key = backend.get("api_key") or os.environ.get(env_name, "") or os.environ.get("OPENAI_API_KEY", "") or "sk-local"
    client = OpenAI(base_url=backend["base_url"], api_key=api_key)
    data = client.models.list().data
    return {"backend": name, "base_url": backend["base_url"], "models": sorted(m.id for m in data)}


def _build_merged() -> Dict[str, dict]:
    base = _read()
    local = _read("backends.local.yaml")
    merged = dict(base.get("backends", {}))
    for name, value in (local.get("backends") or {}).items():
        merged[name] = {**merged.get(name, {}), **value}
    return merged


_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def validate_endpoint(name: str, base_url: str, models: List[str]) -> None:
    if not _VALID_NAME.match(name or ""):
        raise ValueError("后端名只允许字母/数字/下划线/连字符（1-32）")
    if not re.match(r"^https?://", base_url or ""):
        raise ValueError("base_url 必须是 http(s):// 地址")
    if not models:
        raise ValueError("至少填一个模型名（models）")


def save_endpoint(name: str, base_url: str, models: List[str],
                  api_key: str = "", api_key_env: str = "", prices: Dict[str, float] | None = None,
                  explicit_replace: bool = False) -> str:
    """写入 gitignored 的 backends.local.yaml（覆盖/新增后端；先备份）。

    api_key 与 api_key_env 二选一：env 源优先；两者都空=本地无 key 端点。"""
    validate_endpoint(name, base_url, models)
    if api_key and api_key_env:
        raise ValueError("api_key 与 api_key_env 二选一；推荐环境变量方式")
    local_cfg = _read("backends.local.yaml")
    backends = dict(local_cfg.get("backends") or {})
    if name in backends and not explicit_replace:
        raise FileExistsError(f"后端 {name} 已存在（explicit_replace=True 才覆盖；或换名字）")
    entry: Dict[str, Any] = {"base_url": base_url, "models": list(models)}
    if api_key:
        entry["api_key"] = api_key
    elif api_key_env:
        entry["api_key_env"] = api_key_env
    else:
        entry["api_key_env"] = ""
    if prices:
        entry["prices"] = prices
    backends[name] = entry
    local_cfg["backends"] = backends
    _write_local(local_cfg)
    return name


def set_role(role: str, backend: str, model: str) -> None:
    """把角色槽位切到指定后端+模型（写本地覆盖）。"""
    valid = _VALID_NAME
    if not valid.match(role or "") or role not in ("generation", "judge", "vision", "refine", "simulate", "translation"):
        raise ValueError(f"角色不合法：{role!r}（可用 generation/judge/vision/refine/simulate/translation）")
    if not _build_merged().get(backend):
        raise ValueError(f"后端不存在：{backend}")
    local_cfg = _read("backends.local.yaml")
    roles = dict(local_cfg.get("model_roles") or {})
    roles[role] = {"backend": backend, "model": model}
    local_cfg["model_roles"] = roles
    _write_local(local_cfg)


def _write_local(cfg: Dict[str, Any]) -> None:
    """YAML 原子写（先备份）。文件扩展名是 .yaml：用 yaml 序列化而不是 JSON，
    避免 JSON 语法破坏该文件的 YAML 惯例/注释（旧实现 atomic_json 写 JSON）。"""
    import tempfile

    target = ROOT / "configs" / "backends.local.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(f"{target.name}.{time.strftime('%Y%m%dT%H%M%S')}.bak")
        shutil.copy2(target, backup)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".pending-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(cfg, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
