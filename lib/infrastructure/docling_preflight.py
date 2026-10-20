"""Cheap local runtime checks for the UI; no model imports or weight hashing.

These checks establish availability, not integrity. Submitted runs still inspect
the runtime afresh and fingerprint model contents in the background worker.
"""
from __future__ import annotations

import ast
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import threading


_INSPECTIONS = OrderedDict()
_INSPECTION_LOCK = threading.RLock()
_MAX_INSPECTIONS = 8
_LAYOUT_FILES = ("config.json", "model.safetensors", "preprocessor_config.json")
_TABLE_FILES = ("tableformer_accurate.safetensors", "tm_config.json")
_FORMULA_FILES = (
    "decoder_model.onnx", "encoder_model.onnx", "config.json", "generation_config.json",
    "preprocessor_config.json", "special_tokens_map.json", "tokenizer_config.json", "tokenizer.json",
)
_FORMULA_PACKAGES = {"transformers": "4.57.6", "onnxruntime": "1.31.0",
                     "optimum": "2.1.0", "optimum-onnx": "0.1.0"}
_INLINE_LAYOUT_FILES = ("inference.onnx", "inference.yml")
_INLINE_FORMULA_PACKAGES = {"pylatexenc": "2.11"}


def _stat_record(path):
    path = Path(path)
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    except FileNotFoundError:
        return (str(path.absolute()), None)


def _runtime_fingerprint(python, info=None):
    """Include linked environments reported by Python without executing .pth."""
    python = Path(python)
    prefixes = {python.parent, python.parent.parent}
    roots = set()
    tracked = {python, python.resolve()}
    for prefix in prefixes:
        tracked.add(prefix / "pyvenv.cfg")
        roots.add(prefix / "Lib" / "site-packages")
        lib = prefix / "lib"
        if lib.is_dir():
            roots.update(lib.glob("python*/site-packages"))
    if isinstance(info, dict):
        for name in info.get("site_packages", []):
            if isinstance(name, str) and Path(name).is_absolute():
                roots.add(Path(name))
        metadata = info.get("metadata_files", {})
        if isinstance(metadata, dict):
            for name in metadata.values():
                if isinstance(name, str) and Path(name).is_absolute():
                    tracked.add(Path(name))
                    roots.add(Path(name).parent.parent)
    for root in roots:
        tracked.add(root)
        if root.is_dir():
            # Directory timestamps find added/removed distributions. Metadata
            # timestamps also find upgrades performed within an existing dir.
            tracked.update(root.glob("*.dist-info/METADATA"))
            tracked.update(root.glob("*.pth"))
            config = root / "easyocr" / "config.py"
            if config.is_file():
                tracked.add(config)
    return tuple(sorted((_stat_record(path) for path in tracked), key=lambda row: row[0]))


def inspect_runtime_cached(python, *, service=None):
    """Return a detached inspection, invalidated by interpreter/package changes."""
    from lib.infrastructure.docling_runtime import MODERN_PACKAGES, INLINE_LAYOUT_PACKAGES, RUNTIME_PROTOCOL
    from lib.infrastructure.docling_worker_client import run_docling_worker

    if service not in (None, "inline_layout"):
        raise ValueError("invalid_document_parser")
    python = Path(python)
    if not python.is_absolute() or not python.is_file():
        raise ValueError("document_docling_runtime_unavailable")
    key = (str(python.absolute()), service)
    with _INSPECTION_LOCK:
        cached = _INSPECTIONS.get(key)
        info = cached[1] if cached else None
        before = _runtime_fingerprint(python, info)
        if cached and cached[0] == before:
            _INSPECTIONS.move_to_end(key)
            result = deepcopy(info)
        else:
            request = {"action": "inspect"}
            if service:
                request["service"] = service
            result = run_docling_worker(python, request, timeout_seconds=30)
            if before != _runtime_fingerprint(python, info):
                raise ValueError("document_docling_configuration_changed")
            _INSPECTIONS[key] = (_runtime_fingerprint(python, result), deepcopy(result))
            while len(_INSPECTIONS) > _MAX_INSPECTIONS:
                _INSPECTIONS.popitem(last=False)
    versions = result.get("packages", {})
    required = INLINE_LAYOUT_PACKAGES if service == "inline_layout" else MODERN_PACKAGES
    if (result.get("protocol") != RUNTIME_PROTOCOL or not isinstance(versions, dict)
            or any(versions.get(name) != version for name, version in required.items())):
        raise ValueError("document_docling_inline_version_mismatch" if service else "document_docling_version_mismatch")
    return result


def _require_files(directory, names, error):
    try:
        if any(not (directory / name).is_file() or (directory / name).stat().st_size <= 0 for name in names):
            raise ValueError(error)
    except OSError as exc:
        raise ValueError(error) from exc


def _ocr_required_files(config_path, languages):
    """Read EasyOCR's own constants without importing its Torch-based package.

Selection order follows EasyOCR.Reader's default standard recognizer. Unknown
or mixed script families are rejected before creating a local model worker.
"""
    names = {"detection_models", "recognition_models", "latin_lang_list", "arabic_lang_list",
             "bengali_lang_list", "cyrillic_lang_list", "devanagari_lang_list", "other_lang_list"}
    def filenames_only(node):
        if not isinstance(node, ast.Dict):
            raise ValueError("unknown_ocr_config")
        entries = {ast.literal_eval(key): value for key, value in zip(node.keys, node.values)}
        if "filename" in entries:
            # Character tables can contain expressions. They are irrelevant to
            # file availability and are deliberately neither read nor executed.
            return {"filename": ast.literal_eval(entries["filename"])}
        return {key: filenames_only(value) for key, value in entries.items()}
    try:
        if config_path.stat().st_size > 2_000_000:
            raise ValueError("oversized_ocr_config")
        parsed = ast.parse(config_path.read_text(encoding="utf-8"))
        values = {node.targets[0].id: (filenames_only(node.value) if node.targets[0].id.endswith("_models")
                                     else ast.literal_eval(node.value)) for node in parsed.body
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names}
        if set(values) != names:
            raise ValueError("unknown_ocr_config")
        groups = {name: set(values[name]) for name in names if name.endswith("_lang_list")}
        selected = set(languages)
        if not selected or not selected <= set().union(*groups.values()):
            raise ValueError("invalid_document_docling_ocr_languages")
        if selected == {"en"}:
            generation, model, compatible = "gen2", "english_g2", {"en"}
        else:
            choices = [
                ({"th"}, "gen1", "thai_g1"), ({"ch_tra"}, "gen1", "zh_tra_g1"),
                ({"ch_sim"}, "gen2", "zh_sim_g2"), ({"ja"}, "gen2", "japanese_g2"),
                ({"ko"}, "gen2", "korean_g2"), ({"ta"}, "gen1", "tamil_g1"),
                ({"te"}, "gen2", "telugu_g2"), ({"kn"}, "gen2", "kannada_g2"),
                (groups["bengali_lang_list"], "gen1", "bengali_g1"),
                (groups["arabic_lang_list"], "gen1", "arabic_g1"),
                (groups["devanagari_lang_list"], "gen1", "devanagari_g1"),
                (groups["cyrillic_lang_list"], "gen2", "cyrillic_g2"),
                (groups["latin_lang_list"], "gen2", "latin_g2"),
            ]
            family, generation, model = next(choice for choice in choices if selected & choice[0])
            compatible = family | {"en"}
        if not selected <= compatible:
            raise ValueError("invalid_document_docling_ocr_languages")
        files = (values["detection_models"]["craft"]["filename"],
                 values["recognition_models"][generation][model]["filename"])
        if any(not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."} for name in files):
            raise ValueError("unknown_ocr_config")
        return files
    except ValueError as exc:
        if str(exc) == "invalid_document_docling_ocr_languages":
            raise
        raise ValueError("document_docling_unavailable") from exc
    except (OSError, SyntaxError, TypeError, KeyError, StopIteration) as exc:
        raise ValueError("document_docling_unavailable") from exc


def validate_runtime_assets(config, runtime_info=None, *, inline_runtime_info=None):
    """Check every required file exists and is nonempty, without reading weights."""
    if config.get("formula_enrichment"):
        runtime_info = runtime_info if runtime_info is not None else inspect_runtime_cached(config["runtime_python"])
        versions = runtime_info.get("packages", {})
        if (not isinstance(versions, dict)
                or any(versions.get(name) != version for name, version in _FORMULA_PACKAGES.items())):
            raise ValueError("document_docling_version_mismatch")
    root = Path(config["artifacts_path"])
    _require_files(root / "docling-project--docling-layout-heron", _LAYOUT_FILES,
                   "document_docling_runtime_models_required")
    _require_files(root / "docling-project--docling-models/model_artifacts/tableformer/accurate",
                   _TABLE_FILES, "document_docling_runtime_models_required")
    if config.get("formula_enrichment"):
        _require_files(root / "breezedeus--pix2text-mfr-1.5", _FORMULA_FILES,
                       "document_docling_runtime_formula_models_required")
    if config.get("inline_runtime_python"):
        from lib.infrastructure.docling_runtime import INLINE_LAYOUT_PACKAGES, RUNTIME_PROTOCOL
        from lib.infrastructure.docling_inline_formulas import INLINE_LAYOUT_DIRECTORY
        if not config.get("runtime_python") or not config.get("formula_enrichment"):
            raise ValueError("document_docling_inline_runtime_requires_formula")
        formula_versions = (runtime_info or {}).get("packages", {})
        if (not isinstance(formula_versions, dict)
                or any(formula_versions.get(name) != version for name, version in _INLINE_FORMULA_PACKAGES.items())):
            raise ValueError("document_docling_inline_version_mismatch")
        info = inline_runtime_info if inline_runtime_info is not None else inspect_runtime_cached(
            config["inline_runtime_python"], service="inline_layout")
        versions = info.get("packages", {})
        if (info.get("protocol") != RUNTIME_PROTOCOL or not isinstance(versions, dict)
                or any(versions.get(name) != version for name, version in INLINE_LAYOUT_PACKAGES.items())):
            raise ValueError("document_docling_inline_version_mismatch")
        _require_files(root / INLINE_LAYOUT_DIRECTORY, _INLINE_LAYOUT_FILES,
                       "document_docling_inline_models_required")
    if config.get("ocr"):
        info = runtime_info or inspect_runtime_cached(config["runtime_python"])
        if not info.get("packages", {}).get("easyocr"):
            raise ValueError("document_docling_unavailable")
        metadata = info.get("metadata_files", {}).get("easyocr")
        if not metadata:
            raise ValueError("document_docling_unavailable")
        config_path = Path(metadata).parent.parent / "easyocr" / "config.py"
        required = _ocr_required_files(config_path, config["ocr_languages"])
        _require_files(Path(config["ocr_models_path"]), required, "document_docling_ocr_models_required")
