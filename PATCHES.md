# 上游补丁记录（"不造轮子"原则下的最小修补）

原则：submodule 内不改代码；确属硬阻塞时做最小补丁，记录于此，可用
`git apply` 复现（补丁上下文基于锁定提交）。

## PATCH-1：OpenCUA cot-generator 支持 DeepSeek 端点
- 文件：components/opencua/data/cot-generate/gen_cot.py（process_traj 内 base_url 路由）
- 内容：新增 `elif "deepseek" in model.lower(): base_url = "https://api.deepseek.com/v1"`
- 原因：上游硬编码 claude/gpt/gemini/qwen 四家端点；本机视觉引擎为 deepseek-v4-flash-vision-exp
- 复现：
```bash
cd components/opencua
git apply <<'EOF'
diff --git a/data/cot-generate/gen_cot.py b/data/cot-generate/gen_cot.py
--- a/data/cot-generate/gen_cot.py
+++ b/data/cot-generate/gen_cot.py
@@ -246,6 +246,8 @@
     elif "qwen" in model.lower():
         base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
+    elif "deepseek" in model.lower():
+        base_url = "https://api.deepseek.com/v1"
     else:
         raise ValueError(f"Unsupported model: {model}. Please use a valid model name.")
EOF
```
