"""Atomic compare-and-swap persistence for fixed local settings files."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4


_WRITE_LOCK = RLock()


class FileGenerationSettingsDriver:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._paths = {
            "生成偏好": self._root / "configs" / "preferences.yaml",
            "思考风格": self._root / "configs" / "cot_styles.yaml",
            "语言规则": self._root / "configs" / "style_rules.example.yaml",
        }

    def _path(self, category: str) -> Path:
        try:
            path = self._paths[category]
        except KeyError as error:
            raise ValueError("unknown_generation_settings_category") from error
        if path.is_symlink():
            raise ValueError("linked_generation_settings_file")
        return path

    def read(self, category: str) -> str:
        return self._path(category).read_bytes().decode("utf-8")

    def compare_and_swap(self, category: str, expected: str, replacement: str) -> None:
        path = self._path(category)
        with _WRITE_LOCK:
            previous = path.read_bytes()
            if previous.decode("utf-8") != expected:
                raise ValueError("配置已被其他会话更新，请刷新页面后重试")
            backup = path.with_suffix(path.suffix + "." + datetime.now().strftime("%Y%m%d%H%M%S%f")
                                      + "." + uuid4().hex[:8] + ".bak")
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                                 prefix=f".{path.name}.", suffix=".tmp",
                                                 delete=False) as output:
                    temporary = Path(output.name)
                    output.write(replacement.encode("utf-8"))
                    output.flush()
                    os.fsync(output.fileno())
                backup.write_bytes(previous)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
