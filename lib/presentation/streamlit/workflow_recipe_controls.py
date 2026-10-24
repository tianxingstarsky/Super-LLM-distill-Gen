"""Download the frozen recipe without rendering nested review records."""
import json

import streamlit as st


def render_recipe_download(recipe, run_id):
    st.caption("下载本次固定参数、节点提示词与返工原稿。")
    st.download_button("下载完整配方 JSON",
        lambda: json.dumps(recipe, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{run_id}-recipe.json", mime="application/json",
        key=f"workflow-recipe-download:{run_id}", on_click="ignore", width="stretch")
