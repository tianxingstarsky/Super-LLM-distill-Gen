"""File-catalog contracts and classification without storage dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


TRAINING_CATEGORIES = frozenset({"语料", "样本", "DPO 偏好对", "其他偏好数据"})
OUTPUT_SUFFIXES = frozenset({".jsonl", ".json", ".html", ".txt", ".md", ".csv", ".xlsx", ".zip"})
DIRECT_DOWNLOAD_LIMIT_BYTES = 50 * 1024 * 1024
# Reusable input types only; image parsing still requires explicit vision setup.
DOCUMENT_SOURCE_SUFFIXES = frozenset({".md", ".txt", ".tex", ".latex", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"})
CONVERSATION_SOURCE_SUFFIXES = frozenset({".json", ".jsonl"})


@dataclass(frozen=True)
class Asset:
    id: str
    origin: str
    relative_path: str
    category: str
    size: int
    mtime_ns: int

    @property
    def name(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.relative_path).suffix.lower()

    @property
    def label(self) -> str:
        return ("来源 / " if self.origin == "source" else "产物 / ") + self.relative_path

    @property
    def run_id(self) -> str | None:
        parts = PurePosixPath(self.relative_path).parts
        if (self.origin == "output" and len(parts) >= 3 and parts[0] == "workflows"
                and len(parts[1]) == 32 and all(c in "0123456789abcdef" for c in parts[1])):
            return parts[1]
        return None


@dataclass(frozen=True)
class AssetInventory:
    assets: tuple[Asset, ...]
    truncated: bool = False


def generation_source_mode(asset: Asset) -> str:
    """Only original, supported library inputs can enter a creation draft."""
    if asset.origin != "source":
        raise ValueError("asset_not_generation_source")
    if asset.suffix in DOCUMENT_SOURCE_SUFFIXES:
        return "文档资料"
    if asset.suffix in CONVERSATION_SOURCE_SUFFIXES:
        return "Agent 上下文"
    raise ValueError("asset_not_generation_source")


def output_category(relative_path: str) -> str:
    """Classify a generated file by its role, never by an example UI value."""
    path = PurePosixPath(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    suffix = path.suffix.lower()
    if len(parts) >= 4 and parts[0] == "workflows" and parts[2] == "inputs":
        return "输入快照"
    if len(parts) >= 4 and parts[0] == "workflows" and parts[2] in {
            "checkpoints", "streams", "stage-results", "production", "source-context", "analysis"}:
        return "运行记录"
    if name.endswith(".records.json"):
        return "报告与状态"
    if name=="source_figures.zip":
        return "语料"  # Original image/text corpus; visible in normal assets.
    if suffix == ".zip":
        return "数据包"
    if "dpo" in name and suffix == ".jsonl":
        return "DPO 偏好对"
    if any(token in name for token in ("orpo", "rlaif")) and suffix == ".jsonl":
        return "其他偏好数据"
    if "corpus" in parts or name.startswith("corpus") or "cpt" in name or "pretrain" in name:
        return "语料"
    if suffix == ".jsonl" and any(token in name for token in
                                   ("samples", "sft", "agent", "multiturn", "cot", "gsm8k", "chat", "llamafactory")):
        return "样本"
    return "报告与状态"


def common_asset(asset: Asset) -> bool:
    if asset.origin == "source" or asset.category in TRAINING_CATEGORIES or asset.category == "数据包":
        return True
    return asset.relative_path.lower().startswith("export/") and asset.name.lower() == "manifest.json"


def library_output_directory(relative_path: str) -> bool:
    """Select product-facing folders before traversal; never enumerate checkpoints."""
    parts = PurePosixPath(relative_path).parts
    if any(part.startswith(".") for part in parts):
        return False
    if parts and parts[0] in {"human-sessions", "gate_audit", "review_batches"}:
        return False
    if len(parts) >= 3 and parts[0] == "workflows":
        return parts[2] in {"artifacts", "delivery", "human-review"}
    return "review_batches" not in parts


def library_output_file(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.name.startswith(".") or not library_output_directory(str(path.parent)):
        return False
    # Task root files are execution metadata, even if a name contains 'sft'.
    if len(path.parts) == 3 and path.parts[0] == "workflows":
        return False
    category = output_category(relative_path)
    return (category in TRAINING_CATEGORIES or category == "数据包"
            or path.name.lower() in {"manifest.json", "quality.json"}
            or path.suffix.lower() == ".html")


def asset_sort_key(asset: Asset) -> tuple:
    document = asset.suffix in {".pdf", ".docx", ".md", ".txt", ".tex", ".latex"}
    return (0 if asset.origin == "source" else 1,
            0 if asset.origin == "source" and document else 1,
            -asset.mtime_ns if asset.origin == "output" else 0,
            asset.name.casefold(), asset.relative_path.casefold())
