"""Exercise the Streamlit review presenters with real conversation rendering and fake widgets."""
from __future__ import annotations

from copy import deepcopy

from lib.presentation.streamlit import corpus_review_page, preference_review_page, sft_review_page


class FakeStreamlit:
    def __init__(self, *, submit: str | None = None, edits: dict | None = None):
        self.session_state = {}
        self.submit = submit
        self.edits = edits or {}
        self.html_blocks = []
        self.radio_labels = []
        self.text_areas = []
        self.buttons = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def container(self, **_):
        return self

    def expander(self, *_args, **_kwargs):
        return self

    def form(self, *_args, **_kwargs):
        return self

    def columns(self, spec, **_):
        return [self] * (spec if isinstance(spec, int) else len(spec))

    def html(self, markup):
        self.html_blocks.append(markup)

    def selectbox(self, _label, options, *, key=None, **_):
        return self.session_state.get(key, options[0])

    def radio(self, _label, options, *, key=None, format_func=None, **_):
        self.radio_labels.extend(format_func(option) for option in options)
        return self.session_state.get(key, options[0])

    def number_input(self, _label, **kwargs):
        return kwargs["value"]

    def toggle(self, _label, *, key=None, value=False, **_):
        return self.session_state.get(key, value)

    def text_area(self, label, *, value="", **_):
        self.text_areas.append(label)
        return self.edits.get(label, value)

    def text_input(self, label, *, value="", **_):
        return self.edits.get(label, value)

    def form_submit_button(self, label, **_):
        return label == self.submit

    def button(self, label, **kwargs):
        self.buttons.append((label, kwargs.get("disabled")))
        return False

    def __getattr__(self, name):
        if name in {"subheader", "caption", "metric", "progress", "warning", "info",
                    "success", "error", "rerun", "download_button"}:
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)


class FakeReviewApplication:
    def __init__(self, *, target, sample, counts=None, evidence=None, review=None):
        self.target = target
        self.sample = sample
        self.counts = counts or {"pending": 1, "approved": 0, "rejected": 0, "skipped": 0}
        self.evidence = evidence if evidence is not None else {"quotes": ["原始证据"]}
        self.review = review
        self.decisions = []

    def reviewable_runs(self):
        count_key = "sample_count" if self.target in {"sft", "cpt"} else "pair_count"
        return [{"id": "r" * 32, "name": "审核任务", count_key: 1}]

    def queue(self, _run_id, **_):
        identity_key = "sample_id" if self.target in {"sft", "cpt"} else "pair_id"
        row_key = "row" if self.target in {"sft", "cpt"} else "pair"
        return {"counts": self.counts, "total": 1,
                "items": [{identity_key: "a" * 64, row_key: deepcopy(self.sample),
                           "evidence": deepcopy(self.evidence), "review": deepcopy(self.review)}]}

    def decide(self, *args, **kwargs):
        self.decisions.append((args, kwargs))


def test_sft_three_column_preview_escapes_input_and_edits_only_assistant(monkeypatch):
    sample = {"messages": [
        {"role": "user", "content": "请解释 <script>alert(1)</script> ![x](https://remote/img)"},
        {"role": "assistant", "content": "先调用工具", "toolCalls": [{"id": "call-1", "name": "calculator", "args": {"x": 1}}]},
        {"role": "tool", "toolCallId": "call-1", "content": "工具结果 1"},
        {"role": "assistant", "content": "最终答案", "reasoning_content": "可核对"},
    ]}
    app = FakeReviewApplication(target="sft", sample=sample)
    fake = FakeStreamlit()
    monkeypatch.setattr(sft_review_page, "st", fake)
    sft_review_page.render_sft_review(app)

    rendered = "\n".join(fake.html_blocks)
    assert "df-review-dialogue" in rendered and "工具调用" in rendered and "calculator" in rendered
    assert "&lt;script&gt;" in rendered and "<script>alert(1)</script>" not in rendered
    assert "https://remote/img" not in rendered
    assert all("<script>" not in label for label in fake.radio_labels)
    assert not any(label.startswith("第 ") for label in fake.text_areas)
    assert ("生成已审核 SFT 版本", True) in fake.buttons

    fake = FakeStreamlit(submit="通过并保存修订", edits={"第 4 条助手回答": "人工修订"})
    fake.session_state[f"sft-review-edit:{'r' * 32}:{'a' * 64}"] = True
    monkeypatch.setattr(sft_review_page, "st", fake)
    monkeypatch.setattr("lib.review_management.reviewer_identity", lambda: "审阅者")
    sft_review_page.render_sft_review(app)
    args, decision = app.decisions[-1]
    assert args[1] == "a" * 64 and decision["expected_hash"] == "a" * 64
    assert decision["decision"] == "approved"
    assert decision["candidate"]["messages"][0] == sample["messages"][0]
    assert decision["candidate"]["messages"][1]["toolCalls"] == sample["messages"][1]["toolCalls"]
    assert decision["candidate"]["messages"][3]["content"] == "人工修订"


def test_dpo_compares_answers_and_swap_preserves_prompt(monkeypatch):
    pair = {"prompt": [{"role": "user", "content": "比较 <b>答案</b>"}],
            "chosen": [{"role": "assistant", "content": "更优回答"}],
            "rejected": [{"role": "assistant", "content": "较弱回答"}], "tools": []}
    app = FakeReviewApplication(target="dpo", sample=pair)
    fake = FakeStreamlit(submit="交换偏好后通过")
    monkeypatch.setattr(preference_review_page, "st", fake)
    monkeypatch.setattr("lib.review_management.reviewer_identity", lambda: "审阅者")
    preference_review_page.render_preference_review(app, show_header=False)

    rendered = "\n".join(fake.html_blocks)
    assert "df-review-compare-card chosen" in rendered and "df-review-compare-card rejected" in rendered
    assert "&lt;b&gt;答案&lt;/b&gt;" in rendered and "<b>答案</b>" not in rendered
    assert not any(label.startswith("修订") for label in fake.text_areas)
    _, decision = app.decisions[-1]
    assert decision["expected_hash"] == "a" * 64 and decision["decision"] == "approved"
    assert (decision["chosen"], decision["rejected"]) == ("较弱回答", "更优回答")
    assert pair["prompt"] == [{"role": "user", "content": "比较 <b>答案</b>"}]
    assert ("生成已审核 DPO 版本", True) in fake.buttons

    editing = FakeStreamlit(submit="通过并保存修订", edits={"修订更优回答": "人工确认的更优回答"})
    editing.session_state[f"preference-review-edit:{'r' * 32}:{'a' * 64}"] = True
    monkeypatch.setattr(preference_review_page, "st", editing)
    preference_review_page.render_preference_review(app, show_header=False)
    assert "修订更优回答" in editing.text_areas and "修订对照回答" in editing.text_areas
    _, revised = app.decisions[-1]
    assert revised["chosen"] == "人工确认的更优回答"
    assert revised["rejected"] == "较弱回答"


def test_orpo_review_surface_uses_its_own_queue_state_and_release_label(monkeypatch):
    pair = {"prompt": [{"role": "user", "content": "设备如何检修？"}],
            "chosen": [{"role": "assistant", "content": "断电后检查。"}],
            "rejected": [{"role": "assistant", "content": "带电检查。"}], "tools": []}
    app = FakeReviewApplication(target="orpo", sample=pair)
    fake = FakeStreamlit(submit="通过并保存修订")
    fake.session_state["preference-release:" + "r" * 32] = b"old dpo package"
    monkeypatch.setattr(preference_review_page, "st", fake)
    monkeypatch.setattr("lib.review_management.reviewer_identity", lambda: "审阅者")
    preference_review_page.render_preference_review(app, show_header=False)
    assert ("生成已审核 ORPO 版本", True) in fake.buttons
    assert not any(label == "生成已审核 DPO 版本" for label, _ in fake.buttons)
    assert fake.session_state["preference-release:" + "r" * 32] == b"old dpo package"
    assert "preference-review-item:" + "r" * 32 + ":1:orpo" in fake.session_state
    assert "ORPO 偏好对" in "\n".join(fake.html_blocks)
    assert app.decisions[-1][1]["decision"] == "approved"


def test_cpt_three_column_review_uses_real_evidence_and_edits_only_when_requested(monkeypatch):
    sample = {"text": "设备维护 <script>alert(1)</script>\n第二段 ![外链](https://remote/img)"}
    evidence = {"kind": "document", "source_id": "手册 <admin>", "location": 7,
                "evidence_level": "source_text", "judge": {"score": 0.94}}
    app = FakeReviewApplication(target="cpt", sample=sample, evidence=evidence)
    fake = FakeStreamlit()
    monkeypatch.setattr(corpus_review_page, "st", fake)
    corpus_review_page.render_corpus_review(app, show_header=False)

    rendered = "\n".join(fake.html_blocks)
    assert "df-review-corpus-card original" in rendered
    assert "设备维护 &lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "手册 &lt;admin&gt;" in rendered and "0.94" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "修订后的训练语料" not in fake.text_areas
    assert all("<script>" not in label for label in fake.radio_labels)
    assert ("生成已审核 CPT 版本", True) in fake.buttons

    editing = FakeStreamlit(submit="通过并保存修订", edits={"修订后的训练语料": "人工核准的语料"})
    editing.session_state[f"corpus-review-edit:{'r' * 32}:{'a' * 64}"] = True
    monkeypatch.setattr(corpus_review_page, "st", editing)
    monkeypatch.setattr("lib.review_management.reviewer_identity", lambda: "审阅者")
    corpus_review_page.render_corpus_review(app, show_header=False)
    assert "修订后的训练语料" in editing.text_areas
    args, decision = app.decisions[-1]
    assert args[1] == "a" * 64 and decision["expected_hash"] == "a" * 64
    assert decision["decision"] == "approved" and decision["text"] == "人工核准的语料"

    history = corpus_review_page._history_html({
        "decision": "approved", "reviewer": "<admin>", "reviewed_at": "2026-09-23", "reason": "<script>bad</script>",
    })
    assert "&lt;admin&gt;" in history and "&lt;script&gt;bad&lt;/script&gt;" in history
