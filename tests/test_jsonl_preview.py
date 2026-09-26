import json
from lib.infrastructure import jsonl_preview as preview


def test_sparse_index_seeks_last_row_and_refreshes_on_replacement(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("\n".join(json.dumps({"id": i, "text": "原文"}, ensure_ascii=False)
                              for i in range(50000)) + "\n", encoding="utf-8")
    assert preview.read_rows(path, 49999, 1) == [{"id": 49999, "text": "原文"}]
    positions, count = preview._offsets(str(path.resolve()), preview._identity(path))
    assert count == 50000 and len(positions) == 391
    before = preview._offsets.cache_info().hits
    assert preview.read_rows(path, 49000, 2)[0]["id"] == 49000
    assert preview._offsets.cache_info().hits == before + 1
    replacement = tmp_path / "replacement"
    replacement.write_text('{"id": "replacement"}\n', encoding="utf-8")
    replacement.replace(path)
    assert preview.read_rows(path, 1, 1) == []
    assert preview.read_rows(path, 0, 1) == [{"id": "replacement"}]


def test_first_preview_does_not_build_index_and_empty_lines_do_not_count(tmp_path, monkeypatch):
    path = tmp_path / "data.jsonl"
    path.write_text('\n{"id": 0}\n  \n{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    assert preview.read_rows(path, 1, 1) == [{"id": 1}]
    monkeypatch.setattr(preview, "_offsets", lambda *args: (_ for _ in ()).throw(AssertionError("index")))
    assert preview.read_rows(path, 0, 1) == [{"id": 0}]
