"""Delivery quantity controls, independent of execution queue size."""
import math

import streamlit as st

from lib.domain.workflow_production import MAX_PRODUCTION_GOAL, validate_production
from lib.domain.workflow_scale import MAX_CANDIDATES
from lib.presentation.streamlit.i18n import translate_label


def _save_goal(workspace, target, widget_key, save):
    key = f"workflow-production-goals:{workspace}"
    values = dict(st.session_state.get(key, {}))
    values[target] = int(st.session_state[widget_key])
    st.session_state[key] = values
    save(workspace, key)


def _reset_goals(workspace, save):
    key = f"workflow-production-goals:{workspace}"
    st.session_state[key] = {}
    for widget_key in list(st.session_state):
        if widget_key.startswith(f"production-goal-editor:{workspace}:"):
            del st.session_state[widget_key]
    save(workspace, key)


def _save_limit(workspace, field, widget_key, save):
    key = f"workflow-production-limits:{workspace}"
    values = dict(st.session_state.get(key, {}))
    value = st.session_state[widget_key]
    values[field] = float(value) / 100 if field == "min_acceptance_rate" else int(value)
    st.session_state[key] = values
    save(workspace, key)


def _reset_limits(workspace, save):
    key = f"workflow-production-limits:{workspace}"
    st.session_state[key] = {}
    save(workspace, key)


def render_refill_limits(workspace, defaults, save):
    key = f"workflow-production-limits:{workspace}"
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, {})
    saved = st.session_state[key]
    with st.expander("生产停止条件（可选）"):
        st.caption("限制用于控制成本和低收益探索，不要求凑足数量；开始运行后固定。")
        if saved:
            st.button("恢复自动停止条件", key=f"production-limits-reset:{workspace}",
                      on_click=_reset_limits, args=(workspace, save))
        fields = (("max_attempts", "候选尝试上限", 1, 3_000_000),
                  ("max_rounds", "最多生产轮数", 1, 10_000),
                  ("round_size", "每轮候选上限", 1, 100_000),
                  ("low_acceptance_rounds", "低通过率连续轮数", 1, 100),
                  ("item_retries", "单条额外重试次数", 0, 5))
        for field, label, low, high in fields:
            editor_key = f"production-limit-editor:{workspace}:{field}"
            st.session_state[editor_key] = saved.get(field, defaults[field])
            st.number_input(label, low, high, value=None, key=editor_key,
                            on_change=_save_limit, args=(workspace, field, editor_key, save))
        editor_key = f"production-limit-editor:{workspace}:min_acceptance_rate"
        st.session_state[editor_key] = 100.0 * saved.get("min_acceptance_rate", defaults["min_acceptance_rate"])
        st.number_input("最低通过率（%）", 0.0, 100.0, value=None, step=0.5, key=editor_key,
                        on_change=_save_limit, args=(workspace, "min_acceptance_rate", editor_key, save))
    return saved


def render_delivery_goal(workspace, targets, source_mode, labels, number_input, save):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    for field, default in (("enabled", True), ("budget", 0.0), ("goals", {}), ("policy", "quality_first")):
        key = f"workflow-production-{field}:{workspace}"
        if key not in st.session_state:
            st.session_state[key] = draft.get(key, default)
    enabled_key = f"workflow-production-enabled:{workspace}"
    enabled = st.toggle("自动分批生产", value=None, key=enabled_key,
                        on_change=save, args=(workspace, enabled_key),
                        help="按来源与质量安排有限生产。期望规模不必达，保留真实合格结果。")
    synthetic = [target for target in targets if target != "agent" and
                 (target != "cpt" or source_mode == "开放需求")]
    count_key = f"workflow-count:{workspace}"
    limit = MAX_PRODUCTION_GOAL if enabled else MAX_CANDIDATES
    # A saved million-row delivery goal is distinct from the legacy candidate limit.
    if st.session_state.get(count_key, draft.get(count_key, 0)) > limit:
        st.session_state[count_key] = limit
        save(workspace, count_key)
    count = (number_input("期望样本量（非必达）" if enabled else "候选样本规模", 1, limit, 1000,
                          step=100, key=count_key,
                          help="用于规划规模和交付上限。质量不足时少产，不放宽质检。偏好按对计数；原文与轨迹按实际来源处理。")
             if synthetic else st.session_state.get(count_key, draft.get(count_key, 1000)))
    overrides = st.session_state.get(f"workflow-production-goals:{workspace}", {})
    goals = {target: int(overrides.get(target, count)) for target in synthetic}
    if enabled and (len(synthetic) > 1 or any(target in overrides for target in synthetic)):
        with st.expander("分别设置各类数量"):
            language = st.session_state.get("ui_language", "zh")
            if overrides:
                st.button("统一使用上方数量", key=f"production-goals-reset:{workspace}",
                          on_click=_reset_goals, args=(workspace, save))
            for target in synthetic:
                widget_key = f"production-goal-editor:{workspace}:{target}"
                st.session_state[widget_key] = int(overrides.get(target, count))
                goals[target] = int(st.number_input(
                    translate_label(labels[target], language), 1, MAX_PRODUCTION_GOAL,
                    value=None, key=widget_key,
                    on_change=_save_goal, args=(workspace, target, widget_key, save)))
    if any(target not in synthetic for target in targets):
        st.caption("CPT 文档与导入轨迹按来源处理，不重复凑数。")
    budget_key = f"workflow-production-budget:{workspace}"
    if enabled:
        policy_key = f"workflow-production-policy:{workspace}"
        if synthetic:
            language = st.session_state.get("ui_language", "zh")
            policy_labels = {"quality_first": translate_label("优先覆盖当前素材", language),
                             "bounded_replenishment": translate_label("允许扩展更多场景", language)}
            st.selectbox("生成范围", tuple(policy_labels), key=policy_key,
                         format_func=policy_labels.__getitem__, on_change=save, args=(workspace, policy_key),
                         help="默认覆盖当前资料或需求，不因质检损失回填；需要时允许有依据的场景扩展，仍可提前结束。")
        budget = st.number_input("本次预算上限（USD）", 0.0, 1_000_000_000.0,
                                 value=None, step=1.0, format="%.2f", key=budget_key,
                                 on_change=save, args=(workspace, budget_key),
                                 help="0 表示不额外设限；仍受服务总预算约束。按配置单价记账，供应商最终账单可能不同。")
        defaults = validate_production({"version": 2, "quantity_policy": st.session_state[policy_key],
                                        "goals": goals, "budget_usd": float(budget)}, targets)
        limits = render_refill_limits(workspace, defaults, save)
        production = validate_production({**defaults, **limits}, targets)
        st.caption("数量是期望，不是任务成功条件。素材不足或没有新的有效场景时正常结束，按实际合格数量交付。")
    else:
        production = None
        st.caption("候选模式只处理指定候选，不自动补齐质检后的缺口。")
    return min(int(count), MAX_CANDIDATES), production


def review_coverage_hint(production, review):
    if not production or not review.get("enabled"):
        return
    total = sum(production["goals"].values())
    if not total:
        return
    if review.get("mode") == "all":
        st.caption("打包评审：对全部候选进行 AI 评审；评审调用也计入预算。")
    else:
        # Sampling is applied to each finite round, not a misleading whole-job cap.
        per_round = min(max(production["goals"].values()), production["round_size"])
        reviewed = min(per_round, review.get("max_samples_per_target", 1000),
                       math.ceil(per_round * review.get("sample_percent", 1) / 100))
        st.caption((f"打包抽检：每轮每类预计抽检 {reviewed:,} / {per_round:,} 条。未抽检记录不代表通过 AI 评审。"
                    if st.session_state.get("ui_language") != "en" else
                    f"Package sampling: about {reviewed:,} / {per_round:,} records per target per round. Unsampled records are not AI-reviewed."))


STOP_LABELS = {
    "goal_reached": "已达到合格目标", "source_exhausted": "可用来源已处理完",
    "source_limit_reached": "已达到来源处理上限",
    "max_attempts": "已达到尝试上限", "max_rounds": "已达到轮数上限",
    "low_acceptance_rate": "连续通过率过低", "final_validation_loss": "最终校验后仍有缺口",
    "budget_exhausted": "预算已用尽", "cancelled": "已停止",
    "source_coverage_complete": "当前素材已覆盖", "candidate_budget_reached": "已完成本次候选规划",
    "diminishing_returns": "新增有效样本趋少", "expectation_reached": "已达到本次期望规模",
    "director_saturation": "指导员未发现新的有依据场景",
    "no_new_grounded_scenario": "没有新的有依据场景",
    "planning_failed_after_repair": "规划未完成，已保留有效场景",
}


def render_delivery_progress(state):
    production = state.get("production")
    if not isinstance(production, dict):
        return
    language = st.session_state.get("ui_language", "zh")
    goals = production.get("goals", {})
    if goals:
        soft = production.get("version", 1) >= 2
        st.markdown("**" + translate_label("实际产出 / 期望规模" if soft else "合格目标进度", language) + "**")
        for target, progress in goals.items():
            goal, eligible = int(progress["goal"]), int(progress.get("eligible", 0))
            label = f"{target.upper()} · {eligible:,} / {goal:,}"
            reason = progress.get("stop_reason")
            if reason:
                label += " · " + translate_label(STOP_LABELS.get(reason, "待处理"), language)
            st.progress(min(1.0, eligible / goal), text=label)
    if production.get("budget_usd"):
        st.caption((f"本次预算 USD {production['budget_usd']:,.2f} · 已记账 USD {production.get('spent_usd', 0):,.4f}"
                    if language != "en" else
                    f"Run budget USD {production['budget_usd']:,.2f} · Recorded cost USD {production.get('spent_usd', 0):,.4f}"))
    reason = production.get("stop_reason")
    if reason in STOP_LABELS and reason not in {"goal_reached", "expectation_reached"}:
        message = translate_label(STOP_LABELS[reason], language)
        (st.caption if state.get("status") == "completed" else st.warning)(message)
    elif state.get("status") == "running":
        st.caption((f"第 {production.get('round', 0):,} 轮 · 已尝试 {production.get('attempted', 0):,} 个候选；合格数量按已提交轮次累计。"
                    if language != "en" else
                    f"Round {production.get('round', 0):,} · {production.get('attempted', 0):,} candidates attempted. Accepted counts include committed rounds."))
