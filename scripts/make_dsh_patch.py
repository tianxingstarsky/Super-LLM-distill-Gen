"""生成 dsh 补丁文件（cordis.yml，含本机绝对路径，不入库）。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "dsh-dataforge"


def main() -> None:
    ts_path = (PLUGIN / "src" / "dataforge.ts").resolve()
    content = f"# 由 make_dsh_patch.py 生成（本机绝对路径，勿提交）\n- insert:\n    - id: dataforge\n      name: '{ts_path.as_posix()}'\n"
    (PLUGIN / "cordis.yml").write_text(content, encoding="utf-8")
    print(f"written: {PLUGIN / 'cordis.yml'}")
    print("运行前设置：")
    print(f"  export DF_ROOT={ROOT.as_posix()}")
    print(f"  export DF_PYTHON={(ROOT / '.venv' / 'Scripts' / 'python.exe').as_posix()}")
    print("然后在 components/deepseek-harness 下：pnpm dsh web --patch <DF_ROOT>/plugins/dsh-dataforge/cordis.yml")


if __name__ == "__main__":
    main()
