"""Node reasoning controls driven by declared model capabilities."""
from copy import deepcopy

import streamlit as st

from lib.domain.model_reasoning import (
    STANDARD_REASONING_SCHEMA, STANDARD_REASONING_EFFORTS, standard_reasoning,
    resolve_provider_reasoning, validate_reasoning,
)
from lib.presentation.streamlit.i18n import UntranslatedText, translate_label


REASONING_ERRORS = {
    "invalid_model_reasoning": "思考配置无效，请在节点重新选择思考方式。",
    "invalid_model_reasoning_effort": "思考强度无效，请选择模型声明支持的档位。",
    "invalid_model_reasoning_budget": "思考预算至少为 1024 tokens，且必须小于单次输出上限。",
    "model_reasoning_budget_exceeds_output": "思考预算必须小于单次输出上限，请调整节点配置。",
    "model_reasoning_effort_unsupported": "当前模型不支持所选思考强度，请重新获取模型能力或选择自动。",
    "model_reasoning_disable_unsupported": "当前模型不支持关闭思考，请选择自动或已支持的思考档位。",
    "model_reasoning_budget_unsupported": "当前模型协议不支持思考预算，请选择自动或已支持的思考档位。",
    "model_reasoning_effort_required": "当前模型需要明确的思考强度，请选择已支持的档位。",
    "model_reasoning_format_unknown": "尚未确认模型的思考参数格式，请获取模型能力或选择自动。",
    "model_reasoning_parameter_unsupported": "模型服务拒绝了思考参数。已保留配置，请重新获取模型能力或选择自动。",
}
REASONING_ERRORS.update({
    "invalid_standard_reasoning": REASONING_ERRORS["invalid_model_reasoning"],
    "invalid_standard_reasoning_effort": REASONING_ERRORS["invalid_model_reasoning"],
    "invalid_reasoning_provider_mapping": REASONING_ERRORS["model_reasoning_format_unknown"],
    "reasoning_provider_mapping_required": REASONING_ERRORS["model_reasoning_format_unknown"],
})

MODE_LABELS = {"auto": "自动（服务默认）", "enabled": "开启思考", "disabled": "关闭思考",
               "saved": "保留已保存配置"}

CONTROL_COPY = {
    "平台 {effort} → 实际模型设置：{provider}": "Platform {effort} → Actual model setting: {provider}",
    "共用模型设置：{groups}。平台五档不代表模型原生支持五档。":
        "Shared model settings: {groups}. The platform choices are not five native model levels.",
    "保留已保存的原生思考配置；选择开启思考后才切换为平台五档。":
        "Keep the saved native reasoning configuration. Select Enable reasoning to switch to the platform's five levels.",
}


def reasoning_label(text, language=None):
    language = language or st.session_state.get("ui_language", "zh")
    if language == "en" and text in CONTROL_COPY:
        return UntranslatedText(CONTROL_COPY[text])
    return UntranslatedText(translate_label(text, language))


def _key(prefix, backend, model):
    return f"{prefix}:reasoning:{backend}:{model}"


def reasoning_summary(value, language=None):
    language = language or st.session_state.get("ui_language", "zh")
    if isinstance(value, dict) and ("backend" in value or "reasoning" in value):
        value = value.get("reasoning")
    choice = validate_reasoning(value)
    result = translate_label(MODE_LABELS[choice["mode"]], language)
    if choice.get("effort"):
        result += " · " + choice["effort"]
    if choice.get("budget_tokens"):
        result += " · " + str(choice["budget_tokens"]) + " tokens"
    return UntranslatedText(result)


def set_reasoning_widgets(prefix, binding):
    key = _key(prefix, binding["backend"], binding["model"])
    value = deepcopy(binding.get("reasoning"))
    st.session_state[key + ":value"] = value
    choice = validate_reasoning(value)
    standard = choice.get("schema") == STANDARD_REASONING_SCHEMA
    st.session_state[key + ":mode"] = "saved" if choice["mode"] == "enabled" and not standard else choice["mode"]
    st.session_state[key + ":standard_ui"] = True
    for field in ("effort", "budget_tokens"):
        st.session_state.pop(key + ":" + field, None)
        if standard and field in choice:
            st.session_state[key + ":" + field] = choice[field]


def _standard_choice(effort, info, output_tokens, api_format, previous=None):
    mapping = (previous.get("provider_mapping") if isinstance(previous, dict)
               and previous.get("schema") == STANDARD_REASONING_SCHEMA else None)
    return standard_reasoning(effort, model_info=info, api_format=api_format,
                              max_output_tokens=output_tokens, provider_mapping=mapping)


def selected_reasoning(prefix, backend, model, info, output_tokens, *, saved=None):
    """Read callbacks before a node/model change can remove widget state."""
    key = _key(prefix, backend, model)
    if not any(info.get(field) is not None for field in ("reasoning_efforts", "reasoning_format", "reasoning_disabled")):
        info = st.session_state.get(key + ":capabilities", info)
    previous = st.session_state.get(key + ":value", saved)
    mode = st.session_state.get(key + ":mode")
    if mode is None or mode == "saved":
        value = deepcopy(previous)
    elif mode == "auto":
        value = None
    elif mode == "disabled":
        value = {"mode": "disabled"}
    else:
        standard = isinstance(previous, dict) and previous.get("schema") == STANDARD_REASONING_SCHEMA
        if previous and not standard and not st.session_state.get(key + ":standard_ui"):
            value = deepcopy(previous)
        else:
            default = previous["effort"] if standard else "medium"
            effort = st.session_state.get(key + ":effort", default)
            if effort not in STANDARD_REASONING_EFFORTS:
                effort = default
            value = _standard_choice(effort, info, output_tokens,
                st.session_state.get(key + ":api_format", "chat"), previous)
    if value is not None:
        value = validate_reasoning(value, max_output_tokens=output_tokens)
    st.session_state[key + ":value"] = deepcopy(value)
    return value


def _mapping_caption(choice):
    actual = resolve_provider_reasoning(choice)
    st.caption(UntranslatedText(reasoning_label("平台 {effort} → 实际模型设置：{provider}").format(
        effort=choice["effort"], provider=reasoning_summary(actual))))
    groups = []
    for effort in STANDARD_REASONING_EFFORTS:
        native = choice["provider_mapping"][effort]
        group = next((group for group in groups if group[0] == native), None)
        if group is None:
            groups.append((native, [effort]))
        else:
            group[1].append(effort)
    collapsed = [" / ".join(levels) + " → " + reasoning_summary(native)
                 for native, levels in groups if len(levels) > 1]
    if collapsed:
        st.caption(UntranslatedText(reasoning_label(
            "共用模型设置：{groups}。平台五档不代表模型原生支持五档。").format(groups="; ".join(collapsed))))


def render_reasoning_controls(prefix, backend, model, info, output_tokens, *, saved=None,
                              api_format="chat", on_change=None, args=(), application=None, endpoint=None):
    key = _key(prefix, backend, model)
    st.session_state[key + ":capabilities"] = deepcopy(info)
    st.session_state[key + ":api_format"] = api_format
    current = st.session_state.get(key + ":value", saved)
    choice = validate_reasoning(current)
    standard = choice.get("schema") == STANDARD_REASONING_SCHEMA
    legacy_enabled = choice["mode"] == "enabled" and not standard
    try:
        _standard_choice("medium", info, output_tokens, api_format, current)
        can_enable = True
    except ValueError:
        can_enable = False
    options = ["auto", *(["enabled"] if can_enable else []),
               *(["disabled"] if info.get("reasoning_disabled") is True else [])]
    supported_saved = choice["mode"] in options and not legacy_enabled
    if current and choice["mode"] != "auto" and not supported_saved:
        options.append("saved")
    selected = choice["mode"] if supported_saved else "saved"
    if (key + ":mode" not in st.session_state or st.session_state[key + ":mode"] not in options
            or (legacy_enabled and not st.session_state.get(key + ":standard_ui"))):
        st.session_state[key + ":mode"] = selected
    if not st.session_state.get(key + ":standard_ui") and not standard:
        st.session_state.pop(key + ":effort", None)
    st.session_state[key + ":standard_ui"] = True
    st.session_state.setdefault(key + ":value", deepcopy(current))
    mode = st.selectbox("模型思考", options, key=key + ":mode",
                        format_func=lambda item: translate_label(MODE_LABELS[item], st.session_state.get("ui_language", "zh")),
                        on_change=on_change, args=args,
                        help="自动不发送思考覆盖参数，沿用服务默认设置。")
    if mode == "enabled":
        default = choice["effort"] if standard else "medium"
        if (key + ":effort" not in st.session_state
                or st.session_state[key + ":effort"] not in STANDARD_REASONING_EFFORTS):
            st.session_state[key + ":effort"] = default
        st.selectbox("思考强度", list(STANDARD_REASONING_EFFORTS), key=key + ":effort",
                     on_change=on_change, args=args, format_func=UntranslatedText)
        _mapping_caption(_standard_choice(st.session_state[key + ":effort"], info,
                                         output_tokens, api_format, current))
    elif mode == "saved":
        st.caption(reasoning_summary(current))
        if legacy_enabled:
            st.caption(reasoning_label("保留已保存的原生思考配置；选择开启思考后才切换为平台五档。"))
        else:
            st.caption("已保存的思考配置尚未获得当前能力声明确认。配置予以保留，可选择自动重置。")
    elif not can_enable:
        st.caption("尚未获取可调思考能力。可在模型检测中获取模型列表；默认沿用服务设置。")
    st.caption("思考强度控制模型的推理投入，不等同于答案长度；答案详略由提示词与输出上限共同影响。")
    if application is not None and hasattr(application, "discover_models"):
        from lib.presentation.streamlit.workflow_model_capabilities import render_model_discovery
        render_model_discovery(application, backend, endpoint or {}, key=key + ":discover", label="更新模型能力")
    return selected_reasoning(prefix, backend, model, info, output_tokens, saved=saved)
