"""Expose the local guided-answer CLI beside the selected SFT model."""
import json
import re
from urllib.parse import urlsplit

import streamlit as st


def _powershell_literal(text):
    # PowerShell also treats typographic apostrophes as quote delimiters.
    return "'" + re.sub("['‘’‚‛]", lambda match: match[0] * 2, text) + "'"


def _local_qwen(endpoint, model):
    return (urlsplit(endpoint.get("base_url", "")).hostname in {"127.0.0.1", "localhost", "::1"}
            and endpoint.get("api_format", "chat") == "chat"
            and endpoint.get("model_info", {}).get(model, {}).get("reasoning_format") == "llama_cpp_qwen")


def render_guided_answer_cli(binding, endpoints, *, key, reviewer=None):
    en = st.session_state.get("ui_language") == "en"
    with st.expander("Local guided training data (CLI)" if en else "本地指导训练资料（CLI）"):
        endpoint = endpoints.get(binding.get("backend"), {})
        model = binding.get("model", "")
        info = endpoint.get("model_info", {}).get(model, {})
        url = endpoint.get("base_url", "")
        if not model or not _local_qwen(endpoint, model):
            st.caption("Select a local llama.cpp service with the native Qwen chat template as the SFT generation model to build the command."
                       if en else "在 SFT 生成模型中选择本机 llama.cpp 的 Qwen 原生聊天模板服务，即可取得运行命令。")
            return
        context, output = binding["context_window_tokens"], binding["max_output_tokens"]
        if context < 90000 or output < 32768:
            st.info("Guided generation requires at least 90,000 tokens per slot and a 32,768-token output limit. Check the server slots and update the node limits."
                    if en else "指导生成需要每槽至少 90,000 tokens、输出上限至少 32,768 tokens。请核对服务槽位容量并调整本节点配置。")
            return
        sample = {"id": "notebook-01", "question": "Mia has six blank pages and uses two. How many blank pages remain?",
                  "gold": {"kind": "number", "value": "4"},
                  "source_reasoning": "Two of the six blank pages are used, leaving 6 - 2 = 4 blank pages."}
        st.caption("Save the example in the repository root. Each JSONL line holds one question. With gold set to null, provide source_reasoning for the model to expand into an answer. These records go to unverified.jsonl."
                   if en else "将示例保存到项目根目录，JSONL 每行一题。若 gold 为 null，请用 source_reasoning 提供有来源的推理。模型据此补写答案，记录进入 unverified.jsonl。")
        sample_line = json.dumps(sample, ensure_ascii=False)
        st.code(sample_line, language="json", wrap_lines=True)
        st.download_button("Download example JSONL" if en else "下载示例 JSONL", sample_line + "\n",
                           file_name="guided-example.jsonl", mime="application/x-ndjson", key=key)
        student = (binding["backend"], model)
        options = [(name, item) for name, ep in endpoints.items()
                   for item in ep.get("model_info", {}) if _local_qwen(ep, item)]
        preferred = ((reviewer or {}).get("backend"), (reviewer or {}).get("model"))
        teacher = st.selectbox("Local tutor model" if en else "本机指导教师模型", options,
                               index=options.index(preferred if preferred in options else student),
                               format_func=lambda pair: " · ".join(pair), key=key + ":teacher")
        teacher_thinking = st.checkbox("Enable native thinking for the tutor" if en else "启用指导教师原生思考",
                                       value=False, key=key + ":thinking")
        predict_behavior = st.toggle(
            "Guide the student's next action" if en else "引导学生执行下一步",
            key=key + ":behavior",
            help=("After a hint is released, the tutor model uses a fresh chat to predict what the student "
                  "will do. The next disclosure can use the actual response to adjust the guidance."
                  if en else "发布提示后，教师模型另开会话，预测学生会怎样理解并执行。下一轮披露时，教师可对照预测和学生实际作答调整引导。"),
        )
        if predict_behavior:
            st.caption("Each guided turn adds one local prediction call. A correct independent answer skips it."
                       if en else "每次需要引导学生续写时，增加一次本地预测调用。首答合格直接结束。")
        st.caption("Run in PowerShell from the repository root with the project environment active. The student answers with native thinking disabled."
                   if en else "在项目根目录激活项目环境后，用 PowerShell 执行。学生关闭原生思考作答。")
        workers = min(4, max(1, int(info.get("max_concurrency") or 1)))
        teacher_endpoint = endpoints[teacher[0]]
        thinking_flag = "--coach-thinking" if teacher_thinking else "--no-coach-thinking"
        output_dir = ".dataforge/guided-behavior-v4108" if predict_behavior else ".dataforge/guided-example"
        command = ["python -m lib.guided_answers", f"--input guided-example.jsonl --output {output_dir}",
                   f"--student-url {_powershell_literal(url)} --student-model {_powershell_literal(model)}",
                   f"--coach-url {_powershell_literal(teacher_endpoint['base_url'])} --coach-model {_powershell_literal(teacher[1])}",
                   f"--context-tokens {context} --max-tokens {output} --workers {workers} {thinking_flag}"]
        if predict_behavior:
            command.append("--protocol-version 4.10.8")
        variables = []
        for role, ep in (("student", endpoint), ("coach", teacher_endpoint)):
            credential = ep.get("api_key", {}).get("source", "")
            if credential.startswith("env:") or credential == "configs/backends.local.yaml":
                name = credential.removeprefix("env:") if credential.startswith("env:") else (
                    "GUIDED_MODEL_API_KEY" if role == "student" or teacher[0] == student[0] else "GUIDED_TUTOR_API_KEY")
                command.append(f"--{role}-api-key-env {_powershell_literal(name)}")
                variables.append(name)
        if variables:
            st.caption(("Set these shell environment variables before running: " if en else "运行前在当前终端设置凭据环境变量 ")
                       + ", ".join(dict.fromkeys(variables)))
        st.code(" `\n  ".join(command), language="powershell", wrap_lines=True)
        st.caption("Train from training-targets.jsonl. The output directory also contains summary.json with counts and accepted.jsonl with accepted case records. Use a fresh output directory when changing inputs or settings."
                   if en else "训练时读取输出目录中的 training-targets.jsonl。summary.json 记录数量，accepted.jsonl 保存通过核验的案例档案。更换输入或配置时请使用新的输出目录。")
