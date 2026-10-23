"""Legacy append-only JSONL writer; workflow publication uses verified bundles."""
import json
import pathlib
from typing import Dict, List


def write_corpus_jsonl(entries: List[Dict[str, str]], out_path: str | pathlib.Path) -> int:
    """写 CPT 语料（minimind/LLaMA-Factory --stage pt 兼容的 {"text"} 行）。"""
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as output:
        for entry in entries:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(entries)
