"""Verified report snapshots, streaming record indexes, and portable HTML.

Report indexes live outside ``artifacts``. They do not change publication,
training exports, scores, or the immutable manifest. A cold target is scanned
once with bounded record memory; subsequent pages use the existing JSONL seek
index. Live records use the existing bounded completion journal instead.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import tempfile
import time

from filelock import FileLock, Timeout

from lib.domain.workflow_delivery import has_deliverable_results, delivery_manifest_matches
from lib.domain.workflow_quality import canonical
from lib.domain.workflow_report import content_digest, report_sample
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.jsonl_preview import read_rows
from lib.infrastructure.workflow_store import file_hash, read_json, run_path
from lib.contexts.datasets.contracts import preferred_training_record
from lib.infrastructure.verified_preview import verified_snapshot, inventory_identity
from lib.infrastructure.workflow_live_artifacts import live_artifacts
from lib.infrastructure.workflow_report_jobs import REPORT_JOBS, ReportPreparationBusy
from lib.io_utils import atomic_json
from lib.presentation.file_sizes import format_file_size

INDEX_VERSION = 4
MAX_RECORD_CHARS = 20_000_000


def _identity(path):
    stat = path.stat()
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or stat.st_nlink > 1:
        raise ValueError("report_index_path_unsafe")
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=16)
def _verified_index(filename, identity, expected_hash, window):
    path = Path(filename)
    if file_hash(path) != expected_hash or _identity(path) != identity:
        raise ValueError("report_index_integrity_error")
    with path.open("rb") as handle:
        handle.seek(max(0, identity[2] - 256 * 1024))
        lines = handle.read().splitlines()
    footer = json.loads(lines[-1]) if lines else None
    if not isinstance(footer, dict) or set(footer) != {"_report_summary"}:
        raise ValueError("report_index_summary_invalid")
    return footer["_report_summary"]


def _valid_index(path, receipt):
    summary = _verified_index(str(path.resolve()), _identity(path), receipt["sha256"], int(time.monotonic() // 30))
    if summary != receipt.get("summary"):
        raise ValueError("report_index_summary_mismatch")


def _report_directory(run):
    for part in (run, run / "reports", run / "reports" / ".indexes"):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("report_index_path_unsafe")
    folder = run / "reports" / ".indexes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _build_index(run, target, manifest, source_identity, destination, signature):
    source = run / "artifacts" / f"{target}.records.json"
    training = run / "artifacts" / f"{target}.jsonl"
    recipe = read_json(run / "recipe.json")
    if content_digest(recipe) != manifest.get("recipe_hash"):
        raise ValueError("preview_file_changed")
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".report-", suffix=".pending")
    counts, reasons, review_counts, released_count = Counter(), Counter(), Counter(), 0
    fact_sum, fact_count, bound_independent, other_reasons = 0, 0, 0, 0
    digest = sha256()
    try:
        with os.fdopen(fd, "wb") as output, training.open("rb") as released:
            for position, row in enumerate(iter_json_records(source, max_record_chars=MAX_RECORD_CHARS)):
                status = row.get("status", "unknown")
                counts[status if status in {"eligible", "quarantined", "ready"} else "other"] += 1
                if row.get("reason"):
                    reason = str(row["reason"])
                    if len(reason) > 512:
                        reason = "reason_sha256:" + sha256(reason.encode("utf-8")).hexdigest()
                    if reason in reasons or len(reasons) < 100:
                        reasons[reason] += 1
                    else:
                        other_reasons += 1
                eligible = row.get("status") == "eligible"
                if eligible:
                    line = released.readline(MAX_RECORD_CHARS * 4 + 1)
                    if not line or len(line) > MAX_RECORD_CHARS * 4:
                        raise ValueError("report_training_record_mismatch")
                    body = json.loads(line)
                    expected = preferred_training_record(target, row, recipe.get("generation_preferences"),
                                                         sft_output_style=recipe.get("sft_output_style"))
                    if body != expected:
                        raise ValueError("report_training_record_mismatch")
                    released_count += 1
                else:
                    try:
                        body = preferred_training_record(target, row, recipe.get("generation_preferences"),
                                                         sft_output_style=recipe.get("sft_output_style"))
                    except (KeyError, TypeError, ValueError, IndexError):
                        body = None
                projected = report_sample(target, row, body, released=eligible, position=position)
                for kind, checks in projected["reviews"].items():
                    if checks:
                        review_counts[kind] += 1
                bound_independent += any(entry.get("bound_to_current_body") is True
                                         for entry in projected["reviews"]["independent"])
                check = row.get("judge") or {}
                score = check.get("correctness") if isinstance(check, dict) else None
                if type(score) in {int, float} and 1 <= score <= 5:
                    fact_sum += score
                    fact_count += 1
                encoded = (canonical(projected) + "\n").encode("utf-8")
                output.write(encoded)
                digest.update(encoded)
            if released.read(1) or released_count != manifest["counts"].get(target):
                raise ValueError("report_training_record_mismatch")
            summary = {"target": target, "total": sum(counts.values()), "eligible": released_count,
                       "quarantined": counts.get("quarantined", 0), "statuses": dict(counts),
                       "reasons": dict(reasons), "reviews": dict(review_counts),
                       "other_reason_count": other_reasons, "reason_categories_truncated": bool(other_reasons),
                       "quality": {"fact_scored": fact_count,
                                   "fact_correctness_mean": round(fact_sum / fact_count, 3) if fact_count else None,
                                   "score_scale": [1, 5], "independent_body_bound": bound_independent},
                       "counts_verified": True, "counts_source": "verified_records"}
            footer = (canonical({"_report_summary": summary}) + "\n").encode("utf-8")
            output.write(footer)
            digest.update(footer)
            output.flush()
            os.fsync(output.fileno())
        if inventory_identity(run) != source_identity:
            raise ValueError("preview_file_changed")
        Path(temporary).replace(destination)
        receipt = {"version": INDEX_VERSION, "signature": signature, "sha256": digest.hexdigest(),
                   "summary": summary}
        atomic_json(destination.with_suffix(".receipt.json"), receipt)
        return receipt
    finally:
        Path(temporary).unlink(missing_ok=True)


def _index_signature(target, manifest):
    hashes = manifest.get("sha256", {})
    if f"{target}.records.json" not in hashes or f"{target}.jsonl" not in hashes:
        raise ValueError("report_target_unavailable")
    return content_digest({"version": INDEX_VERSION, "records": hashes[f"{target}.records.json"],
                           "training": hashes[f"{target}.jsonl"], "recipe_hash": manifest.get("recipe_hash")})


def _prepare_record_index(run, target, manifest, source_identity, signature):
    folder = _report_directory(run)
    path = folder / f"{target}-{signature}.jsonl"
    receipt_path = path.with_suffix(".receipt.json")
    lock = FileLock(str(folder / f"{target}.lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise ReportPreparationBusy from None
    try:
        receipt = None
        if path.exists() and receipt_path.exists():
            try:
                if _identity(receipt_path)[2] > 256 * 1024:
                    raise ValueError("report_index_receipt_oversized")
                cached = read_json(receipt_path)
                if (cached.get("version") == INDEX_VERSION and cached.get("signature") == signature
                        and isinstance(cached.get("summary"), dict)):
                    _valid_index(path, cached)
                    receipt = cached
            except (OSError, ValueError, TypeError, KeyError):
                # The derived index may be discarded; formal artifacts never
                # are. Rebuild from the authenticated originals on cache damage.
                receipt = None
        if receipt is None:
            receipt = _build_index(run, target, manifest, source_identity, path, signature)
        _valid_index(path, receipt)
        if inventory_identity(run) != source_identity:
            raise ValueError("preview_file_changed")
        return {"path": str(path), "identity": _identity(path), "receipt": receipt}
    finally:
        lock.release()


def _preparation_page(run, target, offset, limit, preparation, manifest=None):
    return {"rows": [], "total": None, "offset": offset, "limit": limit, "has_next": False,
            "finalized": manifest is not None, "indexing": preparation["status"] != "failed",
            "source_kind": "report_preparation_failed" if preparation["status"] == "failed" else "report_index_pending",
            "run_id": run.name, "target": target,
            "manifest_sha256": content_digest(manifest) if manifest else None,
            "preparation": {key: preparation[key] for key in ("status", "phase", "error", "retryable")},
            "summary": {"target": target, "total": None,
                        "eligible": manifest["counts"].get(target) if manifest else None,
                        "quarantined": None, "counts_verified": False, "counts_source": "report_preparation"}}


def _record_page(run, target, manifest, source_identity, offset, limit):
    signature = _index_signature(target, manifest)
    key = ("index", str(run.resolve()), target, signature, source_identity)
    prepare = lambda: _prepare_record_index(run, target, manifest, source_identity, signature)
    job = REPORT_JOBS.request(key, prepare, phase="indexing")
    if job["status"] != "ready":
        return _preparation_page(run, target, offset, limit, job, manifest)
    result = job["result"]
    path, receipt = Path(result["path"]), result["receipt"]
    try:
        changed = _identity(path) != result["identity"]
    except (OSError, ValueError):
        changed = True
    if changed:
        REPORT_JOBS.invalidate(key)
        return _preparation_page(run, target, offset, limit,
                                 REPORT_JOBS.request(key, prepare, phase="indexing"), manifest)
    total = receipt["summary"]["total"]
    if 0 < offset < total:
        # JSONL's compact seek index itself can be cold or evicted. Build/read
        # it on a worker, never synchronously on a million-row jump in the UI.
        page_key = ("page", str(run.resolve()), target, signature, source_identity, result["identity"], offset, limit)
        def read_page():
            if _identity(path) != result["identity"]:
                raise ValueError("preview_file_changed")
            selected = read_rows(path, offset, min(limit, total - offset))
            if _identity(path) != result["identity"] or inventory_identity(run) != source_identity:
                raise ValueError("preview_file_changed")
            return selected
        page_job = REPORT_JOBS.request(page_key, read_page, phase="reading")
        if page_job["status"] != "ready":
            return _preparation_page(run, target, offset, limit, page_job, manifest)
        rows = page_job["result"]
    else:
        rows = read_rows(path, 0, min(limit, total)) if offset == 0 and total else []
    if _identity(path) != result["identity"] or inventory_identity(run) != source_identity:
        REPORT_JOBS.invalidate(key)
        raise ValueError("preview_file_changed")
    return {"rows": rows, "summary": deepcopy(receipt["summary"]), "total": total,
            "offset": offset, "limit": limit, "has_next": offset + len(rows) < total,
            "finalized": True, "indexing": False, "source_kind": "verified_artifacts", "run_id": run.name, "target": target,
            "manifest_sha256": content_digest(manifest)}


class FilesystemWorkflowReportDriver:
    def __init__(self, root: Path, output: Path):
        self.root, self.output = Path(root), Path(output)

    def _snapshot(self, run_id):
        run = run_path(self.output, run_id)
        if run.is_symlink() or getattr(run, "is_junction", lambda: False)():
            raise ValueError("report_run_path_unsafe")
        state, recipe = read_json(run / "state.json"), read_json(run / "recipe.json")
        if state.get("id") != run_id:
            raise ValueError("report_run_identity_mismatch")
        # The saved task state governs display. Probing an exclusive run lock
        # would make simultaneous read-only report requests mistake each other
        # for an active generation worker (and create a lock outside reports).
        active = state.get("status") in {"running", "queued"}
        manifest, identity, issue, preparation = None, None, None, None
        if has_deliverable_results(state) and not active:
            if (run / "artifacts" / "manifest.json").exists():
                try:
                    identity = inventory_identity(run)
                    context = content_digest({"recipe": recipe, "recipe_hash": state.get("recipe_hash"),
                                              "partial_export": (state.get("production") or {}).get("partial_export")})
                    key = ("verify", str(run.resolve()), None, context, identity)
                    def verify():
                        verified, actual_identity = verified_snapshot(run)
                        if (actual_identity != identity or verified.get("run_id") != run_id
                                or verified.get("recipe_hash") != state.get("recipe_hash")
                                or content_digest(recipe) != state.get("recipe_hash")
                                or not isinstance(verified.get("counts"), dict)
                                or set(verified["counts"]) != set(recipe.get("targets", []))
                                or any(type(count) is not int or count < 0 for count in verified["counts"].values())
                                or not delivery_manifest_matches(state, verified)):
                            raise ValueError("report_manifest_context_mismatch")
                        return verified
                    preparation = REPORT_JOBS.request(key, verify, phase="verification")
                    if preparation["status"] == "ready":
                        manifest = preparation["result"]
                        preparation = None
                    elif preparation["status"] == "failed":
                        issue = preparation["error"]
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    issue, manifest, identity = str(exc), None, None
            else:
                issue = "report_manifest_missing"
        return run, state, recipe, active, manifest, identity, issue, preparation

    def overview(self, run_id: str) -> dict:
        run, state, recipe, active, manifest, _, issue, preparation = self._snapshot(run_id)
        quality = state.get("quality") or {}
        target_summaries = quality.get("targets") or {}
        targets = []
        for target in recipe.get("targets", state.get("targets", [])):
            summary = target_summaries.get(target) or {}
            eligible = manifest["counts"].get(target, 0) if manifest else summary.get("eligible", 0)
            total = summary.get("total")
            targets.append({"target": target, "total": total, "eligible": eligible,
                            "quarantined": None, "reasons": deepcopy(summary.get("reasons", {})),
                            "counts_verified": False, "counts_source": "saved_task_summary",
                            "eligible_verified": manifest is not None})
        return {"run_id": run_id, "name": state.get("name", run_id), "status": state.get("status", "unknown"),
                "active": active, "created_at": state.get("created_at"), "updated_at": state.get("updated_at"),
                "artifact_state": "formal" if manifest else "invalid" if issue else "indexing" if preparation else "provisional",
                "artifact_error": issue, "finalized": manifest is not None,
                "indexing": bool(preparation and preparation["status"] != "failed"),
                "preparation": {key: preparation[key] for key in ("status", "phase", "error", "retryable")} if preparation else None,
                "partial_export": bool((manifest or {}).get("production", {}).get("partial_export")),
                "manifest": manifest, "manifest_sha256": content_digest(manifest) if manifest else None, "targets": targets,
                "sources": [{key: deepcopy(source[key]) for key in ("name", "sha256", "bytes") if key in source}
                            for source in recipe.get("sources", []) if isinstance(source, dict)],
                "stages": [{"stage": stage, **{key: deepcopy(metrics[key]) for key in (
                    "label", "status", "done", "total", "outputs", "phase", "error") if key in metrics}}
                           for stage, metrics in state.get("stages", {}).items() if isinstance(metrics, dict)],
                "models": deepcopy(state.get("models", {})), "usage": deepcopy(state.get("usage", {})),
                "quality": {key: deepcopy(quality[key]) for key in (
                    "policy", "human_review", "package_review", "review_repair") if key in quality},
                "preferences": deepcopy(recipe.get("generation_preferences")),
                "sft_output_style": recipe.get("sft_output_style"),
                "report_scope": "selected_page", "independent_review_inferred": False}

    def samples(self, run_id: str, target: str, offset: int = 0, limit: int = 12) -> dict:
        run, state, recipe, active, manifest, identity, issue, preparation = self._snapshot(run_id)
        if target not in recipe.get("targets", []):
            raise ValueError("report_target_unavailable")
        if preparation is not None:
            return _preparation_page(run, target, offset, limit, preparation)
        if issue:
            raise ValueError(issue)
        if manifest:
            return _record_page(run, target, manifest, identity, offset, limit)
        stages = state.get("stages", {})
        stage = target if target in stages else None
        snapshot = live_artifacts(run, state, stage=stage, limit=min(20, limit))
        # Completion-order tails are not a stable pageable catalogue. An offset
        # cannot pretend to seek a not-yet-sealed million-row training export.
        raw = snapshot["rows"] if offset == 0 else []
        rows = []
        for index, row in enumerate(raw):
            try:
                body = preferred_training_record(target, row, recipe.get("generation_preferences"),
                                                 sft_output_style=recipe.get("sft_output_style"))
            except (KeyError, ValueError, TypeError, IndexError):
                body = None
            rows.append(report_sample(target, row, body, released=False, position=index))
        descriptor = next((item for item in snapshot["stages"] if item["stage"] == snapshot["selected_stage"]), {})
        return {"run_id": run_id, "target": target, "rows": rows, "total": None, "offset": offset,
                "limit": min(20, limit), "has_next": False, "finalized": False, "indexing": False,
                "source_kind": snapshot["source_kind"], "selected_stage": snapshot["selected_stage"],
                "summary": {"target": target, "total": None, "eligible": descriptor.get("eligible", 0),
                            "quarantined": descriptor.get("quarantined", 0), "written": descriptor.get("written", 0),
                            "counts_verified": False, "counts_source": "live_stage_tail",
                            "indexing": descriptor.get("indexing", False)}}

    def html(self, run_id: str, target: str, offset: int = 0, limit: int = 12, language: str = "zh") -> str:
        page = self.samples(run_id, target, offset, limit)
        overview = self.overview(run_id)
        return self.html_snapshot(overview, page, language)

    def html_snapshot(self, overview: dict, page: dict, language: str = "zh") -> str:
        preparing = page.get("source_kind") in {"report_index_pending", "report_preparation_failed"}
        if (page["run_id"] != overview["run_id"] or (not preparing and page["finalized"] != overview["finalized"])
                or (not preparing and overview.get("artifact_state") == "invalid")
                or page["target"] not in {item["target"] for item in overview["targets"]}
                or (not preparing and page["finalized"] and page.get("manifest_sha256") != overview.get("manifest_sha256"))):
            raise ValueError("report_snapshot_changed")
        return render_report_html(overview, page, language=language)

    def retry(self, run_id, target=None):
        run = run_path(self.output, run_id)
        REPORT_JOBS.retry(str(run.resolve()), target)

    def html_embedded_snapshot(self, overview: dict, page: dict, language: str = "zh") -> str:
        document = self.html_snapshot(overview, page, language)
        return embed_report_html(document, overview, page, language=language)


def _escape(value):
    return html.escape(str(value), quote=True)


@lru_cache(maxsize=1)
def _report_stylesheet():
    return Path(__file__).with_name("workflow_report.css").read_text(encoding="utf-8")


def scope_report_stylesheet(css: str) -> str:
    """Scope every rule of the controlled flat stylesheet to one wrapper.

    This is deliberately not a general CSS parser: nested/at rules and multiple
    declaration blocks are rejected instead of emitting a global rule. Sample
    text never enters this stylesheet. Commas occur only between selectors in
    the existing selector grammar; functions appear in declarations.
    """
    scope = ".sj-workflow-report"
    rules = []
    for block in css.split("}"):
        if not block.strip():
            continue
        if block.count("{") != 1:
            raise ValueError("report_css_not_flat")
        header, declarations = block.split("{", 1)
        if "@" in header or not header.strip():
            raise ValueError("report_css_rule_unsupported")
        selectors = []
        for selector in header.strip().split(","):
            selector = selector.strip()
            if not selector:
                raise ValueError("report_css_selector_invalid")
            if selector == "*":
                selectors.extend((scope, scope + " *"))
            elif selector in {"body", "html", ":root"}:
                selectors.append(scope)
            else:
                selectors.append(scope + " " + selector)
        rules.append(",".join(selectors) + "{" + declarations + "}")
    return "\n".join(rules)


@lru_cache(maxsize=1)
def _embedded_stylesheet():
    return scope_report_stylesheet(_report_stylesheet())


def embed_report_html(document: str, overview: dict, page: dict, *, language: str = "zh") -> str:
    """Use the identical safe body in normal document flow, without an iframe."""
    body = document.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    identifier = f"sj-workflow-report-{overview['run_id']}-{page['target']}-{page['offset']}"
    return ('<style>' + _embedded_stylesheet() + '</style><section class="sj-workflow-report" data-user-content="true" id="'
            + _escape(identifier) + '" lang="' + _escape(language) + '" aria-label="'
            + _escape(_LABELS[language]["title"]) + '">' + body + '</section>')


def _prose(value):
    if not isinstance(value, str):
        return '<pre class="literal">' + _escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        return '<pre class="literal">' + _escape(value) + '</pre>'
    # Raw HTML is text. Images are text too, so untrusted samples never cause
    # remote requests. Links retain the parser's safe-scheme validation.
    md = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False}).enable("table").disable("image")
    return '<div class="prose">' + md.render(value) + '</div>'


_LABELS = {
    "zh": {"brand": "数简立方", "title": "样本报告", "subtitle": "真实工作记录 · 完整样本正文 · 可追溯的审核依据",
           "formal": "已校验正式产物", "provisional": "运行记录 · 尚未封存", "partial": "已封存的部分产物",
           "total": "候选样本", "eligible": "正式导出", "quarantined": "已隔离", "page": "本页展示",
           "question": "问题与公开上下文", "reasoning": "推理正文", "answer": "答案", "text": "语料正文",
           "messages": "完整对话", "chosen": "优选回答", "rejected": "对比回答", "responses": "偏好回答",
           "prompt": "问题与公开上下文", "tools": "工具定义", "criterion": "偏好维度",
           "response": "候选回答", "rank": "偏好序", "score": "AI 评分", "feedback": "评分依据",
           "node_passed": "节点通过",
           "fact": "事实与任务评审", "context": "可见上下文检查", "human": "人工审核", "style": "风格与格式检查",
           "package": "打包 AI 抽检", "independent": "独立复核记录", "verification": "执行核验",
           "audit": "当前审核依据", "provenance": "来源与当前回执（非训练正文）", "body": "完整训练字段",
           "empty": "尚无完整输出。已保存的问题与任务记录仍可查看。", "unreviewed": "未保存该项审核记录",
           "bound": "与当前正文哈希一致", "unbound": "未绑定当前正文，不能视为通过",
           "source": "资料来源", "stage": "工作流阶段", "status": "状态", "reason": "处理原因",
           "notice": "本报告保留真实模型评分。模型评审、上下文检查和人工审核各有范围，不能互相替代。",
           "scope": "此 HTML 只包含当前页完整样本。教师资料与审核元数据单独展示，不属于训练正文。",
           "live": "这里只显示最近完成的节点输出。未封存结果仍可能被后续审核拒绝，不是正式训练集。",
           "unknown": "尚未确定", "released": "已导出", "candidate": "未导出候选", "count_note": "概览来自已保存任务状态；当前类型的准确计数由校验后的样本索引提供。",
           "source_empty": "无文件来源（开放任务或认证返工）", "created": "创建", "updated": "更新",
           "eligible_status": "可导出", "quarantined_status": "已隔离", "ready_status": "已生成", "unknown_status": "未记录",
           "completed_status": "已完成", "needs_attention_status": "需要处理", "running_status": "运行中",
           "failed_status": "失败", "cancelled_status": "已取消", "pending_status": "等待中", "queued_status": "排队中",
           "preparing": "正在准备报告", "preparation_failed": "报告准备失败",
           "preparation_hint": "样本正在后台校验和整理。页面仍可操作，待准备完成后显示真实数量与完整正文。",
           "preparation_error_hint": "报告尚未就绪，当前没有可展示的已校验正文。可重试报告准备；这不会重新生成样本。"},
    "en": {"brand": "ShuJian Cube", "title": "Sample report", "subtitle": "Real work · Complete sample text · Traceable review evidence",
           "formal": "Verified formal artifacts", "provisional": "Live records · Not sealed", "partial": "Verified partial artifacts",
           "total": "Candidates", "eligible": "Formally exported", "quarantined": "Quarantined", "page": "On this page",
           "question": "Question and visible context", "reasoning": "Reasoning text", "answer": "Answer", "text": "Corpus text",
           "messages": "Full conversation", "chosen": "Preferred response", "rejected": "Comparison response", "responses": "Preference responses",
           "prompt": "Question and visible context", "tools": "Tool definitions", "criterion": "Preference criterion",
           "response": "Candidate response", "rank": "Preference rank", "score": "AI score", "feedback": "Score evidence",
           "node_passed": "Node passed",
           "fact": "Facts and task review", "context": "Visible context check", "human": "Human review", "style": "Style and format checks",
           "package": "Package AI sampling", "independent": "Independent review record", "verification": "Execution verification",
           "audit": "Current review evidence", "provenance": "Sources and current receipts (outside training text)", "body": "Complete training fields",
           "empty": "No complete output yet. The saved question and task record remain available.", "unreviewed": "No saved review record",
           "bound": "Matches the current body hash", "unbound": "Not bound to this body; not a verified pass",
           "source": "Source files", "stage": "Workflow stages", "status": "Status", "reason": "Processing reason",
           "notice": "Actual model scores are preserved. Fact review, context checks, and human review have separate scopes.",
           "scope": "This HTML contains complete samples on the selected page. Teacher material and review metadata are separate from training text.",
           "live": "Only recent node outputs are shown. Later checks may reject these unsealed candidates. This is not a formal training dataset.",
           "unknown": "Not known yet", "released": "Exported", "candidate": "Unreleased candidate", "count_note": "Overview counts come from saved task state. The verified sample index gives exact counts for the selected type.",
           "source_empty": "No source files (open task or authenticated rework)", "created": "Created", "updated": "Updated",
           "eligible_status": "Eligible", "quarantined_status": "Quarantined", "ready_status": "Generated", "unknown_status": "Not recorded",
           "completed_status": "Completed", "needs_attention_status": "Needs attention", "running_status": "Running",
           "failed_status": "Failed", "cancelled_status": "Cancelled", "pending_status": "Pending", "queued_status": "Queued",
           "preparing": "Preparing report", "preparation_failed": "Report preparation failed",
           "preparation_hint": "Samples are being checked and indexed in the background. The page stays usable. Exact counts and full text appear when ready.",
           "preparation_error_hint": "The report is not ready. No verified sample text is shown. Retry report preparation without generating samples again."},
}


def _status(value, labels):
    return labels.get(str(value) + "_status", str(value))


def _body_html(body, labels):
    parts = []
    for field, value in body.items():
        label = labels.get(field, field)
        if field == "responses" and isinstance(value, list):
            responses = []
            for candidate in value:
                if not isinstance(candidate, dict):
                    responses.append(_prose(candidate))
                    continue
                response = candidate.get("response", "")
                badges = ''.join('<span class="badge">' + _escape(labels[label]) + ' ' + _escape(candidate[key]) + '</span>'
                                 for key, label in (("preference_rank", "rank"), ("score", "score")) if key in candidate)
                rendered = (_body_html({"messages": response}, labels) if isinstance(response, list) else _prose(response))
                if candidate.get("feedback"):
                    rendered += '<h4>' + _escape(labels["feedback"]) + '</h4>' + _prose(candidate["feedback"])
                extra = {key: val for key, val in candidate.items() if key not in {"response", "preference_rank", "score", "feedback"}}
                if extra:
                    rendered += _prose(extra)
                responses.append('<section class="response"><h4>' + _escape(labels["response"]) + '</h4>' + badges + rendered + '</section>')
            rendered = ''.join(responses)
        elif field in {"messages", "question", "prompt", "chosen", "rejected"} and isinstance(value, list):
            turns = []
            for message in value:
                if not isinstance(message, dict):
                    turns.append(_prose(message))
                    continue
                role = message.get("role", "unknown")
                content = _prose(message.get("content", ""))
                for key in ("reasoning_content", "reasoning", "thinking"):
                    if message.get(key):
                        content += '<div class="message-reasoning"><h4>' + _escape(labels["reasoning"]) + '</h4>' + _prose(message[key]) + '</div>'
                extra = {key: val for key, val in message.items() if key not in {"role", "content", "reasoning_content", "reasoning", "thinking"}}
                if extra:
                    content += _prose(extra)
                turns.append('<div class="turn"><span class="role">' + _escape(role) + '</span><div>' + content + '</div></div>')
            rendered = ''.join(turns)
        else:
            rendered = _prose(value)
        parts.append('<section class="field field-' + _escape(field) + '"><h4>' + _escape(label) + '</h4>' + rendered + '</section>')
    return ''.join(parts)


def _imported_reasoning_notice(provenance, language):
    """Explain per-turn import treatment without promoting it to an assessment."""
    receipt = provenance.get("conversation_reasoning")
    if not isinstance(receipt, dict):
        return ""
    english = language == "en"
    original = receipt.get("existing_turns", 0)
    synthetic = receipt.get("synthesized_turns", 0)
    missing = receipt.get("remaining_missing_turns", receipt.get("missing_turns", 0))
    summary = (f"Imported reasoning · {original} original · {synthetic} synthesized · {missing} missing"
               if english else f"导入推理 · 原有 {original} 轮 · 合成补全 {synthetic} 轮 · 仍缺失 {missing} 轮")
    scope = ("This receipt describes the input. A later node may have rewritten the output."
             if english else "此收据描述导入阶段；后续节点可能已改写输出。") if provenance.get("conversation_reasoning_scope") == "input_only" else (
             "The input messages were retained. This is not a quality verdict."
             if english else "导入消息已保留；该记录不代表质量审核通过。")
    note = ("Synthesized explanations are not recovered original thoughts."
            if english else "补全内容是合成解释，并非恢复原模型的真实思考。")
    origins = ({"source": "Original", "synthetic_explanation": "Synthesized", "missing": "Missing"}
               if english else {"source": "原有推理", "synthetic_explanation": "合成补全", "missing": "缺失推理"})
    turns = []
    for turn in receipt.get("turns", []):
        index = turn.get("message_index")
        if type(index) is not int:
            continue
        label = origins.get(turn.get("origin"), "Unknown" if english else "未知来源")
        position = f"Assistant at message {index + 1}" if english else f"消息 {index + 1} 的助手发言"
        turns.append('<li>' + _escape(position) + ' · ' + _escape(label) + '</li>')
    details = ('<details class="metadata"><summary>' +
               ("Per-turn input provenance" if english else "逐条助手发言的输入来源") +
               '</summary><ul>' + ''.join(turns) + '</ul></details>') if turns else ''
    return ('<div class="notice"><strong>' + _escape(summary) + '</strong><p>' +
            _escape(scope) + (' ' + _escape(note) if synthetic else '') + '</p>' + details + '</div>')


def _reading_title(row, labels):
    body = row.get("training_body") or row.get("record") or {}
    text = body.get("text")
    if not isinstance(text, str):
        text = body.get("question") or body.get("prompt") or body.get("messages")
    if isinstance(text, list):
        text = next((message.get("content") for message in text
                     if isinstance(message, dict) and message.get("role") == "user"), None)
    if isinstance(text, list):
        text = " ".join(part.get("text", "") for part in text
                        if isinstance(part, dict) and part.get("type") == "text")
    if not isinstance(text, str) or not text.strip():
        return labels["title"]
    if not isinstance(body.get("text"), str):
        # Older exports put visible context and the real utterance in the same
        # user message. Use its literal utterance for the reading title only.
        for marker in ("\n用户消息：", "\nUser message:"):
            if marker in text:
                text = text.rsplit(marker, 1)[1]
    line = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    return line[:88] + ("…" if len(line) > 88 else "")


def render_report_html(overview: dict, page: dict, *, language: str = "zh") -> str:
    """Self-contained HTML; no remote assets, executable sample HTML, or fetches."""
    labels = _LABELS[language]
    css = _report_stylesheet()
    summary = page.get("summary", {})
    total = page.get("total")
    def number(value):
        return f"{value:,}" if type(value) is int else labels["unknown"]
    formal = overview.get("finalized") and page.get("finalized")
    preparing = page.get("source_kind") in {"report_index_pending", "report_preparation_failed"}
    preparation_failed = preparing and page.get("preparation", {}).get("status") == "failed"
    cards = []
    for row in page["rows"]:
        body = row.get("training_body")
        content = _body_html(body, labels) if isinstance(body, dict) else (
            '<p class="empty">' + _escape(labels["empty"]) + '</p>' + _body_html(
                {key: value for key, value in row["record"].items() if key in {"question", "messages", "prompt"}}, labels))
        checks = []
        transfer = row.get("style_transfer")
        if isinstance(transfer, dict) and isinstance(transfer.get("original"), dict):
            original = transfer["original"]
            title = ("风格改写前的原稿 · 对照用，不代表事实已核验" if language == "zh" else
                     "Original before style transfer · Comparison only, not verified facts")
            content += ('<details class="metadata"><summary>' + _escape(title) + '</summary>'
                + '<h4>' + _escape("原推理" if language == "zh" else "Original reasoning") + '</h4><pre>'
                + _escape(original.get("reasoning", "")) + '</pre><h4>'
                + _escape("程序保留的原答案" if language == "zh" else "Original answer retained by the program")
                + '</h4><pre>' + _escape(original.get("answer", "")) + '</pre></details>')
        for kind, evidence in row["reviews"].items():
            if not evidence:
                continue
            entries = []
            for entry in evidence:
                binding = ''
                if "bound_to_current_body" in entry:
                    binding = '<p class="binding">' + _escape(labels["bound"] if entry["bound_to_current_body"] else labels["unbound"]) + '</p>'
                entries.append(binding + '<pre class="audit-data">' + _escape(json.dumps(entry["evidence"], ensure_ascii=False, indent=2)) + '</pre>')
            checks.append('<details class="check"><summary>' + _escape(labels[kind]) + '</summary>' + ''.join(entries) + '</details>')
        audits = ''.join(checks) or '<p class="muted">' + _escape(labels["unreviewed"]) + '</p>'
        badge = labels["released"] if formal and row.get("released") else labels["candidate"]
        title = _reading_title(row, labels)
        record_id = str(row.get("id") or "")
        state_badge = ("" if formal and row.get("released") and row["status"] == "eligible" else '<span class="badge">' + _escape(labels["node_passed"] if row["status"] == "eligible" else _status(row["status"], labels)) + '</span>')
        reason = ('<div class="reason"><b>' + _escape(labels["reason"]) + '</b> ' + _escape(row["reason"]) + '</div>') if row.get("reason") else ''
        reason += _imported_reasoning_notice(row.get("provenance", {}), language)
        review_policy = row.get("provenance", {}).get("cot_review_policy", {})
        if review_policy.get("semantic_status") == "not_assessed":
            reason += '<div class="notice">' + _escape(
                "Not selected for CoT content review. Local checks only at this node; eligibility is not a semantic pass. See any later reviews below."
                if language == "en" else
                "本条未抽中 CoT 内容核验，此节点仅做本地检查；可导出不代表语义评审通过。后续审核证据见下方。") + '</div>'
        cards.append('<article class="sample"><header><div><span class="number">' + _escape(str(row["position"] + 1).zfill(2)) + '</span><div><h3>' + _escape(title) + '</h3><small class="sample-id" title="' + _escape(record_id) + '">' + _escape(record_id[:12]) + '</small></div></div><div class="badges"><span class="badge">' + _escape(page["target"].upper()) + '</span><span class="badge ' + ('good' if formal and row.get("released") else 'warn') + '">' + _escape(badge) + '</span>' + state_badge + '</div></header><div class="sample-body">' + reason + content + '<div class="review-block"><h4>' + _escape(labels["audit"]) + '</h4>' + audits + '</div><details class="metadata"><summary>' + _escape(labels["provenance"]) + '</summary><pre>' + _escape(json.dumps(row["provenance"], ensure_ascii=False, indent=2)) + '</pre></details>' + ('<details class="metadata"><summary>' + _escape(labels["body"]) + ' · SHA256 ' + _escape(row["training_body_sha256"][:12]) + '</summary><code class="body-hash">' + _escape(row["training_body_sha256"]) + '</code><pre>' + _escape(json.dumps(body, ensure_ascii=False, indent=2)) + '</pre></details>' if body is not None else '') + '</div></article>')
    metrics = ''.join('<div class="metric"><span>' + _escape(labels[key]) + '</span><strong>'
        + _escape(("≥ " if key == "quarantined" and not formal and summary.get("indexing")
                   and type(value) is int else "") + number(value)) + '</strong></div>' for key, value in (
        ("total", total), ("eligible", summary.get("eligible") if formal else None),
        ("quarantined", summary.get("quarantined")), ("page", None if preparing else len(page["rows"]))))
    sources = ''.join('<li><b>' + _escape(item.get("name", "")) + '</b><span>' + _escape(format_file_size(item.get("bytes"))) + '</span></li>' for item in overview.get("sources", []))
    stages = ''.join('<li><b>' + _escape(item["stage"]) + '</b><span>' + _escape(_status(item.get("status", "unknown"), labels)) + ' · ' + _escape(number(item.get("done"))) + '/' + _escape(number(item.get("total"))) + '</span></li>' for item in overview.get("stages", []))
    artifact_label = labels["preparation_failed"] if preparation_failed else labels["preparing"] if preparing else labels["partial"] if formal and overview.get("partial_export") else labels["formal"] if formal else labels["provisional"]
    notice = labels["preparation_error_hint"] if preparation_failed else labels["preparation_hint"] if preparing else labels["notice"] if formal else labels["live"]
    if preparation_failed:
        notice += " " + str(page.get("preparation", {}).get("error") or "")
    start = page["offset"] + 1 if page["rows"] else 0
    end = page["offset"] + len(page["rows"])
    return ('<!doctype html><html lang="' + language + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src \'none\'; base-uri \'none\'; form-action \'none\'"><title>' + _escape(labels["brand"] + ' · ' + labels["title"]) + '</title><style>' + css + '</style></head><body><nav><b>' + _escape(labels["brand"]) + '</b><span>' + _escape(artifact_label) + '</span></nav><main><section class="hero"><div><p class="eyebrow">SAMPLE STUDIO / ' + _escape(page["target"].upper()) + '</p><h1>' + _escape(labels["title"]) + '</h1><p class="subtitle">' + _escape(overview["name"]) + '</p><p class="muted">' + _escape(labels["subtitle"]) + '</p></div><div class="hero-cube" aria-hidden="true"><i></i><i></i><i></i></div></section><div class="identity"><code>' + _escape(overview["run_id"]) + '</code><span>' + _escape(_status(overview["status"], labels)) + '</span><span>' + _escape(labels["updated"] + ': ' + str(overview.get("updated_at") or '')) + '</span></div><div class="metrics">' + metrics + '</div><div class="notice">' + _escape(notice) + '</div><div class="overview"><details><summary>' + _escape(labels["source"]) + ' · ' + str(len(overview.get("sources", []))) + '</summary><ul>' + (sources or '<li>' + _escape(labels["source_empty"]) + '</li>') + '</ul></details><details><summary>' + _escape(labels["stage"]) + '</summary><ul>' + stages + '</ul></details></div><div class="section-heading"><h2>' + _escape(labels["title"]) + '</h2><span>' + _escape(f'{start}–{end} / {number(total)}') + '</span></div>' + ''.join(cards) + '<footer>' + _escape(labels["scope"]) + '</footer></main></body></html>')
