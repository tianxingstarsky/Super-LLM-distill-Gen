"""Node reasoning controls driven by declared model capabilities."""
from copy import deepcopy

import streamlit as st

from lib.domain.model_reasoning import effort_levels, validate_reasoning
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

MODE_LABELS = {"auto": "自动（服务默认）", "enabled": "开启思考", "disabled": "关闭思考",
               "saved": "保留已保存配置"}


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
    st.session_state[key + ":mode"] = choice["mode"]
    for field in ("effort", "budget_tokens"):
        st.session_state.pop(key + ":" + field, None)
        if field in choice:
            st.session_state[key + ":" + field] = choice[field]


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
        value = {"mode": "enabled"}
        levels = effort_levels(info.get("reasoning_efforts")) or []
        if info.get("reasoning_format") == "anthropic_budget":
            default = (previous or {}).get("budget_tokens", min(4096, output_tokens - 1))
            value["budget_tokens"] = st.session_state.get(key + ":budget_tokens", max(1024, default))
        elif levels:
            default = info.get("default_reasoning_effort")
            if default not in levels:
                default = levels[0]
            effort = st.session_state.get(key + ":effort", default)
            if effort is not None:
                value["effort"] = effort
        elif (previous or {}).get("mode") == "enabled":
            value.update({field: previous[field] for field in ("effort", "budget_tokens") if field in previous})
    if value is not None:
        value = validate_reasoning(value, max_output_tokens=output_tokens)
    st.session_state[key + ":value"] = deepcopy(value)
    return value


def render_reasoning_controls(prefix, backend, model, info, output_tokens, *, saved=None,
                              api_format="chat", on_change=None, args=(), application=None, endpoint=None):
    key = _key(prefix, backend, model)
    st.session_state[key + ":capabilities"] = deepcopy(info)
    current = st.session_state.get(key + ":value", saved)
    choice = validate_reasoning(current)
    levels = effort_levels(info.get("reasoning_efforts")) or []
    scheme = info.get("reasoning_format")
    budget_mode = api_format == "anthropic" and scheme == "anthropic_budget"
    native_toggle = ((scheme == "deepseek" and api_format in {"chat", "anthropic"})
                     or (scheme == "anthropic_adaptive" and api_format == "anthropic"))
    can_enable = ((bool(levels) and api_format in {"chat", "responses"}) or native_toggle
                  or (budget_mode and output_tokens > 1024))
    if budget_mode and output_tokens <= 1024:
        can_enable = False
    options = ["auto", *(["enabled"] if can_enable else []),
               *(["disabled"] if info.get("reasoning_disabled") is True else [])]
    supported_saved = (choice["mode"] in options
        and ("effort" not in choice or choice["effort"] in levels)
        and ("budget_tokens" not in choice or budget_mode)
        and choice.get("budget_tokens", 0) < output_tokens
        and not (choice["mode"] == "enabled" and budget_mode and "budget_tokens" not in choice)
        and not (choice["mode"] == "enabled" and not native_toggle and not budget_mode and "effort" not in choice))
    if current and choice["mode"] != "auto" and not supported_saved:
        options.append("saved")
    selected = choice["mode"] if supported_saved else "saved"
    if not supported_saved or key + ":mode" not in st.session_state or st.session_state[key + ":mode"] not in options:
        st.session_state[key + ":mode"] = selected
    st.session_state.setdefault(key + ":value", deepcopy(current))
    mode = st.selectbox("模型思考", options, key=key + ":mode",
                        format_func=lambda item: translate_label(MODE_LABELS[item], st.session_state.get("ui_language", "zh")),
                        on_change=on_change, args=args,
                        help="自动不发送思考覆盖参数，沿用服务默认设置。")
    if mode == "enabled" and budget_mode:
        budget = (current or {}).get("budget_tokens", min(4096, output_tokens - 1))
        st.number_input("思考预算（tokens）", min_value=1024, max_value=output_tokens - 1,
                        value=max(1024, budget), step=1024, key=key + ":budget_tokens",
                        on_change=on_change, args=args)
    elif mode == "enabled" and levels:
        effort_options = [None, *levels] if native_toggle else levels
        default = ((current or {}).get("effort") if (current or {}).get("mode") == "enabled"
                   else info.get("default_reasoning_effort"))
        if default not in effort_options:
            default = effort_options[0]
        if key + ":effort" not in st.session_state or st.session_state[key + ":effort"] not in effort_options:
            st.session_state[key + ":effort"] = default
        st.selectbox("思考强度", effort_options, key=key + ":effort", on_change=on_change, args=args,
                     format_func=lambda value: UntranslatedText(value) if value is not None else
                     translate_label("服务默认强度", st.session_state.get("ui_language", "zh")))
    elif mode == "saved":
        st.caption(reasoning_summary(current))
        st.caption("已保存的思考配置尚未获得当前能力声明确认。配置予以保留，可选择自动重置。")
    elif not can_enable:
        st.caption("尚未获取可调思考能力。可在模型检测中获取模型列表；默认沿用服务设置。")
    st.caption("思考强度控制模型的推理投入，不等同于答案长度；答案详略由提示词与输出上限共同影响。")
    if application is not None and hasattr(application, "discover_models"):
        from lib.presentation.streamlit.workflow_model_capabilities import render_model_discovery
        render_model_discovery(application, backend, endpoint or {}, key=key + ":discover", label="更新模型能力")
    return selected_reasoning(prefix, backend, model, info, output_tokens, saved=saved)
