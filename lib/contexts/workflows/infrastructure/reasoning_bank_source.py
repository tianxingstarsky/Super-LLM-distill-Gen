"""Load unchanged entrypoints from the pinned, complete Google source checkout.

The research scripts initialize cloud clients during import. This boundary loads
their original function definitions without executing those import-time clients;
callers provide real model/storage adapters. No algorithm body is rewritten.
"""
import ast
from hashlib import sha256
from pathlib import Path
from types import ModuleType

from ..domain.reasoning_bank import ReasoningBankUnavailable

UPSTREAM_COMMIT = "ed80611788292ea739f1effd31f16c53823b8a0d"
UPSTREAM_URL = "https://github.com/google-research/reasoning-bank"
_SOURCE_HASHES = {
    "WebArena/memory_management.py": "35b8b800180024f3446c4a295fe9d7c19d4aa2cddd0b2f2a44b6680e4d6bc4f9",
    "WebArena/induce_memory.py": "97d7da3fe5bd3e37d05e4aa07c050b1154334951cc2cad20619774ae77e0912c",
    "WebArena/induce_scaling.py": "bb7399af96a674fc36de61665b91d87748aeef7f972df8757ce42869d12f30be",
    "WebArena/prompts/memory_instruction.py": "073d354bc84a24ba8a2c697133b5d832fe89ca67a53705fb0af419528f7b11ee",
}


def _read_source(relative_path, upstream_root):
    if relative_path not in _SOURCE_HASHES:
        raise ValueError("reasoning_bank_unknown_upstream_source")
    root = (Path(__file__).resolve().parents[4] / "components" / "reasoning-bank"
            if upstream_root is None else Path(upstream_root))
    try:
        source = (root / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReasoningBankUnavailable("reasoning_bank_upstream_source_unavailable") from error
    checksum = sha256(source.encode("utf-8")).hexdigest()
    if checksum != _SOURCE_HASHES[relative_path]:
        raise ValueError("reasoning_bank_upstream_source_changed")
    receipt = {"repository": UPSTREAM_URL, "commit": UPSTREAM_COMMIT, "file": relative_path,
               "source_sha256_lf": checksum, "license": "Apache-2.0"}
    return source, ast.parse(source), receipt, root / relative_path


def load_upstream_functions(relative_path, *, names=None, namespace=None, upstream_root=None):
    """Execute exact upstream function bodies in an isolated provider namespace."""
    source, tree, receipt, path = _read_source(relative_path, upstream_root)
    available = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    selected = list(available) if names is None else list(names)
    if not selected or set(selected) - available.keys():
        raise ValueError("reasoning_bank_unknown_upstream_function")
    module = ModuleType("shujian_reasoning_bank_" + Path(relative_path).stem)
    module.__dict__.update(namespace or {})
    module.__file__ = str(path)
    definitions = ast.Module(body=[available[name] for name in selected], type_ignores=[])
    exec(compile(definitions, str(path), "exec"), module.__dict__)
    receipt["functions"] = {name: sha256(ast.get_source_segment(source, available[name]).encode("utf-8")).hexdigest()
                            for name in selected}
    receipt["loading"] = "unchanged_function_definitions_without_cloud_import_initialization"
    return module, receipt


def load_upstream_constants(relative_path, names, *, upstream_root=None):
    """Read original prompt constants without importing a cloud execution stack."""
    source, tree, receipt, _path = _read_source(relative_path, upstream_root)
    assignments = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)}
    if set(names) - assignments.keys():
        raise ValueError("reasoning_bank_unknown_upstream_constant")
    constants = {name: ast.literal_eval(assignments[name]) for name in names}
    receipt["constants"] = {name: sha256(value.encode("utf-8")).hexdigest() for name, value in constants.items()}
    return constants, receipt
