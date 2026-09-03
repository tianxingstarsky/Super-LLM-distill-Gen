"""多模态图文数据管线：图片 → VL 描述 → 问答/多轮对话 → VL 一致性校验。

视觉引擎：deepseek-v4-flash-vision-exp（OpenAI 兼容 image_url，本地图片转 data URI）。
质量门：vision.consistency 用 VL 模型对照原图校验每个回答，图片中不存在的
内容（含常识正确但与图不符）一律驳回——多模态防幻觉关键闸门。
"""
from __future__ import annotations

import base64
import hashlib
import pathlib
from typing import Any, Dict, List

from lib.llm_client import parse_json_robust
from lib.prompts import get, render

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp"}


def encode_image(path: str | pathlib.Path) -> str:
    """本地图片 → base64 data URI（OpenAI 兼容 image_url）。"""
    path = pathlib.Path(path)
    ext = path.suffix.lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"不支持的图片类型 {ext}（支持 {sorted(IMAGE_EXTS)}）")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{MIME[ext]};base64,{data}"


def _image_message(prompt: str, image_path: str) -> Dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": encode_image(image_path)}},
        ],
    }


def _chat_image_json(client: Any, prompt: str, image_path: str | None, temperature: float) -> Dict[str, Any]:
    """VL 调用（严格 JSON）：有图传图（一致性校验用），无图纯文本。"""
    message = _image_message(prompt, image_path) if image_path else {"role": "user", "content": prompt}
    out = client.chat([message], max_tokens=None, temperature=temperature, thinking=False, json_mode=True)
    return parse_json_robust(out)


def image_to_samples(
    client: Any,
    image_path: str | pathlib.Path,
    qa_per_image: int = 2,
    manifest: set[str] | None = None,
) -> Dict[str, Any]:
    """单张图片 → 图文 SFT 样本（问答 + 3 轮对话，全部经一致性校验）。"""
    manifest = manifest if manifest is not None else set()
    image_path = str(image_path)
    stats: Dict[str, Any] = {"qa_kept": 0, "qa_rejected": 0, "chat_kept": False, "errors": 0}
    samples: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    try:
        caption = _chat_image_json(client, render(get("vision.caption")), image_path, 0.3)
        caption_text = caption.get("caption", "")
        if len(caption_text) < 30:
            raise ValueError(f"caption 过短: {caption_text[:60]!r}")

        # 问答
        qa = _chat_image_json(client, render(get("vision.qa_gen"), caption=caption_text, n=qa_per_image), None, 0.8)
        for pair in qa.get("qa", []):
            question = str(pair.get("question", "")).strip()
            answer = str(pair.get("answer", "")).strip()
            if not question or not answer:
                continue
            qid = f"{hashlib.sha256(caption_text.encode()).hexdigest()[:12]}:{hashlib.sha256(question.encode()).hexdigest()[:12]}"
            if qid in manifest:
                continue
            manifest.add(qid)
            check = _chat_image_json(client, render(get("vision.consistency"), question=question, answer=answer), image_path, 0.2)
            if not check.get("keep", False):
                stats["qa_rejected"] += 1
                rejected.append({"question": question[:80], "hallucinated": check.get("hallucinated", [])})
                continue
            stats["qa_kept"] += 1
            samples.append({
                "id": f"vision-{qid}",
                "source": "vision",
                "type": "sft",
                "images": [image_path],
                "caption": caption_text,
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
            })

        # 3 轮对话
        chat = _chat_image_json(client, render(get("vision.chat_gen"), caption=caption_text), None, 0.8)
        turns = chat.get("turns", [])
        if len(turns) == 3:
            messages: List[Dict[str, str]] = []
            all_consistent = True
            for turn in turns:
                q = str(turn.get("user", "")).strip()
                a = str(turn.get("assistant", "")).strip()
                if not q or not a:
                    all_consistent = False
                    break
                check = _chat_image_json(client, render(get("vision.consistency"), question=q, answer=a), image_path, 0.2)
                if not check.get("keep", False):
                    all_consistent = False
                    rejected.append({"question": q[:80], "hallucinated": check.get("hallucinated", [])})
                    break
                messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
            if all_consistent:
                stats["chat_kept"] = True
                samples.append({
                    "id": f"vision-chat-{hashlib.sha256(caption_text.encode()).hexdigest()[:16]}",
                    "source": "vision",
                    "type": "sft",
                    "images": [image_path],
                    "caption": caption_text,
                    "messages": messages,
                })
    except Exception as e:  # noqa: BLE001
        stats["errors"] += 1
        rejected.append({"error": str(e)[:200]})

    stats["samples"] = len(samples)
    return {"samples": samples, "rejected": rejected, "stats": stats}


def run(client: Any, image_dir: str | pathlib.Path, qa_per_image: int = 2, limit: int = 5) -> Dict[str, Any]:
    image_dir = pathlib.Path(image_dir)
    files = [p for p in sorted(image_dir.iterdir()) if p.suffix.lower() in IMAGE_EXTS][:limit]
    manifest: set[str] = set()
    all_samples: List[Dict[str, Any]] = []
    all_rejected: List[Dict[str, Any]] = []
    total = {"images": len(files), "qa_kept": 0, "qa_rejected": 0, "chat_kept": 0, "errors": 0}
    for f in files:
        result = image_to_samples(client, f, qa_per_image, manifest)
        all_samples += result["samples"]
        all_rejected += result["rejected"]
        s = result["stats"]
        print(f" ✔ {f.name}: QA 留 {s['qa_kept']}/驳 {s['qa_rejected']}；对话 {'留' if s['chat_kept'] else '驳'}")
        total["qa_kept"] += s["qa_kept"]
        total["qa_rejected"] += s["qa_rejected"]
        total["chat_kept"] += int(s["chat_kept"])
        total["errors"] += s["errors"]
    return {"samples": all_samples, "rejected": all_rejected, "stats": total}
