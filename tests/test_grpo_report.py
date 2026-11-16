"""Receipt-backed GRPO report behavior, without an ML runtime or network."""
from copy import deepcopy
from html.parser import HTMLParser

from lib.contexts.training.infrastructure.grpo_report import render_grpo_report


def manifest():
    return {"identity": "frozen-example", "configuration": {"seed": 1729, "epochs": 1.0},
            "model": {"model_id": "example/local-model"}, "tasks": {
                "train": [{"task_id": "train-1", "family": "set_counting", "prompt": "Training question", "expected_answer": "1", "split": "train"}],
                "evaluation": [{"task_id": "heldout-1", "family": "set_counting", "prompt": "How many belong to exactly one set?", "expected_answer": "42", "split": "evaluation"},
                               {"task_id": "heldout-2", "family": "inventory_balance", "prompt": "How much remains?", "expected_answer": "3/2", "split": "evaluation"}]}}


def row(case_id, *, correct=True, **overrides):
    return {"case_id": case_id, "messages": [{"role": "user", "content": "Actual question"}],
            "prompt": "Rendered prompt", "reasoning": "Full native reasoning", "answer": "Final answer: \\boxed{42}",
            "raw_output": "<think>Full native reasoning</think>Final answer: \\boxed{42}",
            "output_tokens": 20, "seconds": 2.0, "truncated": False,
            "thinking_closed": True, "ended_with_eos": True,
            "reward": float(correct), "verification": {"answer_correct": correct}, **overrides}


def receipt(*cases):
    return {"status": "completed", "cases": list(cases), "seconds": 12.0,
            "model_load_seconds": 3.0, "cuda_peak_allocated_bytes": 2 * 1024 ** 3}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_report_pairs_actual_cases_and_separates_numeric_correctness_from_reasoning_completion(tmp_path):
    stages = {"baseline": receipt(row("heldout-1", correct=False), row("heldout-2")),
              "grpo_train": {"status": "completed", "actual_epochs": 1.0, "global_steps": 8,
                             "peak_allocated_bytes": 4 * 1024 ** 3, "rollout_output_tokens": 200},
              "grpo_eval": receipt(row("heldout-1", reasoning="", thinking_closed=False), row("heldout-2"))}
    result = render_grpo_report(manifest(), stages, tmp_path)
    assert result["evaluation"]["baseline"]["correct"] == 1
    assert result["evaluation"]["grpo_eval"]["accuracy"] == 1
    assert result["evaluation"]["grpo_eval"]["complete_cases"] == 1
    assert result["paired_changes"] == {"improved": 1, "regressed": 0, "stable_correct": 1,
                                        "stable_incorrect": 0, "pending": 0}
    assert result["families"]["set_counting"]["grpo_eval"]["correct"] == 1
    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "4.00 GiB" in document and "2.00 GiB" in document
    assert "How many belong to exactly one set?" in document and "3/2" in document
    assert "完整原生推理" in document and "参考数值" in document and "推理语义未独立审核" in document
    tags = Tags()
    tags.feed(document)
    assert sum(tag == "article" for tag, _ in tags.tags) == 4
    assert sum(tag == "details" and "data-reasoning" in attrs for tag, attrs in tags.tags) == 4
    assert not any(tag in ("link", "iframe", "img") or (tag == "script" and "src" in attrs) for tag, attrs in tags.tags)


def test_truncated_or_non_eos_outputs_cannot_count_correct_even_with_stale_reward(tmp_path):
    stages = {"baseline": receipt(row("heldout-1", truncated=True), row("heldout-2", ended_with_eos=False)),
              "grpo_eval": receipt(row("heldout-1"), row("heldout-2", correct=False))}
    result = render_grpo_report(manifest(), stages, tmp_path / "custom-report.html")
    assert result["report"] == "custom-report.html"
    assert result["evaluation"]["baseline"]["correct"] == 0
    assert result["evaluation"]["baseline"]["accuracy"] == 0
    assert result["evaluation"]["baseline"]["complete_cases"] == 0
    assert result["paired_changes"]["improved"] == 1


def test_missing_stages_and_unknown_metrics_are_pending_without_zero_accuracy(tmp_path):
    result = render_grpo_report(manifest(), {}, tmp_path)
    assert result["status"] == "incomplete"
    assert result["evaluation"]["baseline"]["accuracy"] is None
    assert result["evaluation"]["baseline"]["correct"] is None
    assert result["evaluation"]["baseline"]["output_tokens"] is None
    assert result["paired_changes"]["pending"] == 2
    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "待执行" in document and "待记录" in document
    assert "0.0%" not in document and "0.00 GiB" not in document


def test_unverified_saved_case_is_pending_and_partial_accuracy_states_coverage(tmp_path):
    unverified = row("heldout-1", verification={})
    result = render_grpo_report(manifest(), {"baseline": receipt(unverified)}, tmp_path)
    summary = result["evaluation"]["baseline"]
    assert summary["saved_cases"] == 1 and summary["missing_cases"] == 1
    assert summary["pending_verification"] == 1 and summary["accuracy"] is None
    assert summary["output_tokens"] == 20 and summary["tokens_per_second"] == 10
    assert "1 / 2" in (tmp_path / "report.html").read_text(encoding="utf-8")


def test_all_artifact_strings_are_escaped_and_portable_receipts_hide_machine_paths_and_secrets(tmp_path):
    source = manifest()
    source["identity"] = "<svg onload='attack()'>"
    source["tasks"]["evaluation"][0]["prompt"] = "<img src=x onerror=attack()> & a < b"
    source["configuration"].update({"model": r"C:\Users\example\private-model", "api_key": "private-secret-value"})
    source["model"]["path"] = "/opt/private/models/model"
    unsafe = row("heldout-1", answer="</pre><script>attack()</script>", reasoning="<b>reasoning</b>",
                 raw_output="/home/example/private/model", verification={"answer_correct": False})
    stages = {"baseline": receipt(unsafe)}
    snapshot = deepcopy((source, stages))
    render_grpo_report(source, stages, tmp_path)
    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "&lt;img src=x onerror=attack()&gt; &amp; a &lt; b" in document
    assert "&lt;/pre&gt;&lt;script&gt;attack()&lt;/script&gt;" in document
    assert "&lt;b&gt;reasoning&lt;/b&gt;" in document
    assert "private-secret-value" not in document
    assert "C:\\Users" not in document and "/opt/private" not in document and "/home/example" not in document
    assert "[已隐藏凭据]" in document and "[本机路径已隐藏]" in document
    parsed = Tags()
    parsed.feed(document)
    assert sum(tag == "script" for tag, _ in parsed.tags) == 1
    assert not any(tag in ("img", "svg") for tag, _ in parsed.tags)
    assert (source, stages) == snapshot
