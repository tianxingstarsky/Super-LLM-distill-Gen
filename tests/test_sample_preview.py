import json

import pytest

from lib.infrastructure import sample_preview


def test_large_file_normalizes_only_selected_records(tmp_path, monkeypatch):
    path = tmp_path / "samples.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for i in range(50000):
            handle.write(json.dumps({"id": str(i), "messages": [{"role": "user", "content": str(i)}]}) + "\n")
    normalized = []
    original = sample_preview.normalize_sample
    def normalize(row):
        normalized.append(row["id"])
        return original(row)
    monkeypatch.setattr(sample_preview, "normalize_sample", normalize)
    preview = sample_preview.SamplePreview(path)
    assert len(preview) == 50000 and normalized == []
    assert preview[49999]["id"] == "49999"
    assert preview[0]["id"] == "0"
    assert normalized == ["49999", "0"]


def test_blank_lines_invalid_selected_record_and_replacement(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text('\n{"id":"first","messages":[{"role":"user","content":"hi"}]}\n\n[1,2]\n', encoding="utf-8")
    preview = sample_preview.SamplePreview(path)
    assert len(preview) == 2 and preview[0]["id"] == "first"
    with pytest.raises(ValueError, match="record 2: expected an object"):
        preview[1]
    with pytest.raises(IndexError):
        preview[2]
    path.write_text('{"id":"replacement","messages":[{"role":"user","content":"hi"}]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="preview_file_changed"):
        len(preview)
    assert sample_preview.SamplePreview(path)[0]["id"] == "replacement"
