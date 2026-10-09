"""Review evidence reports actual coverage without treating samples as a census."""
from html.parser import HTMLParser

import pytest
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.workflow_package_review_report import package_review_report_html


class TableRows(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.rows = []
        self.current = None
        self.cell = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current = []
        elif tag in {"td", "th"}:
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.current.append(self.cell)
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.current)
            self.current = None


def report(mode="sample"):
    return {"package_review": {"enabled": True, "mode": mode, "targets": {
        "sft": {"candidates": 1000, "planned": 100, "reviewed": 100,
                "accepted": 95, "rejected": 5, "unreviewed": 900, "coverage_percent": 10.0,
                "status": "sampled"},
        "cpt": {"candidates": 400, "planned": 400, "reviewed": 400,
                "accepted": 390, "rejected": 10, "unreviewed": 0, "coverage_percent": 100.0,
                "status": "all_reviewed"},
    }}}


def test_sampled_review_keeps_target_evidence_and_explicit_unreviewed_scope():
    markup = package_review_report_html(report())
    assert "抽样评审" in markup
    assert "未抽中的样本沿用上游质检结果" in markup
    assert "抽样评审不代表所有样本均已通过 AI 评审" in markup
    assert "AI 评审已覆盖本次进入打包的全部样本" not in markup
    assert "500 / 1,400" in markup
    assert "35.7%" in markup
    assert TableRows(markup).rows[1:] == [
        ["SFT", "1,000", "100", "95", "5", "900", "10.0%"],
        ["CPT", "400", "400", "390", "10", "0", "100.0%"],
    ]


def test_all_reviewed_reports_rejections_without_claiming_human_approval():
    quality = report("all")
    quality["package_review"]["targets"].pop("sft")
    markup = package_review_report_html(quality)
    assert "全量评审" in markup
    assert "400 / 400" in markup
    assert "AI 评审已覆盖本次进入打包的全部样本" in markup
    assert "AI 隔离样本不进入训练文件" in markup
    assert "人工审核通过" not in markup
    assert TableRows(markup).rows[1] == ["CPT", "400", "400", "390", "10", "0", "100.0%"]


def test_incomplete_all_review_does_not_claim_full_coverage():
    markup = package_review_report_html(report("all"))
    assert "全量评审尚未覆盖所有样本" in markup
    assert "AI 评审已覆盖本次进入打包的全部样本" not in markup


@pytest.mark.parametrize("quality", [{}, {"package_review": {"enabled": False, "status": "disabled"}}])
def test_legacy_and_disabled_runs_add_no_empty_review_panel(quality):
    assert package_review_report_html(quality) == ""


def test_empty_review_is_not_shown_as_successful_census():
    markup = package_review_report_html({"package_review": {"enabled": True, "mode": "all", "targets": {}}})
    assert "本次没有可供 AI 评审的样本" in markup
    assert "AI 评审已覆盖本次进入打包的全部样本" not in markup


def test_untrusted_target_labels_are_escaped():
    quality = report()
    quality["package_review"]["targets"]["<img src=x>"] = quality["package_review"]["targets"].pop("sft")
    markup = package_review_report_html(quality)
    assert "<IMG" not in markup
    assert "&lt;IMG SRC=X&gt;" in markup


@pytest.mark.parametrize("page,status", [("quality", "completed"), ("package", "completed"),
                                         ("package", "needs_attention")])
def test_both_delivery_views_show_same_review_evidence_and_keep_export_available(page, status):
    quality = report()
    quality["targets"] = {"sft": {"total": 1000, "eligible": 995},
                          "cpt": {"total": 400, "eligible": 390}}
    script = f'''
from unittest.mock import patch
import streamlit as st
from lib.presentation.streamlit.workflow_quality_page import render_workflow_quality
from lib.presentation.streamlit.package_page import render_package_page
st.session_state['ws'] = 'fixture'
st.session_state['review_panels'] = []
quality = {quality!r}
manifest = dict(counts={{'sft':995, 'cpt':390}}, sha256={{}}, sources=[])
class Application:
    def list_runs(self):
        return [dict(id='ai-review-run', name='Fixture', status={status!r}, targets=['sft', 'cpt'])]
    def task_runs(self): return self.list_runs()
    def list_releases(self): return []
    def bundle_job(self, run_id): return None
    def state(self, run_id): return dict(targets=['sft', 'cpt'], stages={{}})
    def quality_report(self, run_id): return dict(manifest=manifest, quality=quality)
    def package_contents(self, run_id):
        return dict(manifest=manifest, quality=quality, files=[], bundle=None)
original_html = st.html
def capture(body, *args, **kwargs):
    if isinstance(body, str) and '<div class="df-ai-review">' in body:
        st.session_state['review_panels'].append(body)
    return original_html(body, *args, **kwargs)
with patch('streamlit.html', side_effect=capture):
    if {page!r} == 'quality':
        render_workflow_quality(Application(), 'fixture')
    else:
        render_package_page(Application())
'''
    ui = AppTest.from_string(script.encode("ascii", "backslashreplace").decode("ascii")).run()
    assert not ui.exception
    assert len(ui.session_state["review_panels"]) == 1
    assert "500 / 1,400" in ui.session_state["review_panels"][0]
    if page == "package":
        assert ui.button(key="prepare-package:ai-review-run")
        assert any("可直接下载自动质检数据包" in caption.value for caption in ui.caption)
        assert "人工审核（可选）" in "".join(item.proto.body for item in ui.get("html"))
        if status == "needs_attention":
            assert any("本次 AI 打包评审隔离了部分样本" in warning.value for warning in ui.warning)
