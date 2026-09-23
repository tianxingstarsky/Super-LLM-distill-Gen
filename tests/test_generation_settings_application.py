"""Generation settings edits remain atomic and recoverable across sessions."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.infrastructure.generation_settings_file import FileGenerationSettingsDriver


SOURCE = Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml"


def _application(tmp_path: Path) -> tuple[GenerationSettingsApplication, Path]:
    configs = tmp_path / "configs"
    configs.mkdir()
    path = configs / "preferences.yaml"
    path.write_bytes(SOURCE.read_bytes().replace(b"\n", b"\r\n"))
    (configs / "cot_styles.yaml").write_text("styles: {}\n", encoding="utf-8")
    (configs / "style_rules.example.yaml").write_text("rules: []\n", encoding="utf-8")
    return GenerationSettingsApplication(FileGenerationSettingsDriver(tmp_path)), path


def test_form_save_preserves_original_backup_and_detects_stale_editor(tmp_path):
    app, path = _application(tmp_path)
    original = path.read_bytes()
    snapshot = app.load("生成偏好")
    summary = app.summary(snapshot)
    assert "\r\n" in snapshot.text
    updated = app.save_form(
        snapshot, default_share=0.25,
        relative_tendencies=summary["relative_tendencies"],
        templates_per_dim=summary["templates_per_dim"] + 1,
        shuffle_per_batch=summary["shuffle_per_batch"],
        correction_enabled=summary["correction_enabled"],
        correction_threshold=summary["correction_threshold"],
        correction_tagger=summary["correction_tagger"], cot_style=summary["cot_style"],
    )
    assert path.read_bytes() == updated.encode("utf-8")
    assert app.summary(app.load("生成偏好"))["default_share"] == 0.25
    backups = list(path.parent.glob("preferences.yaml.*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    with pytest.raises(ValueError, match="配置已被其他会话更新"):
        app.save_raw(snapshot, snapshot.text)
    assert path.read_bytes() == updated.encode("utf-8")
    assert len(list(path.parent.glob("preferences.yaml.*.bak"))) == 1


def test_invalid_raw_edit_is_rejected_before_touching_file(tmp_path):
    app, path = _application(tmp_path)
    snapshot = app.load("生成偏好")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="invalid_generation_settings_yaml"):
        app.save_raw(snapshot, "preferences: [broken")
    with pytest.raises(ValueError, match="preference_weights_must_sum_to_one"):
        app.save_raw(snapshot, snapshot.text.replace("default: 0.15", "default: 0.35"))
    assert path.read_bytes() == original
    assert not list(path.parent.glob("preferences.yaml.*.bak"))


def test_malformed_style_yaml_can_be_repaired_in_advanced_editor(tmp_path):
    app, _ = _application(tmp_path)
    path = tmp_path / "configs" / "cot_styles.yaml"
    path.write_text("styles: [broken", encoding="utf-8")
    snapshot = app.load("思考风格")
    assert snapshot.parse_error and snapshot.document is None
    app.save_raw(snapshot, "styles:\n  concise: {}\n")
    assert app.load("思考风格").document == {"styles": {"concise": {}}}
    assert len(list(path.parent.glob("cot_styles.yaml.*.bak"))) == 1


def test_category_is_limited_to_fixed_documents(tmp_path):
    app, _ = _application(tmp_path)
    assert app.categories() == ("生成偏好", "思考风格", "语言规则")
    with pytest.raises(ValueError, match="unknown_generation_settings_category"):
        app.load("../outside")
