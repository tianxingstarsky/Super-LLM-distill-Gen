"""Expose the local guided-answer CLI beside the selected SFT model."""
import json
import re
from urllib.parse import urlsplit

import streamlit as st


def _powershell_literal(text):
    # PowerShell also treats typographic apostrophes as quote delimiters.
    return "'" + re.sub("['‘’‚‛]", lambda match: match[0] * 2, text) + "'"


def render_guided_answer_cli(binding, endpoints, *, key):
    en = st.session_state.get("ui_language") == "en"
    with st.expander("Local guided training data (CLI)" if en else "本地指导训练资料（CLI）"):
        endpoint = endpoints.get(binding.get("backend"), {})
        model = binding.get("model", "")
        info = endpoint.get("model_info", {}).get(model, {})
        url = endpoint.get("base_url", "")
        if (not model or urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}
                or endpoint.get("api_format", "chat") != "chat" or info.get("reasoning_format") != "llama_cpp_qwen"):
            st.caption("Select a local llama.cpp service with the native Qwen chat template as the SFT generation model to build the command."
                       if en else "在 SFT 生成模型中选择本机 llama.cpp 的 Qwen 原生聊天模板服务，即可取得运行命令。")
            return
        context, output = binding["context_window_tokens"], binding["max_output_tokens"]
        if context < 90000 or output < 32768:
            st.info("Guided generation requires at least 90,000 tokens per slot and a 32,768-token output limit. Check the server slots and update the node limits."
                    if en else "指导生成需要每槽至少 90,000 tokens、输出上限至少 32,768 tokens。请核对服务槽位容量并调整本节点配置。")
            return
        sample = {"id": "notebook-01", "question": "Mia has six blank pages and uses two. How many blank pages remain?",
                  "gold": {"kind": "number", "value": "4"}}
        st.caption("Save the example in the repository root. Each JSONL line holds one question. With gold set to null, provide source_reasoning for the model to expand into an answer. These records go to unverified.jsonl."
                   if en else "将示例保存到项目根目录，JSONL 每行一题。若 gold 为 null，请用 source_reasoning 提供有来源的推理。模型据此补写答案，记录进入 unverified.jsonl。")
        sample_line = json.dumps(sample, ensure_ascii=False)
        st.code(sample_line, language="json", wrap_lines=True)
        st.download_button("Download example JSONL" if en else "下载示例 JSONL", sample_line + "\n",
                           file_name="guided-example.jsonl", mime="application/x-ndjson", key=key)
        st.caption("Run in PowerShell from the repository root with the project environment active. The selected model answers with thinking disabled and reviews in a separate request with thinking enabled."
                   if en else "在项目根目录激活项目环境后，用 PowerShell 执行。所选模型关闭思考作答，再以独立请求开启思考担任指导员。")
        workers = min(4, max(1, int(info.get("max_concurrency") or 1)))
        command = ["python -m lib.guided_answers", "--input guided-example.jsonl --output .dataforge/guided-example",
                   f"--student-url {_powershell_literal(url)} --student-model {_powershell_literal(model)}",
                   f"--context-tokens {context} --max-tokens {output} --workers {workers} --coach-thinking"]
        credential = endpoint.get("api_key", {}).get("source", "")
        if credential.startswith("env:") or credential == "configs/backends.local.yaml":
            name = credential.removeprefix("env:") if credential.startswith("env:") else "GUIDED_MODEL_API_KEY"
            command.append(f"--student-api-key-env {_powershell_literal(name)} --coach-api-key-env {_powershell_literal(name)}")
            st.caption(("Set the credential in this shell environment variable first: " if en else "运行前在当前终端设置凭据环境变量 ") + name)
        st.code(" `\n  ".join(command), language="powershell", wrap_lines=True)
        st.caption("In the output directory, summary.json contains the counts and accepted.jsonl contains accepted records. Use a fresh output directory when changing the input or configuration."
                   if en else "输出目录中的 summary.json 记录各类数量，accepted.jsonl 保存通过核验的训练记录。更换输入或配置时请使用新的输出目录。")
