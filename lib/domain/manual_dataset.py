"""Portable, explicitly unreviewed manual text and image samples."""
from __future__ import annotations

from datetime import datetime
import re

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_EDGE = 16_000
IMAGE_TYPES = {".png": ("PNG", "image/png"), ".jpg": ("JPEG", "image/jpeg"),
               ".jpeg": ("JPEG", "image/jpeg"), ".webp": ("WEBP", "image/webp")}
_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid_manual_dataset_id")
    return value


def dataset_name(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 100
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError("invalid_manual_dataset_name")
    return value.strip()


def sample_text(value: str, *, system: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > (20_000 if system else 100_000)
            or "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
            or (not system and not value.strip())):
        raise ValueError("invalid_manual_dataset_text")
    return value.strip()


def attachment_name(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 240
            or value in {".", ".."} or value[-1] in {" ", "."}
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or char in '/\\<>:"|?*' for char in value)):
        raise ValueError("invalid_manual_dataset_attachments")
    if "." not in value or ("." + value.rsplit(".", 1)[1].lower()) not in IMAGE_TYPES:
        raise ValueError("invalid_manual_dataset_image")
    return value


def timestamp(value: str) -> str:
    try:
        valid = isinstance(value, str) and len(value) <= 64 and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("invalid_manual_dataset_storage")
    return value


def media_record(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_manual_dataset_storage")
    media_id = identifier(value.get("id"))
    name = attachment_name(value.get("name"))
    extension = "." + name.rsplit(".", 1)[1].lower()
    extension = ".jpg" if extension == ".jpeg" else extension
    if (value.get("kind") != "image" or value.get("mime_type") != IMAGE_TYPES[extension][1]
            or value.get("path") != f"media/{media_id}{extension}"
            or not isinstance(value.get("sha256"), str) or not _SHA.fullmatch(value["sha256"])
            or type(value.get("size")) is not int or not 0 < value["size"] <= MAX_IMAGE_BYTES):
        raise ValueError("invalid_manual_dataset_storage")
    return {key: value[key] for key in ("id", "name", "mime_type", "kind", "path", "sha256", "size")}


def sample_record(sample_id: str, question: str, answer: str, system: str,
                  media: list[dict], created_at: str) -> dict:
    sample_id = identifier(sample_id)
    question, answer, system = sample_text(question), sample_text(answer), sample_text(system, system=True)
    if (not isinstance(media, list) or len(media) > MAX_IMAGES
            or sum(item.get("size", 0) for item in media if isinstance(item, dict)) > MAX_ATTACHMENTS_BYTES):
        raise ValueError("invalid_manual_dataset_attachments")
    media = [media_record(item) for item in media]
    if len({item["id"] for item in media}) != len(media):
        raise ValueError("invalid_manual_dataset_storage")
    messages = [{"role": "system", "content": system}] if system else []
    content = ([{"type": "image_url", "image_url": {"url": item["path"]}} for item in media]
               + [{"type": "text", "text": question}]) if media else question
    messages.extend(({"role": "user", "content": content}, {"role": "assistant", "content": answer}))
    return {"id": sample_id, "question": question, "answer": answer, "system": system,
            "media": media, "messages": messages,
            "metadata": {"origin": "manual", "review_status": "unreviewed"},
            "created_at": timestamp(created_at)}


def validate_sample(value: dict, expected_id: str) -> dict:
    try:
        if not isinstance(value, dict) or value.get("id") != expected_id:
            raise ValueError
        clean = sample_record(value["id"], value["question"], value["answer"],
                              value["system"], value["media"], value["created_at"])
        if value.get("metadata") != clean["metadata"] or value.get("messages") != clean["messages"]:
            raise ValueError
        return clean
    except (ValueError, KeyError, TypeError):
        raise ValueError("invalid_manual_dataset_storage") from None
