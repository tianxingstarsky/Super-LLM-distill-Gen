"""Node-local checklist editing with existing durable personal templates."""
import streamlit as st
from lib.contexts.workflows.contracts import default_checklist_text, checklist_from_text, default_integrated_reflection
from .prompt_library_controls import render_prompt_library


def checklist_settings(workspace, field):
    enabled = field(workspace, f"workflow-cot-checklist-enabled:{workspace}", False)
    saved_version = field(workspace, f"workflow-cot-checklist-version:{workspace}", None)
    if not enabled:
        return default_integrated_reflection() if saved_version in (None, 7) else None
    default_text = reflection_text() if saved_version in (None, 7) else default_checklist_text()
    config = checklist_from_text(field(workspace, f"workflow-cot-checklist-text:{workspace}", default_text),
        version=saved_version if saved_version is not None else 7)
    if config["version"] == 6:
        config["style_admission"] = field(workspace, f"workflow-cot-style-admission:{workspace}",
                                           "strict" if saved_version is not None else "advisory")
        policy = field(workspace, f"workflow-cot-evidence-policy:{workspace}", 0 if saved_version is not None else 2)
        if policy in (1, 2):
            config["evidence_policy"] = policy
        if policy == 2:
            config["style_admission"] = "strict"
    return config


def reflection_text():
    return "\n".join(("? " if rule["allow_na"] else "")
                     + (f"[{rule['phases'][0]}] " if len(rule.get("phases", [])) == 1 else "")
                     + rule["requirement"]
                     for rule in default_integrated_reflection()["rules"])


def _use_integrated_reflection(workspace, save_field):
    key = f"workflow-cot-checklist-version:{workspace}"
    st.session_state[key] = 7
    save_field(workspace, key)


def render_reasoning_checklist(workspace, *, field, restore, save_field, prompt_library):
    en = st.session_state.get("ui_language", "zh") == "en"
    enabled_key = f"workflow-cot-checklist-enabled:{workspace}"
    text_key = f"workflow-cot-checklist-text:{workspace}"
    saved_version = field(workspace, f"workflow-cot-checklist-version:{workspace}", None)
    integrated = saved_version in (None, 7)
    default_text = reflection_text() if integrated else default_checklist_text()
    restore(workspace, enabled_key, False)
    restore(workspace, text_key, default_text)
    if integrated:
        restyle = field(workspace, f"workflow-generation-answer-mode:{workspace}:cot", "joint") == "faithful_restyle"
        st.caption(("Apply the guidance during editing. Content review follows the coverage selected above."
                    if en else "改写时自然运用思考要点；内容核验按上方抽检或逐条策略执行。") if restyle else
                   ("Draft, examine and refine within generation. No extra model review calls."
                    if en else "在生成中完成草稿推敲与修正，不额外调用模型评审。"))
    else:
        st.button("Use reflection during generation" if en else "改为生成中推敲",
                  key=f"workflow-cot-integrate:{workspace}", on_click=_use_integrated_reflection,
                  args=(workspace, save_field))
    label = (("Custom thinking guidance" if en else "自定义思考要点") if integrated else
             ("Evidence-backed self-check" if en else "逐项证据自检"))
    enabled = st.toggle(label, key=enabled_key,
                        on_change=save_field, args=(workspace, enabled_key))
    if not enabled:
        return
    saved_version = field(workspace, f"workflow-cot-checklist-version:{workspace}", None)
    if saved_version == 6:
        style_key = f"workflow-cot-style-admission:{workspace}"
        policy = field(workspace, f"workflow-cot-evidence-policy:{workspace}", 0 if saved_version is not None else 2)
        restore(workspace, style_key, "strict" if saved_version is not None or policy == 2 else "advisory")
        if policy == 2:
            st.session_state[style_key] = "strict"
        st.selectbox("Style review" if en else "表达风格评审", ("advisory", "strict"), key=style_key,
            disabled=policy == 2,
            format_func=lambda value: ({"advisory": "Advice only", "strict": "Must pass"} if en else
                                       {"advisory": "偏差作为改进建议", "strict": "必须满足风格要求"})[value],
            on_change=save_field, args=(workspace, style_key),
            help=(("Requested language and style requirements must pass under the new evidence policy." if en else
                   "新版证据核查要求指定语言与风格必须通过；可在节点修改具体要求，不能将缺失要求当建议放行。") if policy == 2 else
                  ("Content and every checklist rule must still pass. Put mandatory language/format requirements in the checklist, or choose Must pass." if en else
                   "内容与清单中的每项规则仍必须通过。必须满足的语言或格式要求可写进清单，或选择严格风格要求。")))
    agent = field(workspace, f"workflow-cot-checklist-version:{workspace}", 6) in (3, 4, 5, 6)
    if not integrated:
        st.caption(("The agent chooses what to investigate, reads sources and checks calculations with tools. "
                "Evidence is saved as it works; an independent reviewer checks the same text." if en else
                "Agent 根据疑点自主查资料、复算和核对前提，证据逐项保存；独立评审再核查同一份正文。") if agent else
               ("This copied task retains its original batch review protocol." if en else "此任务保留原有的逐批核查协议。"))
    render_prompt_library(prompt_library, workspace, "reasoning.checklist", save_field=save_field,
        fields={"text": text_key}, defaults={"text": default_text},
        label="My guidance" if en else "我的思考要点", save_label="Save guidance" if en else "保存要点")
    st.text_area("Guidance · one per line" if en else "思考要点 · 每行一项", key=text_key, height=190,
                 on_change=save_field, args=(workspace, text_key), max_chars=32000,
                 help=(("Apply relevant guidance naturally. No fixed order, scorecard or extra reviewer."
                        if en else "按本题需要自然运用，不规定顺序，不输出评分表，也不增加评审调用。") if integrated else
                       ("Up to 32 rules. Prefix a conditional rule with '? ' to allow a justified not-applicable result. "
                       "Other rules must have evidence. In protocol 5, [prefix] or [final] limits a rule to that phase. No fixed reasoning order." if en else
                       "最多 32 项。允许因任务无须采用的规则，行首加 ? 和空格；其余必须有证据。新版可加 [prefix] 或 [final] 限定阶段。清单不规定推理顺序。")))
    try:
        config = checklist_settings(workspace, field)
        st.caption((f"{len(config['rules'])} rules · frozen when the task starts" if en else
                    f"{len(config['rules'])} 项规则 · 启动时冻结，旧任务不受编辑影响"))
    except ValueError:
        st.error("Rules must be unique, non-empty and within the limit." if en else "规则不能重复或为空，且不能超过数量与长度限制。")
