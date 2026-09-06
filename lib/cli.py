"""df-* 命令行（M1 最小闭环，纯 thin CLI，无界面代码）。

用法（在项目根，.venv 已激活）：
  python -m lib.cli import     [--limit N] [--export-limit M] [--cot separated]
  python -m lib.cli stats
  python -m lib.cli preview    [--n 10] [--file data/output/rollout_samples.jsonl]
  python -m lib.cli export     [--format llamafactory|chat] [--input ...] [--out ...]
  python -m lib.cli gate       [propose G0|G1|G3 | approve G0 | reject G0 | status]

闸门接入：import→G1；export --bulk→G3；df-run/df-distill→G0（M1 后续接入）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 本地端点绕代理（spike 报告 F2）；云端 API 不受影响
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

from lib.gates import GateBlocked, GateKeeper  # noqa: E402
from lib import workspace as WS  # noqa: E402

GATES_YAML = ROOT / "configs" / "gates.yaml"
GATES_STATE = ROOT / "data" / "output" / "gates_state.json"
OUT_DIR = ROOT / "data" / "output"
# 兼容占位：实际数据源路径见 configs/pipelines/rollout.yaml（或环境变量 ROLLOUT_DIR）
ROLLOUT_DIR = pathlib.Path(r"C:\Users\tianx\.zcode\cli\rollout")


def _gates() -> GateKeeper:
    return GateKeeper(GATES_YAML, GATES_STATE)


def _client(args, judge: bool = False, role: str = "generation"):
    """按 CLI 参数加载后端：--backend/--model/--base-url 显式优先，
    否则按角色槽位（model_roles）解析；judge=True 等价 role='judge'。"""
    from lib.llm_client import load_backend

    return load_backend(
        ROOT,
        backend=getattr(args, "backend", None) or None,
        model=getattr(args, "model", None) or None,
        base_url=getattr(args, "base_url", None) or None,
        judge=judge,
        role=role,
    )


def _pipeline(name: str):
    from lib.pipeline_config import load_pipeline_config

    return load_pipeline_config(name, ROOT)


def cmd_import(args) -> int:
    gate = _gates()
    if gate.status("G1") != "approved":
        gate.propose("G1", {"rollout_dir": str(ROLLOUT_DIR)})
        raise GateBlocked("数据源闸 G1 待确认：df gate approve G1（确认导入私有会话数据与云端上传）")
    sys.path.insert(0, str(ROOT / "scripts"))
    from import_rollout import run  # type: ignore

    run(limit=args.limit, export_limit=args.export_limit, cot=args.cot, out_dir=OUT_DIR)
    from lib.monitor import trace_run

    stats = json.loads((OUT_DIR / "rollout_stats.json").read_text(encoding="utf-8"))
    trace_run(ROOT, "import", {"cot": args.cot, "stats": stats})
    return 0


def cmd_stats(args) -> int:
    stats = json.loads((OUT_DIR / "rollout_stats.json").read_text(encoding="utf-8"))
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


def cmd_preview(args) -> int:
    path = pathlib.Path(args.file or (OUT_DIR / "rollout_samples.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    samples = [json.loads(l) for l in lines if l.strip()]

    if args.html:
        report = None
        report_path = OUT_DIR / "distill_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        from lib.render import render_preview_html

        out = render_preview_html(samples, report, OUT_DIR / "preview.html", max_samples=args.n)
        print(f"HTML 预览已生成（{min(args.n, len(samples))} 条）: {out}")
        return 0

    for sample in samples[: args.n]:
        last = sample["messages"][-1]
        content = str(last.get("content", ""))[:200]
        reasoning = str(last.get("reasoning_content", ""))[:120]
        print(f"[{sample['id'][:20]}] model={sample['model']} fin={sample['finish_reason']} "
              f"msgs={len(sample['messages'])} toolErr={sample['error_tool_steps']}")
        if reasoning:
            print(f"    思考: {reasoning}…" if len(reasoning) == 120 else f"    思考: {reasoning}")
        print(f"    正文: {content}")
        print()
    print(f"（共 {len(samples)} 条，展示前 {min(args.n, len(samples))} 条）")
    return 0


def cmd_export(args) -> int:
    from lib.quality import read_samples, export_release
    from lib.review import pull_decisions
    from lib.monitor import trace_run

    source = pathlib.Path(args.input) if args.input else OUT_DIR / "rollout_samples.jsonl"
    samples = read_samples(source)
    if args.bulk:
        _gates().require("G3")
    parent = pathlib.Path(args.out) if args.out else OUT_DIR / "export"
    if parent.suffix == ".jsonl":
        parent = parent.parent
    decisions = pull_decisions(dataset_name=WS.dataset_name())
    # Unreviewed DPO/corpus sidecars are draft-only; bulk approval applies to this SFT input.
    destination, counts = export_release(samples, args.format, parent, decisions,
        corpus_path=None if args.bulk else OUT_DIR / "corpus" / "docs.jsonl",
        dpo_path=None if args.bulk else OUT_DIR / "dpo_pairs.jsonl", tag=args.tag, bulk=args.bulk)
    trace_run(ROOT, "export", {"format": args.format, "counts": counts, "bulk": args.bulk, "path": str(destination)})
    print(f"导出完成: {counts} → {destination}")
    return 0


def cmd_distill(args) -> int:
    """蒸馏质检：分类（免费）→ DPO 负样本提取（免费）→ 可选 LLM 打分（需 G0 闸门）。"""
    import lib.adapters.distill as distill_mod
    from lib.prompts import get, render

    samples = [
        json.loads(l)
        for l in pathlib.Path(OUT_DIR / "rollout_samples.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    report = distill_mod.classify_report(samples)
    report["n_samples"] = len(samples)

    pairs = []
    _rcfg = _pipeline("rollout")["rollout"]
    rollout_dir = pathlib.Path(os.environ.get("ROLLOUT_DIR", _rcfg["dir"]))
    for f in sorted(rollout_dir.glob(_rcfg["pattern"])):
        pairs.extend(distill_mod.extract_dpo_pairs(str(f), "separated"))
    report["n_dpo_pairs"] = len(pairs)

    llm_scores = []
    check_n = args.llm_check if args.llm_check is not None else _pipeline("distill")["distill"]["llm_check_n"]
    if check_n > 0:
        _gates().require("G0")  # 调用云端 API 前必须过预算/模型闸
        client, model = _client(args, judge=True)  # judge 角色：更稳的模型（默认 v4-pro）
        candidates = sorted(
            samples,
            key=lambda s: (distill_mod.classify_sample(s)["tag"] != "recovery", -s["error_tool_steps"]),
        )[: check_n]
        print(f"LLM 打分 {len(candidates)} 条（模型 {model}）…")
        for s in candidates:
            last = s["messages"][-1]
            goal = next((m["content"] for m in reversed(s["messages"]) if m["role"] == "user"), "")
            try:
                out = client.chat(
                    [{"role": "user", "content": render(get("distill.summarizer"),
                        goal=goal[:800],
                        thinking=str(last.get("reasoning_content", ""))[:1500],
                        final_answer=str(last.get("content", ""))[:1500],
                    )}],
                    max_tokens=None, temperature=0.2, thinking=False,  # 严格 JSON：禁用思考
                )
                llm_scores.append({"id": s["id"], "score": out})
            except Exception as e:  # noqa: BLE001
                llm_scores.append({"id": s["id"], "error": str(e)[:200]})
        report["llm_scores"] = llm_scores
        report["llm_usage"] = client.usage

    # 产物落盘
    (OUT_DIR / "distill_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if pairs:
        (OUT_DIR / "dpo_pairs.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8"
        )
    from lib.monitor import trace_run

    trace_run(ROOT, "distill", {"report": report})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"报告/DPO 对 → {OUT_DIR}")
    return 0


def cmd_review(args) -> int:
    """审核中心人工审核（单进程融合）：push（推送+评分建议）/ pull（拉回标注+按通过率放行 G3）/ summary。"""
    import lib.review as review_mod

    gate = _gates()
    if args.action == "push":
        samples = [
            json.loads(l)
            for l in pathlib.Path(OUT_DIR / "rollout_samples.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ][: args.n]
        report_path = OUT_DIR / "distill_report.json"
        scores = {}
        if report_path.exists():
            scores = {s.get("id"): s.get("score", "") for s in json.loads(report_path.read_text(encoding="utf-8")).get("llm_scores", [])}
        dataset = WS.dataset_name(getattr(args, "ws", None))
        n = review_mod.push_samples(samples, scores, dataset_name=dataset)
        print(f"已推送 {n} 条到审核中心（数据集 {dataset}，控制台「人工审核」页过目）")
        print("标注完 keep/reject 后运行: df review pull")
        return 0

    if args.action == "pull":
        decisions = review_mod.pull_decisions(dataset_name=WS.dataset_name(getattr(args, "ws", None)))
        review_mod.write_review_log(decisions, OUT_DIR / "review.jsonl")
        rcfg = _pipeline("review")["review"]
        result = review_mod.decide_gate(
            decisions, threshold=rcfg["pass_threshold"], minimum=rcfg["min_reviewed"]
        )
        print(json.dumps(result, ensure_ascii=False, indent=1))
        print("汇总不自动放行；G3 需人工确认，bulk 导出还会校验当前样本的审核覆盖率。")
        return 0

    if args.action == "app":
        return _launch_console()

    if args.action == "summary":
        decisions = review_mod.pull_decisions(dataset_name=WS.dataset_name())
        print(json.dumps(review_mod.decide_gate(decisions), ensure_ascii=False, indent=1))
        return 0
    return 1


def cmd_monitor(args) -> int:
    """监控摘要：本地 runs.jsonl（Langfuse 未配置时的兜底审计）。"""
    runs_path = OUT_DIR / "runs.jsonl"
    if not runs_path.exists():
        print("暂无运行记录（执行 df import / df distill / df export 后生成）")
        return 0
    runs = [json.loads(l) for l in runs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"本地运行审计 {len(runs)} 条（最近 {min(len(runs), args.n)} 条）：")
    for r in runs[-args.n :]:
        usage = r.get("usage") or {}
        extra = ""
        if usage:
            extra = f" calls={usage.get('calls', '')} prompt={usage.get('prompt_tokens', '')} completion={usage.get('completion_tokens', '')}"
        print(f"  [{str(r.get('at', ''))[11:19]}] {r.get('kind', '')}{extra}")
    print("图形化查看: df review app → 侧边栏选「监控」")
    return 0


def cmd_prompt_eval(args) -> int:
    """提示词真机评测：全部用例跑 judge 模型 + 结构检查，写报告。支持 --ids 过滤与自定义用例文件。"""
    _gates().require("G0")
    from lib.prompt_eval import load_cases, run_all

    client, model = _client(args, judge=True)
    cases = load_cases(args.cases_file) if args.cases_file else None
    ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
    report = run_all(client, cases, ids)
    print(f"评测模型 {model}，共 {len(report)} 个用例…")
    for r in report:
        mark = "✔" if r["passed"] else "✘"
        failed = [c["name"] for c in r["checks"] if not c["ok"]]
        print(f" {mark} {r['case']}" + (f"（未过: {', '.join(failed)}）" if failed else ""))
    (OUT_DIR / "prompt_eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    passed = sum(1 for r in report if r["passed"])
    print(f"通过 {passed}/{len(report)}；用量 {client.usage}")
    print(f"报告 → {OUT_DIR / 'prompt_eval_report.json'}（含输出样例，供人工抽检语义质量）")
    return 0


def cmd_translate(args) -> int:
    """翻译管线：互译 + 回译校验（提示词驱动，需 G0 闸门）。参数来自 configs/pipelines/translation.yaml。"""
    _gates().require("G0")
    from lib.translator import run_translation

    cfg = _pipeline("translation")
    client, model = _client(args, role="translation")
    lines = pathlib.Path(args.input).read_text(encoding="utf-8").splitlines()
    print(f"翻译管线（模型 {model}）：{len(lines)} 行 → 取 {min(args.limit, len(lines))} 条")
    pairs = run_translation(
        client, lines, args.limit,
        faithful_threshold=cfg["translation"]["faithful_threshold"],
        temperature=cfg["translation"]["temperature"],
    )
    out = OUT_DIR / "translation_pairs.jsonl"
    out.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8"
    )
    kept = [p for p in pairs if p.get("keep")]
    for p in pairs:
        if "error" in p:
            print(f" ✘ {p['source'][:40]} → 失败: {p['error'][:60]}")
        else:
            mark = "✔" if p["keep"] else "✘"
            print(f" {mark} [{p['source_lang']}] {p['source'][:36]} → {p['target'][:36]} (回译忠实度 {p['score']})")
    print(f"保留 {len(kept)}/{len(pairs)}（score≥4）；用量 {client.usage}")
    print(f"平行语料 → {out}")
    return 0


def cmd_identity_gen(args) -> int:
    """身份问答零参考训练集：多样化"你是谁"问题 + 固定事实回答 + 事实校验（G0 闸门）。"""
    _gates().require("G0")
    from lib.identity_gen import load_config, run
    from lib.length import load_profiles, truncate_to_max

    client, model = _client(args)
    cfg = load_config(args.config)
    manifest = pathlib.Path(cfg['dedup_file'])
    if not manifest.is_absolute():
        cfg['dedup_file'] = str(OUT_DIR / manifest.name)
    # 上限守卫：仅截断保护（不做目标长度注入——长靠任务性质，不靠注水）
    max_tokens = args.max_answer_tokens
    if max_tokens is None:
        profiles = load_profiles(ROOT / "configs" / "pipelines" / "length_profiles.yaml")
        max_tokens = int(profiles.get("max_context", {}).get("answer_tokens", 0) or 0)
    print(f"身份问答生成（模型 {model}，公司={cfg['company']} 模型={cfg['model_name']}，"
          f"目标 {cfg['n_questions']} 条，回答上限={max_tokens or '不限'} tokens）…")
    result = run(client, cfg, answer_cap=max_tokens)
    stats = result["stats"]
    out = OUT_DIR / "identity_samples.jsonl"
    out.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in result["samples"]) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    if result["rejected"]:
        print(f"被事实校验驳回 {len(result['rejected'])} 条（见 samples 缺省）")
    print(f"样本 → {out}；开篇多样性 {stats['opening_diversity']}（1.0=无重复开篇）")
    return 0


def cmd_doc2corpus(args) -> int:
    """文档 → CPT 语料（知识注入层，零 LLM 成本，无闸门）。参数来自 configs/pipelines/rollout 同级的默认。"""
    import lib.doc2corpus as d2c

    cfg = _pipeline("doc2corpus")["doc2corpus"] if "doc2corpus" in _pipeline("doc2corpus") else {}
    path = pathlib.Path(args.input)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix.lower() in d2c.SUPPORTED_EXTS)
    target = pathlib.Path(args.out or (OUT_DIR / "corpus" / "docs.jsonl"))
    if not files:
        raise ValueError(f"没有可导入的文档：{path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    import uuid
    out = target.with_name('.pending-' + uuid.uuid4().hex + '.jsonl')
    chunk_size = args.chunk_size or cfg.get("chunk_size", 2000)
    overlap = args.overlap if args.overlap is not None else cfg.get("overlap", 0)
    manifest: set[str] = set()
    total_kept = 0
    failed = []
    for f in files:
        try:
            result = d2c.doc_to_corpus(f, target_chars=chunk_size, overlap=overlap, manifest=manifest)
        except Exception as e:  # noqa: BLE001 —— 单文件失败不阻断
            print(f" ✘ {f.name}: {str(e)[:120]}")
            failed.append(str(f))
            continue
        total_kept += d2c.write_corpus_jsonl(result["entries"], out)
        s = result["stats"]
        print(f" ✔ {s['file']}: {s['chars']} 字符 → {s['kept']} 块（去重 {s['dups']}）")
    if failed or not total_kept:
        out.unlink(missing_ok=True)
        raise ValueError(f"导入未完成：{len(failed)} 个文件失败；旧语料未覆盖")
    if target.exists():
        import shutil
        shutil.copy2(target, target.with_name(target.name + '.' + uuid.uuid4().hex + '.bak'))
    os.replace(out, target)
    print(f"共 {total_kept} 块 → {target}")
    print("语料草稿已生成；导出前仍需检查原文质量。")
    return 0


def cmd_doc2data(args) -> int:
    """文档 → 问答 SFT（表达层，含事实依据校验；需 G0 闸门）。参数来自 configs/pipelines/doc2data.yaml。"""
    _gates().require("G0")
    import lib.doc2data as d2d

    cfg = _pipeline("doc2data")["doc2data"]
    client, model = _client(args)
    result = d2d.doc_to_samples(
        client, args.input,
        qa_per_chunk=args.qa_per_chunk or cfg["qa_per_chunk"],
        max_chunks=args.max_chunks or cfg["max_chunks"],
        chunk_size=args.chunk_size or cfg["chunk_size"],
        mode=args.mode,
    )
    stats = result["stats"]
    out = OUT_DIR / "doc_samples.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for s in result["samples"]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"模型 {model}；文档 {args.input}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"样本 → {out}；事实校验驳回率 {stats['ground_reject_rate']}（防幻觉质量门）")
    for r in result["rejected"][:3]:
        print(f" 驳回示例: {r.get('question', r.get('chunk', ''))} → {r.get('unsupported', r.get('error', ''))}")
    return 0


def cmd_cot_style(args) -> int:
    """CoT 风格偏好调教：风格画像→软采样注入→风格校验→SFT 样本+风格 DPO 对（G0 闸门）。"""
    _gates().require("G0")
    import lib.cot_style as cot
    import yaml

    client, model = _client(args)
    styles_cfg = cot.load_styles(ROOT / "configs" / "cot_styles.yaml", args.profile)
    tasks_raw = yaml.safe_load(pathlib.Path(args.tasks).read_text(encoding="utf-8"))
    tasks = [{"goal": t["goal"], "annotated_steps": t.get("annotated_steps", "")} for t in tasks_raw][: args.n]
    print(f"CoT 风格调教（模型 {model}，画像 {args.profile}，{len(tasks)} 个任务）…")
    result = cot.run(client, tasks, styles_cfg)

    samples_out = OUT_DIR / "cot_styled_samples.jsonl"
    dpo_out = OUT_DIR / "cot_style_dpo.jsonl"
    samples_out.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in result["samples"]) + "\n", encoding="utf-8"
    )
    dpo_out.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in result["dpo_pairs"]) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["stats"], ensure_ascii=False, indent=1))
    print(f"SFT 样本 → {samples_out}；风格 DPO 对 → {dpo_out}")
    return 0


def cmd_vision(args) -> int:
    """多模态图文数据管线：图片→VL描述→问答/多轮对话→一致性校验（G0 闸门）。"""
    _gates().require("G0")
    import lib.multimodal as mm

    client, model = _client(args, role="vision")
    print(f"多模态管线（视觉引擎 {model}，目录 {args.input}）…")
    result = mm.run(client, args.input, qa_per_image=args.qa_per_image, limit=args.n)
    out = OUT_DIR / "vision_samples.jsonl"
    out.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in result["samples"]) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["stats"], ensure_ascii=False, indent=1))
    print(f"图文样本 → {out}；一致性校验驳回 {result['stats']['qa_rejected']} 条 QA（防幻觉质量门）")
    for r in result["rejected"][:2]:
        print(f" 驳回示例: {r.get('question', r.get('error', ''))[:60]} → {r.get('hallucinated', '')}")
    return 0


def cmd_dpo_enhance(args) -> int:
    """DPO 偏好对增强（G0 闸门）：candidates 多候选判分 / refine 自精炼 / hallucinate 幻觉负样本。"""
    _gates().require("G0")
    import lib.dpo_enhance as dpe

    client, model = _client(args)
    prompts = [json.loads(l)["prompt"] for l in pathlib.Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip()][: args.n]
    print(f"DPO 增强（模型 {model}，模式 {args.mode}，{len(prompts)} 条 prompt）…")
    if args.mode == "candidates":
        # 多模型采样（UltraFeedback 式）：默认模型 + judge 模型两个生成源，制造真实分差
        extra = [c for c in [_client(args, judge=True)[0]] if c.model != client.model]
        pairs = dpe.candidates(client, prompts, n_per_prompt=3, extra_clients=extra)
    elif args.mode == "refine":
        pairs = dpe.refine(client, prompts)
    else:  # hallucinate：输入含 answer/facts
        items = [
            {"prompt": json.loads(l)["prompt"], "answer": json.loads(l).get("answer", ""), "facts": json.loads(l).get("facts", "")}
            for l in pathlib.Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip()
        ][: args.n]
        pairs = dpe.hallucinate(client, items)

    out = OUT_DIR / f"dpo_enhanced_{args.mode}.jsonl"
    out.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8")
    print(f"生成偏好对 {len(pairs)} 条（分差门槛 candidates≥2 / 其余≥1）→ {out}")
    return 0


def cmd_dpo_merge(args) -> int:
    """统一汇集各来源 DPO 对：rollout 错误对 + 风格对 + 增强对 → 去重合并导出。"""
    import lib.dpo_enhance as dpe

    sources = {
        "distill": OUT_DIR / "dpo_pairs.jsonl",
        "cotstyle": OUT_DIR / "cot_style_dpo.jsonl",
        "candidates": OUT_DIR / "dpo_enhanced_candidates.jsonl",
        "refine": OUT_DIR / "dpo_enhanced_refine.jsonl",
        "hallucinate": OUT_DIR / "dpo_enhanced_hallucinate.jsonl",
        "enhanced_legacy": OUT_DIR / "dpo_enhanced.jsonl",
    }
    entries: list = []
    for name, path in sources.items():
        if not path.exists():
            continue
        try:
            loaded = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except json.JSONDecodeError:
            continue
        entries.extend(loaded)
        print(f" {name}: {len(loaded)} 对")
    merged = dpe.merge_pairs(entries)
    out = OUT_DIR / "dpo_all.jsonl"
    out.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in merged) + "\n", encoding="utf-8")
    print(f"合并去重后 {len(merged)} 对 → {out}")
    return 0


def cmd_agent_gen(args) -> int:
    """Agent 工具使用零参考数据（联网查找/代码审查编辑/间接联网；G0 闸门）。"""
    _gates().require("G0")
    import lib.agent_gen as ag

    client, model = _client(args)
    tools = ag.load_tools(ROOT / "configs" / "agent_tools.yaml")
    scenario_keys = list(tools["scenarios"].keys())
    scenarios = [args.scenario] if args.scenario != "all" else scenario_keys
    print(f"agent 零参考生成（模型 {model}，场景 {scenarios}，每场景 {args.n} 任务）…")
    out = OUT_DIR / "agent_samples.jsonl"
    # 跨运行查重：已有样本的 goal 哈希进 manifest（追加模式不产生重复）
    manifest: set = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                sample_id = json.loads(line).get("id", "")
                manifest.add(sample_id.split("-")[-1])
            except json.JSONDecodeError:
                continue
    result = ag.run(client, tools["tools"], scenarios, n_per_scenario=args.n, manifest=manifest)
    with open(out, "a", encoding="utf-8") as f:
        for s in result["samples"]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=1))
    print(f"轨迹样本 → {out}（与 rollout 蒸馏同构，可合并导出）")
    return 0


def cmd_gui_cot(args) -> int:
    """GUI 轨迹 CoT 蒸馏：调用 OpenCUA cot-generator 上游成品（需 G0 闸门）。"""
    _gates().require("G0")
    import subprocess

    import yaml

    from lib.adapters.chatlog_to_traj import validate_gui_traj_line
    from lib.adapters.opencua_out import merged_to_samples

    # 1) 输入校验（OpenCUA traj 格式）
    bad = 0
    for line in pathlib.Path(args.traj).read_text(encoding="utf-8").splitlines():
        err = validate_gui_traj_line(line)
        if err:
            print(f"输入格式错误: {err[:120]}")
            bad += 1
    if bad:
        return 6

    # 2) 上游成品整链（subprocess，env API_KEY；模型默认 vision-exp）
    local_cfg = yaml.safe_load((ROOT / "configs" / "backends.local.yaml").read_text(encoding="utf-8"))
    api_key = local_cfg["backends"]["deepseek"]["api_key"]
    upstream_dir = ROOT / "components" / "opencua" / "data" / "cot-generate"
    out_dir = pathlib.Path(args.out or (OUT_DIR / "gui_cot"))
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    # 上游按步骤断点缓存（已处理步骤跳过）；重复运行必须清空，否则回放旧结果
    import shutil

    if out_dir.exists():
        shutil.rmtree(out_dir)
    merged_old = out_dir.parent / "task_with_cot.jsonl"
    if merged_old.exists():
        merged_old.unlink()
    print(f"运行上游 OpenCUA cot-generator（模型 {args.model}）…")
    proc = subprocess.run(
        [
            sys.executable, "-W", "ignore", "gen_cot.py",
            "--traj_path", str(pathlib.Path(args.traj).resolve()),
            "--image_folder", str(pathlib.Path(args.images).resolve()),
            "--output_dir", str(out_dir.resolve()),
            "--model", args.model,
        ],
        cwd=str(upstream_dir),
        env={**os.environ, "API_KEY": api_key, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"[上游失败] {proc.stderr[-300:]}")
        return proc.returncode

    # 3) 适配为统一格式（上游自动 merge 到 output_dir 父目录）
    merged = out_dir.parent / "task_with_cot.jsonl"
    if not merged.exists():
        merged = out_dir.parent / "gui_cot" / "task_with_cot.jsonl"
    if not merged.exists():
        print("[上游成功但未找到合并产物] 请检查上游输出")
        return 6
    result = merged_to_samples(str(merged))
    out = OUT_DIR / "gui_samples.jsonl"
    with open(out, "a", encoding="utf-8") as f:
        for s in result["samples"]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=1))
    print(f"GUI 轨迹样本 → {out}（与 rollout 蒸馏同构）")
    return 0


def cmd_models(args) -> int:
    """列出指定后端/自定义端点的可用模型（models 网关 /v1/models 自动获取）。"""
    import yaml
    from openai import OpenAI

    if args.base_url:
        base_url = args.base_url
        api_key = os.environ.get("OPENAI_API_KEY", "")
    else:
        local_cfg = yaml.safe_load((ROOT / "configs" / "backends.local.yaml").read_text(encoding="utf-8"))
        b = local_cfg.get("backends", {}).get(args.backend or "deepseek", {})
        base_url, api_key = b.get("base_url", ""), b.get("api_key", "")
    if not base_url:
        print("未找到后端配置（--backend 指定或 --base-url 自定义）")
        return 6
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        models = [m.id for m in client.models.list().data]
        print(f"{base_url} 可用模型（{len(models)}）：")
        for m in sorted(models):
            print(f"  - {m}")
    except Exception as e:  # noqa: BLE001
        print(f"[网关不可用] {str(e)[:200]}")
        return 6
    return 0


def cmd_style_correct(args) -> int:
    """语言风格强矫正（多轮去 AI 味；用户注入规则/示例；G0 闸门；refine 角色）。"""
    _gates().require("G0")
    import lib.style_fix as sf

    client, model = _client(args, role="refine")  # 精炼角色：文笔要求高
    cfg = sf.load_rules(args.rules)
    samples = [
        json.loads(l)
        for l in pathlib.Path(args.input).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    print(f"风格强矫正（模型 {model}，{len(samples[:args.n])} 样本，规则 {len(cfg['rules'])} 条，示例 {len(cfg['exemplars'])} 对）…")
    result = sf.run(client, samples, cfg, rounds=args.rounds, threshold=args.threshold, limit=args.n)
    out = OUT_DIR / "style_corrected_samples.jsonl"
    dpo_out = OUT_DIR / "stylefix_dpo.jsonl"
    out.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in result["samples"]) + "\n", encoding="utf-8")
    dpo_out.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in result["dpo_pairs"]) + "\n", encoding="utf-8")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=1))
    print(f"矫正样本 → {out}；矫正前后 DPO 对 → {dpo_out}")
    return 0


def cmd_commands(args) -> int:
    """列出命令注册表（单一事实源）：分组展示，附闸门标注。"""
    from lib.extensions import groups, load_commands

    cmds = load_commands(ROOT)
    for group, names in groups(cmds).items():
        print(f"== {group} ==")
        for n in names:
            m = cmds[n]
            gates = "/".join(m.get("gates", [])) or "-"
            print(f"  {n:<15} 闸门:{gates:<6} {m['help']}")
    return 0


def cmd_review_remote(args) -> int:
    """分布式评审客户端：协作者在自己主机拉取→（本地 agent 自动或人工）→提交回中心机。"""
    import lib.review_remote as rr
    from lib.llm_client import load_backend

    if args.action == "setup":
        return _setup_review_remote(args)

    from lib.io_utils import atomic_json
    cfg = rr.load_config(args.config)
    ws_name = WS.resolve(getattr(args, "ws", None))
    if ws_name != WS.DEFAULT and cfg.get("dataset", "rollout_review") == "rollout_review":
        cfg["dataset"] = WS.dataset_name(ws_name)
    client = rr.get_client(cfg)
    path = rr.inbox_path(cfg, client, OUT_DIR)
    with rr.batch_lock(path):
        if args.action == "pull":
            rows = rr.pull(cfg, batch=args.batch or cfg.get("batch_size", 10), client=client, path=path)
            print(f"待审 {len(rows)} 条；账号 {client.me['username']}；批次 {path}")
            return 0
        batch = rr.load_batch(path)
        if batch.get("submitted"):
            print("该批已提交；pull 获取下一批。")
            return 0
        decisions = batch["records"]
        if args.action == "auto":
            _gates().require("G0")
            judge, model = load_backend(ROOT, model=args.model or cfg.get("model") or None,
                                        backend=cfg.get("backend"), base_url=cfg.get("base_url"), role="judge")
            decisions = rr._judge_answers(decisions, judge, model, policy=args.policy or cfg.get("policy", "quality"),
                                         max_chars=cfg.get("max_review_chars", 60000))
            batch["records"] = decisions
            atomic_json(path, batch)
            errors = sum(bool(d.get("review_error")) for d in decisions)
            print(f"判定 {len(decisions)} 条；keep={sum(d.get('decision') == 'keep' for d in decisions)}；未完成={errors}。确认后显式 submit。")
            return 4 if errors else 0
        if args.action == "human":
            batch["records"] = rr.human_loop(decisions, cfg)
            atomic_json(path, batch)
            return 0
        n = rr.submit(decisions, cfg, client=client)
        batch["submitted"] = True
        atomic_json(path, batch)
        print(f"账号 {client.me['username']} 提交 {n} 条（可安全重试，不重复计票）")
        return 0


def cmd_quality_report(args) -> int:
    from lib.quality import read_samples, report
    from lib.review import pull_decisions
    source = pathlib.Path(args.input) if args.input else OUT_DIR / "rollout_samples.jsonl"
    result = report(read_samples(source), pull_decisions(dataset_name=WS.dataset_name()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args) -> int:
    """环境全项自检（df doctor）：把真机踩过的坑固化为检查，一键确认可开工。"""
    from lib.doctor import main as doctor_main

    return doctor_main()


def cmd_dsh(args) -> int:
    """一键进 dsh：任务直跑（--team 挂审核小队补丁）。补丁/技能联接/环境全自动。"""
    from lib.dsh_env import run
    task = args.task or "读取 review-team 技能并组织审核。"
    if args.review_config:
        if not args.team:
            raise ValueError("--review-config requires --team")
        mappings = []
        for assignment in args.review_config:
            policy, separator, filename = assignment.partition("=")
            if not separator or policy not in ("quality", "safety"):
                raise ValueError("Use --review-config quality=PATH or safety=PATH")
            path = pathlib.Path(filename).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            mappings.append({"policy": policy, "config": str(path)})
        task += "\n审核配置分配（只传路径，不读取或回显密钥）：" + json.dumps(mappings, ensure_ascii=False)
    task += f"\n所有 dataforge 调用显式传 ws={WS.resolve()}；不得自行批准闸门。"
    return run(task, args.team, ROOT)


def cmd_workspace(args) -> int:
    """工作区管理：数据按区分流（list/status/use）。"""
    if args.action == "list":
        cur = WS.current()
        for name in WS.list_all():
            mark = "（当前）" if name == cur else ""
            print(f"  {name}{mark}")
        return 0
    if args.action == "status":
        info = WS.status(getattr(args, "ws", None))
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0
    if args.action == "use":
        if not args.name:
            print("用法: df workspace use <名字>")
            return 1
        name = WS.set_current(args.name)
        from lib.monitor import trace_run
        trace_run(ROOT, "workspace_selected", {"selected_workspace": name})
        print(f"当前工作区 → {name}（输出目录 {WS.out(name)}）")
        return 0
    return 1


def cmd_user(args) -> int:
    """协作者账号管理（审核中心内置；替代原 Argilla 建号脚本）。"""
    from lib import review_center as rc

    if args.action == "grant":
        rc.grant(args.name, WS.dataset_name(getattr(args, "ws", None)))
        print("已授予所选工作区访问权限")
        return 0
    if args.action == "create":
        if not args.name:
            print("用法: df user create <用户名> [--role admin|annotator]")
            return 1
        try:
            key = rc.create_user(args.name, role=getattr(args, "role", "annotator") or "annotator")
        except ValueError as e:
            print(f"[创建失败] {e}")
            return 1
        from lib.monitor import trace_run
        trace_run(ROOT, "review_user_created", {"username": args.name, "role": args.role})
        print(f"协作者 {args.name}（{getattr(args, 'role', 'annotator')}）已创建，api_key={key}")
        print("把 api_key 离线发给协作者，配进其 configs/review_remote.yaml（server=中心机地址）")
        return 0
    if args.action == "list":
        rows = rc.user_rows()
        print(f"共 {len(rows)} 个账号：")
        for r in rows:
            print(f"  {r['username']}（{r['role']}）创建于 {r['created_at']}")
        return 0
    return 1


def _launch_console() -> int:
    """统一运营控制台（单进程融合）：streamlit UI + 审核中心 API（6900，本进程线程托管）。

    审核 API 必须在启动器进程起线程：streamlit 只有浏览器会话连入才执行页面脚本，
    若等页面加载再起 API，协作者会在没人打开页面时连不上中心。"""
    from lib import review_center as rc

    rc.start_in_thread()  # 幂等；端口被占（如独立 review-server）也不阻塞控制台
    import subprocess

    print("启动控制台: http://localhost:8501（审核中心 API: http://127.0.0.1:6900）")
    from streamlit.web import bootstrap
    try:
        bootstrap.run(str(ROOT / "lib" / "webapp.py"), False, [],
                      {"server.port": 8501, "server.headless": True, "server.address": "127.0.0.1"})
    finally:
        rc.stop_thread()
    return 0


def cmd_review_server(args) -> int:
    """独立运行审核中心 HTTP 服务（无 UI 的中心机/协作者自建中心；控制台进程会自动内置）。"""
    from lib import review_center as rc

    rc.serve(port=args.port)
    return 0


def _setup_review_remote(args) -> int:
    """交互式生成协作者评审配置（免手编 yaml），写文件后立即与中心机握手验证。"""
    import yaml

    import lib.review_remote as rr

    cfg_path = pathlib.Path(args.config)
    print("生成协作者评审配置（回车用默认值）")
    if cfg_path.exists():
        raise FileExistsError(f"配置已存在，未覆盖：{cfg_path}")
    import getpass
    server = args.server or (input("中心机地址 [http://127.0.0.1:6900]: ").strip() or "http://127.0.0.1:6900")
    api_key = os.environ.get(args.key_env, "") if args.key_env else getpass.getpass("审核 API key: ").strip()
    if not api_key:
        print("未输入 api_key，放弃（先找中心机管理员要一份）")
        return 1
    dataset = WS.dataset_name()
    model = args.model or ("" if args.server else input("评审模型（留空=judge 槽位）: ").strip())
    cfg = {"server": server, "api_key": api_key, "dataset": dataset,
           "batch_size": 10, "model": model or ""}
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("x", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, allow_unicode=True, sort_keys=False)
    print(f"已写入 {cfg_path}")
    try:
        me = rr.get_client(cfg).me
        print(f"✔ 与中心机握手成功：我是 {me.get('username')}（可以 df review-remote pull 开工）")
    except Exception as e:  # noqa: BLE001
        print(f"[注意] 中心机暂不可达：{str(e)[:160]}")
        print("配置已保存；中心机可达后直接 df review-remote pull 即可")
        return 2
    return 0


def cmd_gate(args) -> int:
    gate = _gates()
    if args.action == "status":
        for gid, g in gate.defs.items():
            print(f"{gid} [{gate.status(gid)}] {g.title} — {g.trigger}")
        return 0
    if args.action == "propose":
        gate.propose(args.gate_id)
        print(f"{args.gate_id} → awaiting（待确认）")
        return 0
    if args.action in ("approve", "reject"):
        gate.decide(args.gate_id, args.action == "approve")
        print(f"{args.gate_id} → {'approved' if args.action == 'approve' else 'rejected'}")
        return 0
    return 1


def build_parser():
    parser = argparse.ArgumentParser(prog="df", description="Super-LLM-distill-Gen CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="导入 rollout 数据（需 G1 闸门）")
    p_import.add_argument("--limit", type=int, default=0)
    p_import.add_argument("--export-limit", type=int, default=200)
    p_import.add_argument("--cot", default="separated", choices=["separated", "tags", "plain", "drop"])
    p_import.set_defaults(func=cmd_import)

    sub.add_parser("stats", help="查看导入统计").set_defaults(func=cmd_stats)

    p_preview = sub.add_parser("preview", help="预览样本（--html 生成美化渲染页）")
    p_preview.add_argument("--n", type=int, default=10)
    p_preview.add_argument("--file", default=None, help="缺省=当前工作区 rollout_samples.jsonl")
    p_preview.add_argument("--html", action="store_true", help="生成静态 HTML 预览页（人工过目）")
    p_preview.set_defaults(func=cmd_preview)

    p_export = sub.add_parser("export", help="导出训练格式（minimind=三件套 sft_t2t/pretrain_t2t/dpo）")
    p_export.add_argument("--format", default="chat", choices=["llamafactory", "chat", "minimind", "all"])
    p_export.add_argument("--input", default=None, help="缺省=当前工作区 rollout_samples.jsonl")
    p_export.add_argument("--out", default=None, help="缺省=当前工作区 export/sft.jsonl")
    p_export.add_argument("--tag", help="新版本标签；重复标签拒绝覆盖，缺省为 UTC 时间戳")
    p_export.add_argument("--bulk", action="store_true", help="G3 + 当前样本质量和审核覆盖校验")
    p_export.set_defaults(func=cmd_export)

    p_distill = sub.add_parser("distill", help="蒸馏质检：分类+DPO负样本+可选LLM打分")
    p_distill.add_argument("--llm-check", type=int, default=None, help="LLM 打分条数（>0 需 G0 闸门；缺省取 pipelines/distill.yaml）")
    p_distill.add_argument("--backend")
    p_distill.add_argument("--model")
    p_distill.set_defaults(func=cmd_distill)

    p_review = sub.add_parser("review", help="人工审核（app=统一控制台，push/pull=Argilla 可选）")
    p_review.add_argument("action", choices=["app", "push", "pull", "summary"])
    p_review.add_argument("--n", type=int, default=20, help="push 条数")
    p_review.set_defaults(func=cmd_review)

    p_console = sub.add_parser("console", help="统一运营控制台（七页：总览/预览/运行/审核/监控/闸门/偏好）")
    p_console.set_defaults(func=lambda args: _launch_console())

    p_peval = sub.add_parser("prompt-eval", help="提示词真机评测（G0 闸门）")
    p_peval.add_argument("--backend")
    p_peval.add_argument("--model")
    p_peval.add_argument("--ids", help="只评测这些提示词 id/用例名（逗号分隔）")
    p_peval.add_argument("--cases-file", help="自定义用例文件（YAML，格式见 lib/prompt_eval.load_cases）")
    p_peval.set_defaults(func=cmd_prompt_eval)

    p_translate = sub.add_parser("translate", help="翻译管线：互译+回译校验（G0 闸门）")
    p_translate.add_argument("--input", default=str(ROOT / "data" / "seeds" / "topics.txt"))
    p_translate.add_argument("--limit", type=int, default=5)
    p_translate.add_argument("--backend")
    p_translate.add_argument("--model")
    p_translate.set_defaults(func=cmd_translate)

    p_identity = sub.add_parser("identity-gen", help="身份问答零参考训练集（G0 闸门）")
    p_identity.add_argument("--config", default=str(ROOT / "configs" / "identity.example.yaml"))
    p_identity.add_argument("--max-answer-tokens", type=int, default=None,
                            help="回答截断上限（保护性；缺省取 length_profiles.yaml）")
    p_identity.add_argument("--backend")
    p_identity.add_argument("--model")
    p_identity.set_defaults(func=cmd_identity_gen)

    p_doc2corpus = sub.add_parser("doc2corpus", help="文档→CPT 语料（知识注入层，零 LLM）")
    p_doc2corpus.add_argument("--input", required=True, help="文件或目录（md/txt/pdf/docx）")
    p_doc2corpus.add_argument("--out", default=None, help="缺省=当前工作区 corpus/docs.jsonl")
    p_doc2corpus.add_argument("--chunk-size", type=int, default=None)
    p_doc2corpus.add_argument("--overlap", type=int, default=None)
    p_doc2corpus.set_defaults(func=cmd_doc2corpus)

    p_doc2data = sub.add_parser("doc2data", help="文档→问答 SFT（表达层+事实校验，G0 闸门）")
    p_doc2data.add_argument("--input", required=True)
    p_doc2data.add_argument("--qa-per-chunk", type=int, default=None)
    p_doc2data.add_argument("--max-chunks", type=int, default=None)
    p_doc2data.add_argument("--mode", choices=["single", "cross"], default="single",
                            help="single=逐块问答；cross=跨块综合分析（知识学习自然长数据）")
    p_doc2data.add_argument("--chunk-size", type=int, default=None)
    p_doc2data.add_argument("--backend")
    p_doc2data.add_argument("--model")
    p_doc2data.set_defaults(func=cmd_doc2data)

    p_cotstyle = sub.add_parser("cot-style", help="CoT 风格偏好调教（G0 闸门）")
    p_cotstyle.add_argument("--tasks", default=str(ROOT / "configs" / "cot_tasks.example.yaml"))
    p_cotstyle.add_argument("--profile", default="default")
    p_cotstyle.add_argument("--n", type=int, default=5)
    p_cotstyle.add_argument("--backend")
    p_cotstyle.add_argument("--model")
    p_cotstyle.set_defaults(func=cmd_cot_style)

    p_vision = sub.add_parser("vision", help="多模态图文数据管线（G0 闸门）")
    p_vision.add_argument("--input", required=True, help="图片目录")
    p_vision.add_argument("--n", type=int, default=5)
    p_vision.add_argument("--qa-per-image", type=int, default=2)
    p_vision.add_argument("--backend")
    p_vision.add_argument("--model")
    p_vision.set_defaults(func=cmd_vision)

    p_dpo = sub.add_parser("dpo-enhance", help="DPO 偏好对增强（G0 闸门）")
    p_dpo.add_argument("--mode", choices=["candidates", "refine", "hallucinate"], required=True)
    p_dpo.add_argument("--input", required=True, help="prompts JSONL（hallucinate 模式需含 answer/facts）")
    p_dpo.add_argument("--n", type=int, default=5)
    p_dpo.add_argument("--backend")
    p_dpo.add_argument("--model")
    p_dpo.set_defaults(func=cmd_dpo_enhance)

    p_dpom = sub.add_parser("dpo-merge", help="统一汇集各来源 DPO 对并去重")
    p_dpom.set_defaults(func=cmd_dpo_merge)

    p_agent = sub.add_parser("agent-gen", help="Agent 工具使用零参考数据（G0 闸门）")
    p_agent.add_argument("--scenario", default="all", choices=["all", "web", "code", "indirect_web"])
    p_agent.add_argument("--n", type=int, default=2, help="每场景任务数")
    p_agent.add_argument("--backend")
    p_agent.add_argument("--model")
    p_agent.set_defaults(func=cmd_agent_gen)

    p_gui = sub.add_parser("gui-cot", help="GUI 轨迹 CoT 蒸馏（上游 OpenCUA 成品，G0 闸门）")
    p_gui.add_argument("--traj", required=True, help="OpenCUA traj JSONL（task_id/instruction/traj[{image,value.code}]）")
    p_gui.add_argument("--images", required=True, help="截图目录")
    p_gui.add_argument("--out", default=None, help="缺省=当前工作区 gui_cot")
    p_gui.add_argument("--model", default="deepseek-v4-flash-vision-exp")
    p_gui.set_defaults(func=cmd_gui_cot)

    p_monitor = sub.add_parser("monitor", help="运行监控摘要（本地审计）")
    p_monitor.add_argument("--n", type=int, default=10)
    p_monitor.set_defaults(func=cmd_monitor)

    p_remote = sub.add_parser("review-remote", help="分布式评审客户端（协作者主机端）")
    p_remote.add_argument("action", choices=["pull", "auto", "human", "submit", "setup"])
    p_remote.add_argument("--config", default=str(ROOT / "configs" / "review_remote.yaml"))
    p_remote.add_argument("--batch", type=int, default=None)
    p_remote.add_argument("--model")
    p_remote.add_argument("--policy", choices=["quality", "safety"])
    p_remote.add_argument("--server")
    p_remote.add_argument("--key-env", help="Environment variable containing the review API key")
    p_remote.set_defaults(func=cmd_review_remote)

    p_cmds = sub.add_parser("commands", help="列出命令注册表（单一事实源）")
    p_cmds.set_defaults(func=cmd_commands)

    p_gate = sub.add_parser("gate", help="闸门管理")
    p_gate.add_argument("action", choices=["propose", "approve", "reject", "status"])
    p_gate.add_argument("gate_id", nargs="?", default="")
    p_gate.set_defaults(func=cmd_gate)

    p_models = sub.add_parser("models", help="列出可用模型（models 网关自动获取）")
    p_models.add_argument("--backend", help="配置中的后端名（缺省 deepseek）")
    p_models.add_argument("--base-url", help="自定义端点 URL（如 http://localhost:11434/v1）")
    p_models.set_defaults(func=cmd_models)

    p_stylefix = sub.add_parser("style-correct", help="语言风格强矫正（多轮去 AI 味，G0 闸门）")
    p_stylefix.add_argument("--input", required=True, help="统一样本 JSONL")
    p_stylefix.add_argument("--rules", default=str(ROOT / "configs" / "style_rules.example.yaml"))
    p_stylefix.add_argument("--rounds", type=int, default=3)
    p_stylefix.add_argument("--threshold", type=int, default=4)
    p_stylefix.add_argument("--n", type=int, default=20)
    p_stylefix.add_argument("--backend")
    p_stylefix.add_argument("--model")
    p_stylefix.set_defaults(func=cmd_style_correct)

    p_workspace = sub.add_parser("workspace", help="工作区管理（数据按区分流：list/status/use）")
    p_workspace.add_argument("action", choices=["list", "status", "use"])
    p_workspace.add_argument("name", nargs="?", default="", help="工作区名（use 时必填）")
    p_workspace.set_defaults(func=cmd_workspace)

    p_user = sub.add_parser("user", help="协作者账号管理（审核中心内置）")
    p_user.add_argument("action", choices=["create", "list", "grant"])
    p_user.add_argument("name", nargs="?", default="", help="用户名（create 时必填）")
    p_user.add_argument("--role", choices=["admin", "annotator"], default="annotator")
    p_user.set_defaults(func=cmd_user)

    p_rserver = sub.add_parser("review-server", help="审核中心 HTTP 服务（独立模式；控制台进程内置同款）")
    p_rserver.add_argument("--port", type=int, default=6900)
    p_rserver.set_defaults(func=cmd_review_server)

    p_dsh = sub.add_parser("dsh", help="一键进 dsh：任务直跑（--team 挂审核小队补丁，环境全自动）")
    p_dsh.add_argument("task", nargs="?", help="给 harness agent 的任务文本")
    p_dsh.add_argument("--review-config", action="append", help="可重复：quality=配置路径 或 safety=配置路径")
    p_dsh.add_argument("--team", action="store_true", help="挂载审核小队补丁（子智能体协作用）")
    p_dsh.set_defaults(func=cmd_dsh)

    p_doctor = sub.add_parser("doctor", help="环境全项自检（一键确认可开工）")
    p_doctor.set_defaults(func=cmd_doctor)

    p_quality = sub.add_parser("quality-report", help="当前样本结构、重复和审核覆盖报告")
    p_quality.add_argument("--input")
    p_quality.set_defaults(func=cmd_quality_report)

    # 全局 --ws：所有子命令可用（df import --ws docs）；default 工作区=原 data/output
    for p in sub.choices.values():
        p.add_argument("--ws", help="工作区名（缺省=当前工作区，见 df workspace list）")

    return parser


def main() -> int:
    global OUT_DIR, GATES_STATE
    parser = build_parser()
    args = parser.parse_args()
    # 工作区解析在分发前完成：重绑本模块常量即对全部 cmd_* 生效
    ws_name = WS.resolve(getattr(args, "ws", None))
    OUT_DIR = WS.output_at(ROOT, ws_name)
    if args.cmd not in ("doctor", "quality-report"):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
    GATES_STATE = OUT_DIR / "gates_state.json"
    os.environ["DF_WORKSPACE"] = ws_name
    try:
        return args.func(args)
    except GateBlocked as e:
        print(f"[闸门拦截] {e}")
        return 3
    except (ValueError, FileNotFoundError, FileExistsError, PermissionError, ConnectionError) as e:
        print(f"[未完成] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        if e.__class__.__name__ == "BudgetExceeded":
            print(f"[预算硬停] {e}")
            return 5
        raise


if __name__ == "__main__":
    raise SystemExit(main())