"""A release must pass current-content review before filesystem side effects."""
from __future__ import annotations

import json

import pytest

from lib.application.release_service import ReleaseApplication
from lib.bootstrap.releases import release_application
from lib.domain.release_quality import sample_hash


def _sample(number: int) -> dict:
    return {"id": f"row-{number}", "messages": [
        {"role": "user", "content": f"Question {number}"},
        {"role": "assistant", "content": f"Answer {number}"},
    ]}


def test_bulk_gate_rejects_stale_votes_before_release_driver_is_called(tmp_path):
    class Driver:
        def write_release(self, *args, **kwargs):
            pytest.fail("blocked bulk release reached filesystem adapter")

    samples = [_sample(number) for number in range(10)]
    votes = [{"sample_id": row["id"], "sample_hash": sample_hash(row), "decision": "keep"}
             for row in samples]
    changed = json.loads(json.dumps(samples))
    changed[0]["messages"][-1]["content"] = "Changed answer"
    changed[1]["messages"][-1]["content"] = "Another changed answer"

    application = ReleaseApplication(Driver())
    with pytest.raises(ValueError, match="review_coverage_below_90_percent"):
        application.export_release(changed, "chat", tmp_path, votes, bulk=True)
    assert list(tmp_path.iterdir()) == []


def test_dataset_scoped_report_gets_votes_through_port():
    row = _sample(1)

    class Driver:
        def review_decisions(self, dataset_name):
            assert dataset_name == "workspace-review"
            return [{"sample_id": row["id"], "sample_hash": sample_hash(row),
                     "decision": "keep"}]

    result = ReleaseApplication(Driver()).quality_report_for_dataset([row], "workspace-review")
    assert result["review_coverage"] == 1
    assert result["review"]["reviewed"] == 1


def test_invalid_jsonl_is_reported_with_line_and_has_no_release(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps(_sample(1)) + "\n\n[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"samples\.jsonl:3: expected an object"):
        release_application().read_samples(path)
    assert list(tmp_path.iterdir()) == [path]


def test_lossy_minimind_conversion_keeps_incomplete_manifest(tmp_path):
    row = {**_sample(1), "images": ["frame.png"]}
    with pytest.raises(ValueError, match="images"):
        release_application().export_release([row], "minimind", tmp_path, tag="image-release")
    destination = tmp_path / "image-release"
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"status": "writing", "format": "minimind"}
    assert not (destination / "sft_t2t.jsonl").read_text(encoding="utf-8")
