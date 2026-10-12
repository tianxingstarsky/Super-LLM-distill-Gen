"""Explain the shared preference stage without inventing training stages."""
import html

from lib.presentation.streamlit.i18n import translate_label


PREFERENCE_LABELS = {
    "dpo": "DPO 偏好对", "rlaif": "RLAIF AI 反馈", "orpo": "ORPO 偏好对",
}


def selected_preferences(targets):
    return tuple(target for target in PREFERENCE_LABELS if target in (targets or ()))


def preference_stage_label(targets):
    selected = selected_preferences(targets)
    if len(selected) == 1:
        return PREFERENCE_LABELS[selected[0]]
    return " · ".join(target.upper() for target in selected) if selected else "偏好候选"


def preference_route(node, review_repair=None):
    if node == "preference":
        if review_repair is not None:
            return "本节点生成候选对 → 评分与修正确定偏好 → 按所选类型分别导出。"
        return "本节点生成并评审候选对，保留偏好与 AI 反馈，再按所选类型分别导出。"
    if node == "review":
        return "比较同一问题的两份回答；未达标时在本节点修正并重新评分，通过后进入打包。"
    return "通过检查的偏好对与 AI 反馈按目标分别导出；同一候选可复用，不重复生成。"


def preference_details_html(targets, *, language="zh"):
    """Compact, target-specific outputs inside the existing node inspector."""
    descriptions = {
        "dpo": "同一问题的优选与对照回答，供 DPO 训练使用。",
        "orpo": "同一问题的优选与对照回答，供 ORPO 训练使用。",
        "rlaif": "两份回答的排名、正确性分、各项评分与 AI 评语。",
    }
    fields = {
        "dpo": "prompt · chosen · rejected",
        "orpo": "prompt · chosen · rejected",
        "rlaif": "prompt · responses · criterion",
    }
    rows = []
    for target in selected_preferences(targets):
        label = html.escape(translate_label(PREFERENCE_LABELS[target], language))
        description = html.escape(translate_label(descriptions[target], language))
        rows.append(f'<div class="df-preference-output df-preference-{target}">'
                    f'<span class="df-preference-mark">{target.upper()}</span><div>'
                    f'<strong>{label}</strong><p>{description}</p>'
                    f'<small><code>{target}.jsonl</code> · {fields[target]}</small></div></div>')
    if not rows:
        return ""
    return '''<style>
    .df-preference-outputs{display:grid;gap:0;margin:4px 0 12px;border:1px solid #dce7f5;
      border-radius:12px;background:linear-gradient(120deg,#f8fbff,#fff);overflow:hidden;}
    .df-preference-output{display:flex;gap:12px;padding:13px 14px;align-items:flex-start;}
    .df-preference-output+.df-preference-output{border-top:1px solid #e4edf7;}
    .df-preference-mark{display:inline-flex;align-items:center;justify-content:center;flex:0 0 52px;
      min-height:32px;margin-top:2px;border-radius:9px;background:#e5efff;color:#2860ab;
      box-shadow:inset 0 1px 0 #fff,0 2px 5px #c4d6f02e;font-size:11px;font-weight:800;letter-spacing:.4px;}
    .df-preference-orpo .df-preference-mark{background:#eee8ff;color:#7351b8;}
    .df-preference-rlaif .df-preference-mark{background:#dff3ef;color:#207e6d;}
    .df-preference-output>div{min-width:0;}
    .df-preference-output strong{font-size:13px;color:#243b58;}
    .df-preference-output p{font-size:12px;line-height:1.6;color:#5e728c;margin:4px 0;}
    .df-preference-output small{font-size:10px;line-height:1.6;color:#73859c;overflow-wrap:anywhere;}
    .df-preference-output code{font-size:11px;color:#34577d;background:transparent;padding:0;}
    </style><div class="df-preference-outputs">''' + "".join(rows) + "</div>"
