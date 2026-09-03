"""生成合成 GUI 轨迹 fixture（浅色→深色→保存 三张状态截图 + OpenCUA traj JSONL）。

用途：验证上游 OpenCUA cot-generator 整链（无真实录屏时的端到端测试）。
运行：.venv/Scripts/python.exe scripts/make_gui_fixture.py
"""
from __future__ import annotations

import json
import pathlib

from PIL import Image, ImageDraw, ImageFont

BASE = pathlib.Path(__file__).resolve().parent.parent / "data" / "seeds" / "screenshots" / "gui_fixture"

# 用系统雅黑大字号（PIL 默认位图字体过小过糊，VL 模型读不清导致误判）
_FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
_FONT = ImageFont.truetype(_FONT_PATH, 22)


def draw_screen(dark: bool, saved: bool) -> Image.Image:
    img = Image.new("RGB", (640, 480), (35, 35, 42) if dark else (242, 242, 246))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 40, 580, 440], fill=(28, 28, 34) if dark else (255, 255, 255))
    fg = (235, 235, 235) if dark else (20, 20, 20)
    d.text((80, 60), "设置", font=_FONT, fill=fg)
    d.text((80, 110), "主题：深色" if dark else "主题：浅色", font=_FONT, fill=fg)

    # 深色模式开关（0.85, 0.12）：旁注文字 + 胶囊 + 圆形旋钮
    tx, ty = int(640 * 0.85), int(480 * 0.12)
    d.text((tx - 190, ty - 12), "深色模式: 开" if dark else "深色模式: 关", font=_FONT, fill=fg)
    d.rounded_rectangle([tx - 34, ty - 14, tx + 34, ty + 14], radius=14,
                        fill=(60, 150, 255) if dark else (170, 170, 178))
    knob_x = tx + 16 if dark else tx - 16
    d.ellipse([knob_x - 10, ty - 10, knob_x + 10, ty + 10], fill=(255, 255, 255))

    # 保存按钮（0.85, 0.30）：恒为绿色系按钮
    sx, sy = int(640 * 0.85), int(480 * 0.30)
    d.rounded_rectangle([sx - 70, sy - 20, sx + 70, sy + 20], radius=10,
                        fill=(46, 140, 76) if saved else (38, 96, 58))
    d.text((sx - 46, sy - 13), "已保存" if saved else "保存", font=_FONT, fill=(255, 255, 255))
    return img


def main() -> None:
    img_dir = BASE / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    draw_screen(False, False).save(img_dir / "shot_0.png")
    draw_screen(True, False).save(img_dir / "shot_1.png")
    draw_screen(True, True).save(img_dir / "shot_2.png")
    task = {
        "task_id": "gui-demo-001",
        "instruction": "在设置窗口中切换到深色模式并点击保存",
        "traj": [
            {"image": "shot_0.png", "value": {"code": "pyautogui.click(x=0.85, y=0.12)"}},
            {"image": "shot_1.png", "value": {"code": "pyautogui.click(x=0.85, y=0.30)"}},
        ],
    }
    (BASE / "task.jsonl").write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"fixture ready: {BASE}")


if __name__ == "__main__":
    main()
