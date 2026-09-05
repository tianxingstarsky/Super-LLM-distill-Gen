"""生成 dsh 补丁文件（cordis.yml + team.yml，含本机绝对路径，不入库）。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "dsh-dataforge"
HARNESS = ROOT / "components" / "deepseek-harness"


def main() -> None:
    ts_path = (PLUGIN / "src" / "dataforge.ts").resolve()
    # 插件名必须是 file:// URL（Windows 绝对路径原生 import 不认；相对路径 loader 才会自动转）
    uri = ts_path.as_uri()
    content = f"# 由 make_dsh_patch.py 生成（本机绝对路径，勿提交）\n- insert:\n    - id: dataforge\n      name: '{uri}'\n"
    (PLUGIN / "cordis.yml").write_text(content, encoding="utf-8")
    print(f"written: {PLUGIN / 'cordis.yml'}")

    # 审核小队补丁：团队包同样按 file:// URL 挂载（包名挂载会被
    # plugin-package-inventory-deepseek 拒绝——"cannot resolve active package"，实测确认）
    agent_team = (HARNESS / "packages" / "experimental" / "agent-team" / "src" / "index.ts").resolve()
    tool_team = (HARNESS / "packages" / "experimental" / "tool-agent-team" / "src" / "index.ts").resolve()
    template = (PLUGIN / "team.example.yml").read_text(encoding="utf-8")
    team = (template
            .replace("{{AGENT_TEAM_URI}}", agent_team.as_uri())
            .replace("{{TOOL_AGENT_TEAM_URI}}", tool_team.as_uri()))
    (PLUGIN / "team.yml").write_text(team, encoding="utf-8")
    print(f"written: {PLUGIN / 'team.yml'}")
    print("运行前设置：")
    print(f"  export DF_ROOT={ROOT.as_posix()}")
    print(f"  export DF_PYTHON={(ROOT / '.venv' / 'Scripts' / 'python.exe').as_posix()}")
    print("然后在 components/deepseek-harness 下：pnpm dsh web --patch <DF_ROOT>/plugins/dsh-dataforge/cordis.yml")
    print("（审核小队再加一个 --patch <DF_ROOT>/plugins/dsh-dataforge/team.yml）")


if __name__ == "__main__":
    main()
