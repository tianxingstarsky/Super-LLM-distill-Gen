"""Bounded, deterministic counts of native training records; never a quality score."""
from collections import Counter
import re

from lib.domain.reasoning_fields import REASONING_FIELDS


CHAR_BINS = ((200, "0–200"), (1000, "201–1,000"), (4000, "1,001–4,000"),
             (16000, "4,001–16,000"), (None, "16,001+"))


def text_parts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for block in value:
            if isinstance(block, str):
                yield block
            elif isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}:
                if isinstance(block.get("text"), str):
                    yield block["text"]


def language(parts):
    chinese = latin = 0
    for text in parts:
        chinese += sum(1 for _ in re.finditer(r"[\u3400-\u9fff]", text))
        latin += sum(1 for _ in re.finditer(r"[A-Za-z]", text))
    if not chinese and not latin:
        return "other"
    share = chinese / (chinese + latin)
    return "mixed" if .2 <= share <= .8 else "zh" if share > .8 else "en"


def char_bin(size):
    return next(label for maximum, label in CHAR_BINS if maximum is None or size <= maximum)


def record_features(target, row):
    body, users, reasoning = [], [], []
    turns = 0
    image = bool(row.get("images"))
    tools = bool(row.get("tools"))

    def response(value, role="assistant"):
        nonlocal image, tools
        if isinstance(value, list):
            for item in value:
                response(item, role)
        elif isinstance(value, dict):
            if value.get("type") in {"text", "input_text", "output_text"}:
                body.extend(text_parts([value]))
                return
            actual_role = value.get("role", role)
            if actual_role in {"user", "assistant"}:
                body.extend(text_parts(value.get("content")))
            if actual_role == "assistant":
                for key in REASONING_FIELDS:
                    if value.get(key):
                        reasoning.extend(text_parts(value[key]))
                        break  # Alias fields are one explanation, not multiple traces.
            content = value.get("content")
            if isinstance(content, list):
                image |= any(isinstance(b, dict) and b.get("type") in
                             {"image", "image_url", "input_image"} for b in content)
            tools |= bool(value.get("tool_calls")) or actual_role in {"tool", "function"}
        else:
            body.extend(text_parts(value))

    def prompt(value):
        nonlocal turns
        if isinstance(value, list):
            for message in value:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                if message.get("role") == "user" and not tool_result:
                    turns += 1
                    users.extend(text_parts(content))
                response(message)
        else:
            users.extend(text_parts(value))
            body.extend(text_parts(value))
            turns += bool(value)

    if target in {"sft", "multiturn", "agent", "agent_negative"}:
        prompt(row.get("messages", []))
    elif target == "cpt":
        body.extend(text_parts(row.get("text")))
    elif target in {"dpo", "orpo", "rlaif"}:
        prompt(row.get("prompt"))
        if target == "rlaif":
            for item in row.get("responses", []):
                response(item.get("response") if isinstance(item, dict) else item)
        else:
            response(row.get("chosen"))
            response(row.get("rejected"))
    else:
        prompt(row.get("question"))
        response(row.get("answer"))
        if target == "cot":
            reasoning.extend(text_parts(row.get("reasoning")))
    return body, users, reasoning, turns, image, tools


class Distribution:
    def __init__(self):
        self.rows = self.body_chars = self.reasoning_chars = self.reasoning_rows = 0
        self.images = self.tools = 0
        self.languages = Counter(dict.fromkeys(("zh", "en", "mixed", "other"), 0))
        self.user_languages = Counter(dict.fromkeys(("zh", "en", "mixed", "other", "none"), 0))
        self.turns = Counter(dict.fromkeys(("0", "1", "2", "3–5", "6+"), 0))
        self.lengths = Counter(dict.fromkeys((label for _, label in CHAR_BINS), 0))
        self.reasoning_lengths = Counter(dict.fromkeys((label for _, label in CHAR_BINS), 0))

    def add(self, target, row):
        body, users, reasoning, turns, image, tools = record_features(target, row)
        size, thought_size = sum(map(len, body)), sum(map(len, reasoning))
        self.rows += 1
        self.body_chars += size
        self.reasoning_chars += thought_size
        self.reasoning_rows += thought_size > 0
        self.images += image
        self.tools += tools
        self.languages[language(body)] += 1
        self.user_languages[language(users) if users else "none"] += 1
        self.turns[str(turns) if turns < 3 else "3–5" if turns < 6 else "6+"] += 1
        self.lengths[char_bin(size)] += 1
        if thought_size:
            self.reasoning_lengths[char_bin(thought_size)] += 1

    def result(self):
        return {"rows": self.rows, "body_chars": self.body_chars, "reasoning_chars": self.reasoning_chars,
                "reasoning_rows": self.reasoning_rows, "image_rows": self.images, "tool_rows": self.tools,
                **{name: dict(getattr(self, name)) for name in
                   ("languages", "user_languages", "turns", "lengths", "reasoning_lengths")}}

    def merge(self, other):
        for name in ("rows", "body_chars", "reasoning_chars", "reasoning_rows", "images", "tools"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for name in ("languages", "user_languages", "turns", "lengths", "reasoning_lengths"):
            getattr(self, name).update(getattr(other, name))
