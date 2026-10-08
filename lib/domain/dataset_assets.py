"""File-catalog contracts and classification without storage dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


TRAINING_CATEGORIES = frozenset({"语料", "样本", "DPO 偏好对", "其他偏好数据"})
OUTPUT_SUFFIXES = frozenset({".jsonl", ".json", ".html", ".txt", ".md", ".csv", ".xlsx", ".zip"})
DIRECT_DOWNLOAD_LIMIT_BYTES = 50 * 1024 * 1024
# Reusable input types only; image parsing still requires explicit vision setup.
DOCUMENT_SOURCE_SUFFIXES = frozenset({".md", ".txt", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"})
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
    if name.endswith(".records.json"):
        return "报告与状态"
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
    if asset.origin == "source" or asset.category in TRAINING_CATEGORIES:
        return True
    return asset.relative_path.lower().startswith("export/") and asset.name.lower() == "manifest.json"


def asset_sort_key(asset: Asset) -> tuple:
    document = asset.suffix in {".pdf", ".docx", ".md", ".txt"}
    return (0 if asset.origin == "source" else 1,
            0 if asset.origin == "source" and document else 1,
            asset.name.casefold(), asset.relative_path.casefold())
