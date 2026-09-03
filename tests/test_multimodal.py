"""多模态管线离线测试（fake client 脚本化 + PIL 合成图 fixture）。"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def fixture_image(tmp_path_factory) -> pathlib.Path:
    from PIL import Image, ImageDraw

    img_dir = tmp_path_factory.mktemp("images")
    img = Image.new("RGB", (320, 240), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 60, 280, 180], fill=(120, 90, 60))  # 棕色书桌
    draw.rectangle([90, 100, 230, 160], fill=(200, 200, 210))  # 银色笔记本
    draw.ellipse([170, 60, 210, 100], fill=(90, 50, 20))  # 咖啡杯
    path = img_dir / "desk.png"
    img.save(path)
    return path


def test_encode_image_roundtrip(fixture_image):
    from lib.multimodal import encode_image

    uri = encode_image(fixture_image)
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 100


class FakeClient:
    """按提示词脚本化：caption→描述；qa_gen→2 对（一条含幻觉）；consistency 判 STYLE 标记。"""

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if isinstance(content, list):
            content = content[0]["text"]
        if "视觉理解专家" in content:
            return json.dumps({
                "caption": "图片中有一张木质书桌，桌上放着一台银色笔记本电脑和一杯咖啡，背景是书架。",
                "objects": ["书桌", "笔记本电脑", "咖啡", "书架"],
                "text_content": "", "scene": "室内",
            }, ensure_ascii=False)
        if "多模态事实校验员" in content:
            answer = content.split("# 待校验回答:")[-1]
            consistent = "HALLUC" not in answer
            return json.dumps({
                "consistent": consistent,
                "hallucinated": [] if consistent else ["打印机"],
                "keep": consistent,
            }, ensure_ascii=False)
        if "多模态数据生成专家" in content and "turns" not in content:
            return json.dumps({"qa": [
                {"question": "桌上有什么？", "answer": "一台笔记本电脑和一杯咖啡。"},
                {"question": "还有什么？", "answer": "还有一台打印机。HALLUC"},
            ]}, ensure_ascii=False)
        # vision.chat_gen
        return json.dumps({"turns": [
            {"user": "图里有什么？", "assistant": "书桌、笔记本电脑和咖啡。"},
            {"user": "笔记本什么颜色？", "assistant": "银色。"},
            {"user": "这大概是哪里？", "assistant": "像书房，背景有书架。"},
        ]}, ensure_ascii=False)


def test_image_to_samples_grounding_gate(fixture_image):
    from lib.multimodal import image_to_samples

    result = image_to_samples(FakeClient(), fixture_image, qa_per_image=2)
    stats = result["stats"]
    assert stats["qa_kept"] == 1       # 含幻觉（打印机）的一条被驳回
    assert stats["qa_rejected"] == 1
    assert stats["chat_kept"] is True  # 3 轮全部通过一致性校验
    assert stats["samples"] == 2
    for s in result["samples"]:
        assert s["source"] == "vision"
        assert s["images"] == [str(fixture_image)]
        assert "caption" in s
    assert result["rejected"][0]["hallucinated"] == ["打印机"]


def test_run_over_directory(fixture_image):
    from lib.multimodal import run

    result = run(FakeClient(), fixture_image.parent, qa_per_image=2, limit=1)
    assert result["stats"]["images"] == 1
    assert result["stats"]["qa_kept"] == 1
