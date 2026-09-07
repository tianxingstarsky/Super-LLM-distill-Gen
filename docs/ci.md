# 持续集成（CI）

本地主套件 126 项 + UI 套件 2 项全绿（Windows 实测）。GitHub Actions 工作流已准备在
`ci/ci.yml.inactive`（内容与 `.github/workflows/ci.yml` 一致）。

**为什么未激活**：当前推送到 GitHub 的 OAuth App 凭据没有 `workflow` scope，
GitHub 拒绝创建/更新 `.github/workflows/*.yml`。激活方式二选一：

```bash
# A. 给授权 App 加 workflow scope 后启用
cp ci/ci.yml.inactive .github/workflows/ci.yml
git add .github/workflows/ci.yml && git commit -m "CI: 启用 GitHub Actions" && git push

# B. 本地持续门禁（无需 GitHub 权限）：提交前跑
python -m pytest -q --tb=short ui_tests          # UI 与 distilabel 分进程，勿合并
```

工作流内容：Ubuntu/Windows 双平台；checkout（含 submodule）→ Python 3.11 →
`pip install -r requirements.txt` → 主套件 → UI 套件；默认只读权限；25 分钟超时；
`NO_PROXY`/`PYTHONIOENCODING` 对齐本地环境。
